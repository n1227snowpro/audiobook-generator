# Audiobook Generator

A local web app that converts **PDF, EPUB, and DOCX** books into narrated MP3 audio chapters using the [ElevenLabs](https://elevenlabs.io) text-to-speech API.

![Python](https://img.shields.io/badge/Python-3.8+-blue) ![Flask](https://img.shields.io/badge/Flask-3.0+-lightgrey) ![ElevenLabs](https://img.shields.io/badge/ElevenLabs-TTS-purple)

---

## Features

- Upload PDF, EPUB, or DOCX files (including files without extensions)
- Automatic chapter detection — reads TOC, heading styles, and custom style names
- Review, rename, and delete chapters before generating
- ElevenLabs voice & model selection with named presets
- Test voice button — previews the first paragraph before committing
- Cost calculator — estimates API cost per chapter and total based on model pricing
- Generate chapters one by one or all at once
- Real-time progress via Server-Sent Events
- Download individual MP3s or all chapters as a ZIP
- All data stored safely in `~/Library/Application Support/AudiobookGenerator/`

---

## Requirements

- macOS (tested on macOS 14+)
- Python 3.8 or higher
- An [ElevenLabs API key](https://elevenlabs.io)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/n1227snowpro/audiobook-generator.git
cd audiobook-generator
```

### 2. Install dependencies

```bash
pip3 install -r requirements.txt
```

This installs:

| Package | Purpose |
|---|---|
| Flask + Flask-SQLAlchemy | Web server and database |
| PyMuPDF | PDF parsing |
| ebooklib + beautifulsoup4 | EPUB parsing |
| python-docx | DOCX parsing |
| elevenlabs | ElevenLabs TTS API |
| lxml | HTML/XML processing |

### 3. Run the app

```bash
python3 app.py
```

Then open **http://localhost:8081** in your browser.

---

## Usage

### Step 1 — Upload
Drag and drop (or browse for) a PDF, EPUB, or DOCX file. The app will automatically detect chapters in the background.

### Step 2 — Review Chapters
Check the detected chapters. You can rename or delete any chapter before proceeding. Chapter content is preserved even after edits.

### Step 3 — Voice Settings
Enter your ElevenLabs API key and click **Load Voices** to fetch your available voices and models. Configure:

| Setting | Description |
|---|---|
| Voice | The ElevenLabs voice to use |
| Model | TTS model (v3 recommended for quality, Flash for speed) |
| Speed | Narration speed (0.7× – 1.2×) |
| Stability | How consistent the voice sounds (0–1) |
| Similarity Boost | How closely it matches the voice profile (0–1) |
| Style | Expressiveness (0–1, not supported by all models) |

Save your settings as a **named preset** to reload them instantly next time.

### Step 4 — Generate
- Click **Generate Test** to hear the first paragraph before committing
- Check the **Cost Estimate** panel — it shows cost per chapter and total based on model pricing
- Click **Generate** on individual chapters, or **⚡ Generate All Remaining** to process everything
- Progress updates in real time

### Step 5 — Download
Download individual chapter MP3s or click **Download All (ZIP)** to get everything in one file.

---

## Data Storage

All user data is stored outside the project folder so it is never accidentally deleted:

```
~/Library/Application Support/AudiobookGenerator/
├── uploads/       # Uploaded book files
├── output/        # Generated MP3 files (organised by book ID)
└── audiobook.db   # SQLite database
```

---

## ElevenLabs Model Pricing (approximate)

| Model | Cost per 1,000 characters |
|---|---|
| eleven_v3 | $0.40 |
| eleven_multilingual_v2 | $0.30 |
| eleven_turbo_v2_5 | $0.15 |
| eleven_flash_v2_5 | $0.08 |

Prices shown are estimates. Check [ElevenLabs pricing](https://elevenlabs.io/pricing) for current rates. The cost calculator in Step 4 uses these defaults but lets you enter a custom rate.

---

## Troubleshooting

**Chapters not detected correctly**
The parser tries TOC/bookmarks first, then heading styles, then pattern matching. For best results, use a well-structured file with proper heading styles.

**ElevenLabs API error**
Double-check your API key and make sure your account has sufficient character credits.

**Port already in use**
Edit the last line of `app.py` and change the port number:
```python
app.run(debug=True, port=8081, threaded=True)
```
