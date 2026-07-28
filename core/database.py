"""
SQLite access layer.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional

from config import DATABASE_PATH
from core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Template:
    id: Optional[int]
    name: str
    category: str
    body: str
    created_at: str = ""


@dataclass
class DocumentRecord:
    id: Optional[int]
    template_id: int
    title: str
    file_path: str
    created_at: str = ""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Database operation failed; rolled back.")
        raise
    finally:
        conn.close()


def initialize_database() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS templates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                category    TEXT NOT NULL,
                body        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id  INTEGER NOT NULL,
                title        TEXT NOT NULL,
                file_path    TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                FOREIGN KEY (template_id) REFERENCES templates(id)
            );
            """
        )
    logger.info("Database ready at %s", DATABASE_PATH)


def add_template(name: str, category: str, body: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO templates (name, category, body, created_at) VALUES (?, ?, ?, ?)",
            (name, category, body, datetime.now().isoformat(timespec="seconds")),
        )
        template_id = cur.lastrowid
    logger.info("Template added: id=%s name=%s", template_id, name)
    return template_id


def update_template(template_id: int, name: str, category: str, body: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE templates SET name = ?, category = ?, body = ? WHERE id = ?",
            (name, category, body, template_id),
        )
    logger.info("Template updated: id=%s", template_id)


def delete_template(template_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    logger.info("Template deleted: id=%s", template_id)


def get_template(template_id: int) -> Optional[Template]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    return Template(**dict(row)) if row else None


def list_templates(category: Optional[str] = None) -> list[Template]:
    with _connect() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM templates WHERE category = ? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM templates ORDER BY category, name").fetchall()
    return [Template(**dict(r)) for r in rows]


def add_document_record(template_id: int, title: str, file_path: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO documents (template_id, title, file_path, created_at) VALUES (?, ?, ?, ?)",
            (template_id, title, file_path, datetime.now().isoformat(timespec="seconds")),
        )
        doc_id = cur.lastrowid
    logger.info("Document record added: id=%s title=%s", doc_id, title)
    return doc_id


def seed_default_templates() -> None:
    if list_templates():
        return

    starters = [
        (
            "নোটিশ - সাধারণ",
            "নোটিশ",
            "নোটিশ\n\nবিষয়: {বিষয়}\n\nসংশ্লিষ্ট সকলের অবগতির জন্য জানানো যাচ্ছে যে, {বার্তা}\n\n"
            "উপরোক্ত বিষয়ে সংশ্লিষ্ট সকলকে যথাযথ ব্যবস্থা গ্রহণের জন্য অনুরোধ করা হলো।\n\n"
            "তারিখ: {তারিখ}",
        ),
        (
            "অফিস আদেশ - সাধারণ",
            "অফিস আদেশ",
            "অফিস আদেশ\n\nস্মারক নং: {স্মারক_নং}\nতারিখ: {তারিখ}\n\nবিষয়: {বিষয়}\n\n"
            "{বার্তা}\n\nএই আদেশ অবিলম্বে কার্যকর হবে।",
        ),
        (
            "স্মারক - সাধারণ",
            "স্মারক",
            "স্মারক নং: {স্মারক_নং}\nতারিখ: {তারিখ}\n\nবরাবর,\n{প্রাপক}\n\nবিষয়: {বিষয়}\n\n{বার্তা}",
        ),
    ]
    for name, category, body in starters:
        add_template(name, category, body)
    logger.info("Seeded %d starter templates.", len(starters))


def list_documents(limit: int = 100) -> list[DocumentRecord]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [DocumentRecord(**dict(r)) for r in rows]
