"""One lazy SQLite store for civic reports, community posts and support.

User media is a bounded sanitised image BLOB, committed atomically with its
record. This prevents orphan files and partial successful uploads.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
    location_text TEXT NOT NULL, latitude REAL, longitude REAL,
    location_confirmed INTEGER NOT NULL, public INTEGER NOT NULL,
    photo_public INTEGER NOT NULL, media_id TEXT,
    status TEXT NOT NULL CHECK(status='reported'), created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, UNIQUE(owner,idempotency_key)
);
CREATE INDEX IF NOT EXISTS reports_public_position ON reports(public,latitude,longitude);
CREATE INDEX IF NOT EXISTS reports_owner ON reports(owner,created_at);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT NOT NULL REFERENCES reports(id),
    status TEXT NOT NULL, created_at TEXT NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS support (
    report_id TEXT NOT NULL REFERENCES reports(id), supporter TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY(report_id,supporter)
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL, text TEXT NOT NULL, location_text TEXT NOT NULL,
    report_id TEXT REFERENCES reports(id), public INTEGER NOT NULL,
    photo_public INTEGER NOT NULL, media_id TEXT, created_at TEXT NOT NULL,
    UNIQUE(owner,idempotency_key)
);
CREATE INDEX IF NOT EXISTS posts_public ON posts(public,created_at);
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, record_type TEXT NOT NULL,
    record_id TEXT NOT NULL, content BLOB NOT NULL,
    content_type TEXT NOT NULL CHECK(content_type='image/jpeg')
);
PRAGMA user_version=1;
"""


class CivicStore:
    def __init__(self, path=None):
        data = Path(os.getenv('SMART_NAGRIK_DATA_DIR', Path(__file__).parent / 'data'))
        self.path = Path(path) if path is not None else data / 'civic.sqlite3'

    @contextmanager
    def connection(self, write=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=3000')
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise sqlite3.DatabaseError('Unsupported civic schema version')
            if version == 0:
                db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()
