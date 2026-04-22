#!/usr/bin/env python3
"""
Audiobook Generator — Web UI
Run: python3 app.py
Open: http://localhost:8081
"""
from __future__ import annotations
import io
import json
import os
import re
import queue
import threading
import time
import zipfile
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from models import Book, Chapter, Preset, Setting, db
from parsers import pdf_parser, epub_parser, docx_parser

BASE_DIR = Path(__file__).parent
_default_data_dir = Path.home() / "Library" / "Application Support" / "AudiobookGenerator"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_default_data_dir)))
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

# Database: use DATABASE_URL env var (PostgreSQL) or fall back to local SQLite
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url:
    if _db_url.startswith("postgres://"):  # Supabase/Heroku use postgres://, SQLAlchemy needs postgresql://
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATA_DIR / 'audiobook.db'}"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

db.init_app(app)

# Per-book SSE event queues: {book_id: queue.Queue}
_sse_queues: dict[int, queue.Queue] = {}
_sse_lock = threading.Lock()


def get_sse_queue(book_id: int) -> queue.Queue:
    with _sse_lock:
        if book_id not in _sse_queues:
            _sse_queues[book_id] = queue.Queue(maxsize=500)
        return _sse_queues[book_id]


def push_event(book_id: int, event_type: str, data: dict):
    q = get_sse_queue(book_id)
    try:
        q.put_nowait({"type": event_type, "data": data})
    except queue.Full:
        pass


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_book():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = Path(f.filename).suffix.lower().lstrip(".")

    # If no recognised extension, detect by magic bytes
    if ext not in ("pdf", "epub", "docx"):
        header = f.stream.read(8)
        f.stream.seek(0)
        if header[:4] == b"%PDF":
            ext = "pdf"
        elif header[:4] == b"PK\x03\x04":
            # ZIP-based: could be DOCX or EPUB — peek inside
            import zipfile, io as _io
            raw = f.stream.read()
            f.stream.seek(0)
            try:
                zf = zipfile.ZipFile(_io.BytesIO(raw))
                names = zf.namelist()
                if any("mimetype" in n for n in names):
                    ext = "epub"
                elif any("word/" in n for n in names):
                    ext = "docx"
                else:
                    ext = "docx"  # best guess
            except Exception:
                ext = "docx"
        else:
            return jsonify({"error": "Unsupported file type. Upload a PDF, EPUB, or DOCX."}), 400

    save_path = UPLOAD_DIR / (Path(f.filename).stem + "." + ext)
    stem = Path(f.filename).stem
    counter = 1
    while save_path.exists():
        save_path = UPLOAD_DIR / f"{stem}_{counter}.{ext}"
        counter += 1

    f.save(save_path)

    book = Book(
        original_filename=f.filename,
        title=Path(f.filename).stem.replace("_", " ").replace("-", " ").title(),
        file_type=ext,
        status="uploaded",
    )
    db.session.add(book)
    db.session.commit()

    # Parse in background
    thread = threading.Thread(target=_parse_book, args=(app, book.id, save_path, ext), daemon=True)
    thread.start()

    return jsonify({"book_id": book.id, "status": "parsing"})


def _parse_book(flask_app, book_id: int, filepath: Path, ext: str):
    with flask_app.app_context():
        book = db.session.get(Book, book_id)
        try:
            if ext == "pdf":
                raw_chapters = pdf_parser.parse(str(filepath))
            elif ext == "epub":
                raw_chapters = epub_parser.parse(str(filepath))
            elif ext == "docx":
                raw_chapters = docx_parser.parse(str(filepath))
            else:
                raise ValueError(f"Unknown extension: {ext}")

            for i, ch in enumerate(raw_chapters, start=1):
                chapter = Chapter(
                    book_id=book_id,
                    chapter_number=i,
                    title=ch["title"],
                    content=ch["content"],
                    char_count=len(ch["content"]),
                )
                db.session.add(chapter)

            book.status = "parsed"
            db.session.commit()
            push_event(book_id, "parsed", {"chapter_count": len(raw_chapters)})

        except Exception as e:
            book.status = "error"
            db.session.commit()
            push_event(book_id, "error", {"message": str(e)})


