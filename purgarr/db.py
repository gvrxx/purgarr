"""SQLite storage for deletion requests."""

import os
import sqlite3
import time
from dataclasses import dataclass

from .arr import MediaItem

# Statuses: pending -> deleting -> deleted | gone, or pending -> declined.
OPEN_STATUSES = ("pending", "deleting")

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    arr_id INTEGER NOT NULL,
    external_id INTEGER,
    title TEXT NOT NULL,
    year INTEGER,
    poster_url TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    link TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    resolved_at INTEGER,
    resolved_by INTEGER,
    admin_channel_id INTEGER,
    admin_message_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_request_per_item
    ON requests (service, arr_id) WHERE status IN ('pending', 'deleting');
CREATE TABLE IF NOT EXISTS requesters (
    request_id INTEGER NOT NULL REFERENCES requests (id),
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (request_id, user_id)
);
"""


@dataclass(frozen=True)
class Request:
    id: int
    service: str
    arr_id: int
    external_id: int | None
    title: str
    year: int | None
    poster_url: str | None
    size_bytes: int
    detail: str
    link: str | None
    status: str
    created_at: int
    resolved_at: int | None
    resolved_by: int | None
    admin_channel_id: int | None
    admin_message_id: int | None

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(frozen=True)
class Requester:
    user_id: int
    channel_id: int


class Database:
    def __init__(self, path: str):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._conn:
            self._conn.executescript(SCHEMA)
            # A crash mid-delete leaves 'deleting' behind; approving again re-checks whether the item still exists.
            self._conn.execute("UPDATE requests SET status = 'pending' WHERE status = 'deleting'")

    def close(self) -> None:
        self._conn.close()

    def add_request(self, item: MediaItem, user_id: int, channel_id: int) -> tuple[Request, str]:
        """Records a request. Returns the request and 'created', 'joined' (someone else already asked) or 'duplicate'."""
        now = int(time.time())
        with self._conn:
            row = self._conn.execute(
                "SELECT id FROM requests WHERE service = ? AND arr_id = ? AND status IN (?, ?)",
                (item.service, item.arr_id, *OPEN_STATUSES),
            ).fetchone()
            if row is None:
                cursor = self._conn.execute(
                    "INSERT INTO requests (service, arr_id, external_id, title, year, poster_url, size_bytes,"
                    " detail, link, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item.service, item.arr_id, item.external_id, item.title, item.year, item.poster_url,
                     item.size_bytes, item.detail, item.link, now),
                )
                request_id, outcome = cursor.lastrowid, "created"
            else:
                request_id, outcome = row["id"], "joined"

            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO requesters (request_id, user_id, channel_id, created_at) VALUES (?, ?, ?, ?)",
                (request_id, user_id, channel_id, now),
            )
            if cursor.rowcount == 0:
                outcome = "duplicate"
        return self.get(request_id), outcome

    def get(self, request_id: int) -> Request | None:
        row = self._conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        return Request(**row) if row else None

    def requesters(self, request_id: int) -> list[Requester]:
        rows = self._conn.execute(
            "SELECT user_id, channel_id FROM requesters WHERE request_id = ? ORDER BY created_at", (request_id,)
        ).fetchall()
        return [Requester(**row) for row in rows]

    def pending(self, limit: int) -> list[Request]:
        rows = self._conn.execute(
            "SELECT * FROM requests WHERE status IN (?, ?) ORDER BY created_at LIMIT ?", (*OPEN_STATUSES, limit)
        ).fetchall()
        return [Request(**row) for row in rows]

    def count_pending(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM requests WHERE status IN (?, ?)", OPEN_STATUSES).fetchone()[0]

    def by_user(self, user_id: int, limit: int = 10) -> list[Request]:
        rows = self._conn.execute(
            "SELECT r.* FROM requests r JOIN requesters q ON q.request_id = r.id"
            " WHERE q.user_id = ? ORDER BY q.created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [Request(**row) for row in rows]

    def set_admin_message(self, request_id: int, channel_id: int, message_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE requests SET admin_channel_id = ?, admin_message_id = ? WHERE id = ?",
                (channel_id, message_id, request_id),
            )

    def _transition(self, request_id: int, from_status: str, to_status: str, admin_id: int | None = None) -> bool:
        resolved = to_status not in OPEN_STATUSES
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE requests SET status = ?, resolved_at = ?, resolved_by = ? WHERE id = ? AND status = ?",
                (to_status, int(time.time()) if resolved else None, admin_id if resolved else None,
                 request_id, from_status),
            )
        return cursor.rowcount == 1

    def claim_for_deletion(self, request_id: int) -> bool:
        """pending -> deleting. False if another click already handled it."""
        return self._transition(request_id, "pending", "deleting")

    def release(self, request_id: int) -> None:
        """deleting -> pending, after a failed delete so it can be retried."""
        self._transition(request_id, "deleting", "pending")

    def finish_deletion(self, request_id: int, admin_id: int, gone: bool = False) -> bool:
        return self._transition(request_id, "deleting", "gone" if gone else "deleted", admin_id)

    def decline(self, request_id: int, admin_id: int) -> bool:
        return self._transition(request_id, "pending", "declined", admin_id)
