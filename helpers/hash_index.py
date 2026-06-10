import os
from datetime import datetime, timezone

from helpers.dedupe_db import db_session, init_db, upsert_object

INDEX_DBS = {
    "photos": "dedupe.db",
    "videos": "videos_dedupe.db",
}


def index_db_for_category(category):
    return INDEX_DBS.get(category)


def find_existing_key(category, content_hash):
    db_path = index_db_for_category(category)
    if not db_path or not os.path.exists(db_path):
        return None

    init_db(db_path)
    with db_session(db_path) as conn:
        row = conn.execute(
            """
            SELECT s3_key FROM objects
            WHERE hash = ? AND status != 'deleted'
            ORDER BY
                CASE status WHEN 'canonical' THEN 0 WHEN 'inventoried' THEN 1 ELSE 2 END,
                s3_key
            LIMIT 1
            """,
            (content_hash,),
        ).fetchone()
        return row["s3_key"] if row else None


def register_upload(category, s3_key, content_hash, size):
    db_path = index_db_for_category(category)
    if not db_path:
        return

    init_db(db_path)
    with db_session(db_path) as conn:
        upsert_object(
            conn,
            s3_key=s3_key,
            size=size,
            etag=f'"{content_hash}"',
            content_hash=content_hash,
            hash_method="upload",
            last_modified=datetime.now(timezone.utc).isoformat(),
        )