@app.route("/api/books/<int:book_id>")
def get_book(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404
    return jsonify(book.to_dict())


@app.route("/api/books/<int:book_id>/chapters")
def get_chapters(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404
    return jsonify([ch.to_dict() for ch in book.chapters])


@app.route("/api/books/<int:book_id>/chapters", methods=["PUT"])
def update_chapters(book_id):
    """Replace the chapter list with user-edited version."""
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "Expected a list of chapters"}), 400

    # Keep a map of existing content keyed by chapter id so we can fall back to it
    existing_content = {
        ch.id: ch.content
        for ch in Chapter.query.filter_by(book_id=book_id).all()
    }

    Chapter.query.filter_by(book_id=book_id).delete()

    for i, ch in enumerate(data, start=1):
        # Prefer content sent by client; fall back to whatever was in the DB
        content = ch.get("content") or existing_content.get(ch.get("id"), "") or ""
        chapter = Chapter(
            book_id=book_id,
            chapter_number=i,
            title=ch.get("title", f"Chapter {i}"),
            content=content,
            char_count=len(content),
        )
        db.session.add(chapter)

    book.status = "confirmed"
    db.session.commit()
    return jsonify({"ok": True, "chapter_count": len(data)})


def _parse_tts_params(params: dict):
    def _f(key, default):
        v = params.get(key)
        return float(v) if v is not None else default
    return {
        "api_key": (params.get("api_key") or "").strip(),
        "voice_id": params.get("voice_id") or "",
        "model_id": params.get("model_id") or "eleven_multilingual_v2",
        "stability": _f("stability", 0.5),
        "similarity_boost": _f("similarity_boost", 0.75),
        "style": _f("style", 0.0),
        "speed": _f("speed", 1.0),
    }


@app.route("/api/chapters/<int:chapter_id>/generate", methods=["POST"])
def generate_chapter(chapter_id):
    """Generate audio for a single chapter."""
    chapter = db.session.get(Chapter, chapter_id)
    if not chapter:
        return jsonify({"error": "Not found"}), 404
    if chapter.status == "generating":
        return jsonify({"error": "Already generating"}), 400

    params = _parse_tts_params(request.get_json() or {})
    if not params["api_key"]:
        return jsonify({"error": "ElevenLabs API key required"}), 400

    thread = threading.Thread(
        target=_generate_chapters_list,
        args=(app, chapter.book_id, [chapter_id], params),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/books/<int:book_id>/generate", methods=["POST"])
def generate_audio(book_id):
    """Generate audio for all pending/error chapters."""
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404

    params = _parse_tts_params(request.get_json() or {})
    if not params["api_key"]:
        return jsonify({"error": "ElevenLabs API key required"}), 400

    pending_ids = [
        ch.id for ch in book.chapters if ch.status in ("pending", "error")
    ]
    if not pending_ids:
        return jsonify({"error": "No pending chapters to generate"}), 400

    book.status = "generating"
    db.session.commit()

    thread = threading.Thread(
        target=_generate_chapters_list,
        args=(app, book_id, pending_ids, params),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "queued": len(pending_ids)})


def _generate_chapters_list(flask_app, book_id: int, chapter_ids: list, params: dict):
    with flask_app.app_context():
        from elevenlabs import ElevenLabs, VoiceSettings

        client = ElevenLabs(api_key=params["api_key"])
        total = len(chapter_ids)

        for idx, chapter_id in enumerate(chapter_ids):
            chapter = db.session.get(Chapter, chapter_id)
            if not chapter:
                continue

            chapter.status = "generating"
            chapter.voice_id = params["voice_id"]
            chapter.model_id = params["model_id"]
            db.session.commit()

            push_event(book_id, "chapter_start", {
                "chapter_id": chapter.id,
                "chapter_number": chapter.chapter_number,
                "title": chapter.title,
                "index": idx,
                "total": total,
            })

            try:
                voice_settings = VoiceSettings(
                    stability=params["stability"],
                    similarity_boost=params["similarity_boost"],
                    style=params["style"],
                    use_speaker_boost=True,
                    speed=params["speed"],
                )

                audio_chunks = []
                for chunk_text in _chunk_text(chapter.content, max_chars=4800):
                    audio = client.text_to_speech.convert(
                        text=chunk_text,
                        voice_id=params["voice_id"],
                        model_id=params["model_id"],
                        voice_settings=voice_settings,
                        output_format="mp3_44100_192",
                    )
                    audio_chunks.append(b"".join(audio))

                combined = b"".join(audio_chunks)

                out_dir = OUTPUT_DIR / str(book_id)
                out_dir.mkdir(parents=True, exist_ok=True)
                safe_title = _safe_filename(chapter.title)
                out_path = out_dir / f"{chapter.chapter_number:03d}_{safe_title}.mp3"
                out_path.write_bytes(combined)

                acx_stats = _normalize_acx(out_path)

                chapter.status = "done"
                chapter.output_path = str(out_path)
                chapter.error_msg = None
                if acx_stats:
                    chapter.acx_rms_db = acx_stats["rms_db"]
                    chapter.acx_peak_db = acx_stats["peak_db"]
                db.session.commit()

                push_event(book_id, "chapter_done", {
                    "chapter_id": chapter.id,
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title,
                    "index": idx,
                    "total": total,
                    "acx": acx_stats,
                })

            except Exception as e:
                chapter.status = "error"
                chapter.error_msg = str(e)
                db.session.commit()

                push_event(book_id, "chapter_error", {
                    "chapter_id": chapter.id,
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title,
                    "error": str(e),
                    "index": idx,
                    "total": total,
                })

        # Refresh book status
        book = db.session.get(Book, book_id)
        chapters = book.chapters
        if all(ch.status == "done" for ch in chapters):
            book.status = "done"
        elif any(ch.status == "done" for ch in chapters):
            book.status = "generating"  # partial
        db.session.commit()

        push_event(book_id, "batch_complete", {
            "book_id": book_id,
            "all_done": all(ch.status == "done" for ch in chapters),
        })


