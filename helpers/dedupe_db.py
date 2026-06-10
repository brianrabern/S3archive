import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    s3_key TEXT UNIQUE NOT NULL,
    size INTEGER,
    etag TEXT,
    hash TEXT,
    hash_method TEXT,
    last_modified TEXT,
    inventoried_at TEXT,
    status TEXT DEFAULT 'inventoried'
);
CREATE INDEX IF NOT EXISTS idx_objects_hash ON objects(hash);
CREATE INDEX IF NOT EXISTS idx_objects_status ON objects(status);
"""


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path):
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


@contextmanager
def db_session(db_path):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_inventoried_keys(conn):
    rows = conn.execute("SELECT s3_key FROM objects").fetchall()
    return {row["s3_key"] for row in rows}


def upsert_object(conn, s3_key, size, etag, content_hash, hash_method, last_modified):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO objects (s3_key, size, etag, hash, hash_method, last_modified, inventoried_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(s3_key) DO UPDATE SET
            size = excluded.size,
            etag = excluded.etag,
            hash = excluded.hash,
            hash_method = excluded.hash_method,
            last_modified = excluded.last_modified,
            inventoried_at = excluded.inventoried_at
        """,
        (s3_key, size, etag, content_hash, hash_method, last_modified, now),
    )


def get_duplicate_groups(conn):
    return conn.execute(
        """
        SELECT hash, COUNT(*) AS key_count, GROUP_CONCAT(s3_key, char(28)) AS keys_blob
        FROM objects
        WHERE hash IS NOT NULL AND status != 'deleted'
        GROUP BY hash
        HAVING COUNT(*) > 1
        ORDER BY key_count DESC, hash
        """
    ).fetchall()


def get_objects_by_hash(conn, content_hash):
    return conn.execute(
        """
        SELECT s3_key, size, etag, hash_method, last_modified, status
        FROM objects
        WHERE hash = ? AND status != 'deleted'
        ORDER BY s3_key
        """,
        (content_hash,),
    ).fetchall()


def mark_deleted(conn, s3_keys):
    conn.executemany(
        "UPDATE objects SET status = 'deleted' WHERE s3_key = ?",
        [(key,) for key in s3_keys],
    )


def mark_canonical_and_duplicates(conn, canonical_key, duplicate_keys):
    conn.execute(
        "UPDATE objects SET status = 'canonical' WHERE s3_key = ?",
        (canonical_key,),
    )
    conn.executemany(
        "UPDATE objects SET status = 'duplicate' WHERE s3_key = ?",
        [(key,) for key in duplicate_keys],
    )


def get_stats(conn):
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN hash IS NULL THEN 1 ELSE 0 END) AS missing_hash,
            SUM(CASE WHEN hash_method = 'etag' THEN 1 ELSE 0 END) AS etag_hashed,
            SUM(CASE WHEN hash_method IN ('md5', 'sha256') THEN 1 ELSE 0 END) AS streamed_hashed
        FROM objects
        WHERE status != 'deleted'
        """
    ).fetchone()
    dup_count = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT hash FROM objects
            WHERE hash IS NOT NULL AND status != 'deleted'
            GROUP BY hash HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    return {
        "total": row["total"],
        "missing_hash": row["missing_hash"],
        "etag_hashed": row["etag_hashed"],
        "streamed_hashed": row["streamed_hashed"],
        "duplicate_groups": dup_count,
    }
