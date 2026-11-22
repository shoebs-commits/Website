"""SQLite helpers for PDFKit. Stores operation metadata, never document bytes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "pdfkit.db"


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_type TEXT NOT NULL,
                input_filenames TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('processing', 'success', 'failed')),
                created_at TEXT NOT NULL,
                error_message TEXT,
                output_filename TEXT
            )
        """)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_operations_created_at "
            "ON operations(created_at DESC)"
        )


def create_operation(operation_type: str, input_filenames: list[str]) -> int:
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as connection:
        cursor = connection.execute(
            """INSERT INTO operations
               (operation_type, input_filenames, status, created_at)
               VALUES (?, ?, 'processing', ?)""",
            (operation_type, json.dumps(input_filenames), created_at),
        )
        return int(cursor.lastrowid)


def update_operation(
    operation_id: int,
    status: str,
    *,
    error_message: str | None = None,
    output_filename: str | None = None,
) -> None:
    if status not in {"processing", "success", "failed"}:
        raise ValueError("Unsupported operation status")
    with get_connection() as connection:
        connection.execute(
            """UPDATE operations
               SET status = ?, error_message = ?, output_filename = ?
               WHERE id = ?""",
            (status, error_message, output_filename, operation_id),
        )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["input_filenames"] = json.loads(item["input_filenames"])
    except (TypeError, json.JSONDecodeError):
        item["input_filenames"] = []
    return item


def get_operation(operation_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_operations(limit: int = 50) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT * FROM operations
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (safe_limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]