def _chunk_text(text: str, max_chars: int = 4800) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_chars:
            chunks.append(text)
            break
        # Find last sentence boundary before max_chars
        boundary = max_chars
        for sep in (".\n", ". ", ".\t", "!", "?"):
            pos = text.rfind(sep, 0, max_chars)
            if pos > max_chars // 2:
                boundary = pos + len(sep)
                break
        chunks.append(text[:boundary])
        text = text[boundary:]

    return [c for c in chunks if c.strip()]


def _normalize_acx(path: Path) -> dict | None:
    """Normalize an MP3 to ACX standards: -20 dB RMS target, peak ≤ -3 dBFS, 192 kbps 44.1 kHz.
    Returns {"rms_db": float, "peak_db": float} or None on failure."""
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_mp3(str(path))

        # Guard: silent or near-silent audio — skip normalization
        if audio.dBFS < -60:
            return {"rms_db": round(audio.dBFS, 1), "peak_db": round(audio.max_dBFS, 1)}

        TARGET_RMS_DB = -20.0
        PEAK_LIMIT_DB = -3.0

        gain_db = TARGET_RMS_DB - audio.dBFS
        normalized = audio.apply_gain(gain_db)

        # Back off gain if peak would clip above -3 dBFS
        if normalized.max_dBFS > PEAK_LIMIT_DB:
            gain_db -= (normalized.max_dBFS - PEAK_LIMIT_DB)
            normalized = audio.apply_gain(gain_db)

        normalized.export(
            str(path),
            format="mp3",
            bitrate="192k",
            parameters=["-ar", "44100"],
        )

        return {"rms_db": round(normalized.dBFS, 1), "peak_db": round(normalized.max_dBFS, 1)}

    except Exception as e:
        print(f"[ACX normalize] warning: {e}")
        return None


def _safe_filename(name: str) -> str:
    import re
    name = re.sub(r'[^\w\s\-]', '', name)
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80] or "chapter"


@app.route("/api/books/<int:book_id>/events")
def sse_events(book_id):
    """Server-Sent Events stream for real-time progress."""
    def generate():
        q = get_sse_queue(book_id)
        # Send current state immediately
        with app.app_context():
            book = db.session.get(Book, book_id)
            if book:
                yield f"data: {json.dumps({'type': 'state', 'data': book.to_dict()})}\n\n"

        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("generation_complete", "error"):
                    break
            except queue.Empty:
                yield ": heartbeat\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/books/<int:book_id>/test-voice", methods=["POST"])
