from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Book(db.Model):
    __tablename__ = "books"

    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(500), nullable=False)
    title = db.Column(db.String(500))
    file_type = db.Column(db.String(10))  # pdf, epub, docx
    status = db.Column(db.String(50), default="uploaded")
    # uploaded → parsed → confirmed → generating → done | error
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    chapters = db.relationship(
        "Chapter", back_populates="book", order_by="Chapter.chapter_number", cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "id": self.id,
            "original_filename": self.original_filename,
            "title": self.title,
            "file_type": self.file_type,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "chapter_count": len(self.chapters),
        }


class Chapter(db.Model):
    __tablename__ = "chapters"

    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    chapter_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(500))
    content = db.Column(db.Text)
    char_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default="pending")
    # pending → generating → done | error
    output_path = db.Column(db.String(1000))
    error_msg = db.Column(db.Text)
    voice_id = db.Column(db.String(200))
    model_id = db.Column(db.String(200))
    acx_rms_db = db.Column(db.Float, nullable=True)
    acx_peak_db = db.Column(db.Float, nullable=True)
    book = db.relationship("Book", back_populates="chapters")

    def to_dict(self):
        return {
            "id": self.id,
            "book_id": self.book_id,
            "chapter_number": self.chapter_number,
            "title": self.title,
            "content": self.content or "",
            "char_count": self.char_count,
            "status": self.status,
            "has_audio": bool(self.output_path),
            "error_msg": self.error_msg,
            "acx_rms_db": self.acx_rms_db,
            "acx_peak_db": self.acx_peak_db,
        }