def test_voice(book_id):
    """Generate audio for the first paragraph of the first chapter as a quick preview."""
    book = db.session.get(Book, book_id)
    if not book or not book.chapters:
        return jsonify({"error": "No chapters found"}), 404

    first_chapter = book.chapters[0]
    content = first_chapter.content or ""
    paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
    if not paragraphs:
        return jsonify({"error": "First chapter has no content"}), 400

    test_text = paragraphs[0][:600]  # cap at 600 chars

    params = _parse_tts_params(request.get_json() or {})
    if not params["api_key"]:
        return jsonify({"error": "API key required"}), 400
    if not params["voice_id"]:
        return jsonify({"error": "Voice ID required"}), 400

    try:
        from elevenlabs import ElevenLabs, VoiceSettings
        client = ElevenLabs(api_key=params["api_key"])
        voice_settings = VoiceSettings(
            stability=params["stability"],
            similarity_boost=params["similarity_boost"],
            style=params["style"],
            use_speaker_boost=True,
            speed=params["speed"],
        )
        audio = client.text_to_speech.convert(
            text=test_text,
            voice_id=params["voice_id"],
            model_id=params["model_id"],
            voice_settings=voice_settings,
            output_format="mp3_44100_192",
        )
        audio_bytes = b"".join(audio)

        # Normalize to ACX standard via temp file
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            tmp_path.write_bytes(audio_bytes)
            _normalize_acx(tmp_path)
            audio_bytes = tmp_path.read_bytes()
        finally:
            try: _os.unlink(tmp_path)
            except Exception: pass

        return send_file(io.BytesIO(audio_bytes), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/chapters/<int:chapter_id>/download")
def download_chapter(chapter_id):
    chapter = db.session.get(Chapter, chapter_id)
    if not chapter or not chapter.output_path:
        return jsonify({"error": "Audio not found"}), 404
    path = Path(chapter.output_path)
    if not path.exists():
        return jsonify({"error": "File missing on disk"}), 404
    safe_title = re.sub(r'[^\w\s\-]', '', chapter.title or f"chapter_{chapter.chapter_number}").strip()
    safe_title = re.sub(r'\s+', '_', safe_title) or f"chapter_{chapter.chapter_number}"
    download_name = f"{safe_title}.mp3"
    return send_file(path, mimetype="audio/mpeg", as_attachment=True,
                     download_name=download_name)


@app.route("/api/books/<int:book_id>/download-all")
def download_all(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404

    done_chapters = [ch for ch in book.chapters if ch.output_path and Path(ch.output_path).exists()]
    if not done_chapters:
        return jsonify({"error": "No audio files available yet"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ch in done_chapters:
            p = Path(ch.output_path)
            safe_ch = re.sub(r'[^\w\s\-]', '', ch.title or f"chapter_{ch.chapter_number}").strip()
            safe_ch = re.sub(r'\s+', '_', safe_ch) or f"chapter_{ch.chapter_number}"
            zf.write(p, f"{safe_ch}.mp3")
    buf.seek(0)

    safe_title = _safe_filename(book.title or book.original_filename)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{safe_title}_audiobook.zip")


@app.route("/api/voices")
def get_voices():
    """Proxy ElevenLabs voices list (requires api_key query param)."""
    api_key = request.args.get("api_key", "").strip()
    if not api_key:
        return jsonify({"error": "api_key required"}), 400
    try:
        from elevenlabs import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        voices = client.voices.get_all()
        return jsonify([
            {"voice_id": v.voice_id, "name": v.name, "category": v.category}
            for v in voices.voices
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/models")
def get_models():
    """Proxy ElevenLabs models list (requires api_key query param)."""
    api_key = request.args.get("api_key", "").strip()
    if not api_key:
        return jsonify({"error": "api_key required"}), 400
    try:
        from elevenlabs import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        models = client.models.get_all()
        return jsonify([
            {"model_id": m.model_id, "name": m.name}
            for m in models
            if getattr(m, "can_do_text_to_speech", None) is not False
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/books")
def list_books():
    books = Book.query.order_by(Book.created_at.desc()).all()
    return jsonify([b.to_dict() for b in books])


@app.route("/api/books/<int:book_id>", methods=["DELETE"])
def delete_book(book_id):
    book = db.session.get(Book, book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404
    import shutil
    out_dir = OUTPUT_DIR / str(book_id)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    db.session.delete(book)
    db.session.commit()
    return jsonify({"ok": True})


# ─── Presets ─────────────────────────────────────────────────────────────────

@app.route("/api/presets")
def list_presets():
    presets = Preset.query.order_by(Preset.name).all()
    return jsonify([p.to_dict() for p in presets])


@app.route("/api/presets", methods=["POST"])
def save_preset():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Preset name required"}), 400
    preset = Preset.query.filter_by(name=name).first()
    if not preset:
        preset = Preset(name=name)
        db.session.add(preset)
    preset.api_key = data.get("api_key", "")
    preset.voice_id = data.get("voice_id", "")
    preset.voice_name = data.get("voice_name", "")
    preset.model_id = data.get("model_id", "eleven_multilingual_v2")
    preset.speed = float(data.get("speed", 1.0))
    preset.stability = float(data.get("stability", 0.5))
    preset.similarity_boost = float(data.get("similarity_boost", 0.75))
    preset.style = float(data.get("style", 0.0))
    db.session.commit()
    return jsonify(preset.to_dict())


@app.route("/api/presets/<int:preset_id>", methods=["DELETE"])
def delete_preset(preset_id):
    preset = db.session.get(Preset, preset_id)
    if not preset:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(preset)
    db.session.commit()
    return jsonify({"ok": True})


# ─── Settings ────────────────────────────────────────────────────────────────

@app.route("/api/settings")
def get_settings():
    rows = Setting.query.all()
    return jsonify({r.key: r.value for r in rows})


@app.route("/api/settings", methods=["PUT"])
def save_settings():
    data = request.get_json() or {}
    for key, value in data.items():
        row = db.session.get(Setting, key)
        if row:
            row.value = str(value)
        else:
            db.session.add(Setting(key=key, value=str(value)))
    db.session.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=8081, threaded=True)
