import json
import os
from datetime import datetime, timezone

from helpers.dedupe_db import db_session, init_db, upsert_object
from helpers.s3 import bucket_name, s3_client

INDEX_DBS = {
    "photos": "dedupe.db",
    "videos": "videos_dedupe.db",
}

HASH_INDEX_KEY = "photos/hash-index.json"

_s3_hash_cache = None
_s3_hash_dirty = False

_STATUS_ORDER = {"canonical": 0, "inventoried": 1}


def index_db_for_category(category):
    return INDEX_DBS.get(category)


def is_hash_index_dirty():
    return _s3_hash_dirty


def _pick_better_key(current, candidate, current_status, candidate_status):
    if current is None:
        return candidate
    cur_rank = _STATUS_ORDER.get(current_status, 2)
    cand_rank = _STATUS_ORDER.get(candidate_status, 2)
    if cand_rank < cur_rank:
        return candidate
    if cand_rank > cur_rank:
        return current
    return min(current, candidate)


def build_hash_map_from_db(db_path):
    """Build md5 -> s3_key map from a dedupe SQLite database."""
    if not os.path.exists(db_path):
        return {}

    init_db(db_path)
    hashes = {}
    status_for_key = {}
    with db_session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT hash, s3_key, status FROM objects
            WHERE hash IS NOT NULL AND hash != '' AND status != 'deleted'
            """
        ).fetchall()
    for row in rows:
        content_hash = row["hash"]
        s3_key = row["s3_key"]
        status = row["status"]
        if content_hash not in hashes:
            hashes[content_hash] = s3_key
            status_for_key[content_hash] = status
        else:
            chosen = _pick_better_key(
                hashes[content_hash],
                s3_key,
                status_for_key[content_hash],
                status,
            )
            hashes[content_hash] = chosen
            status_for_key[content_hash] = (
                status if chosen == s3_key else status_for_key[content_hash]
            )
    return hashes


def load_s3_hash_index(force=False):
    """Load photos/hash-index.json from S3 into memory."""
    global _s3_hash_cache
    if _s3_hash_cache is not None and not force:
        return _s3_hash_cache

    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=HASH_INDEX_KEY)
        data = json.loads(response["Body"].read())
        _s3_hash_cache = data.get("hashes") or {}
    except s3_client.exceptions.NoSuchKey:
        _s3_hash_cache = {}
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") == "NoSuchKey":
            _s3_hash_cache = {}
        else:
            print(f"Warning: could not load {HASH_INDEX_KEY} ({err})")
            _s3_hash_cache = {}
    return _s3_hash_cache


def upload_hash_index(hashes):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(hashes),
        "hashes": hashes,
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=HASH_INDEX_KEY,
        Body=body,
        ContentType="application/json",
    )
    print(
        f"Uploaded s3://{bucket_name}/{HASH_INDEX_KEY} "
        f"({len(hashes)} hashes, {len(body)} bytes)"
    )


def persist_s3_hash_index():
    """Upload in-memory hash index to S3 (after new uploads)."""
    global _s3_hash_dirty
    if not _s3_hash_dirty or _s3_hash_cache is None:
        return False
    upload_hash_index(_s3_hash_cache)
    _s3_hash_dirty = False
    return True


def _find_in_local_db(category, content_hash):
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


def find_existing_key(category, content_hash):
    if not content_hash:
        return None

    key = _find_in_local_db(category, content_hash)
    if key:
        return key

    if category == "photos":
        return load_s3_hash_index().get(content_hash)
    return None


def register_upload(category, s3_key, content_hash, size):
    global _s3_hash_dirty

    db_path = index_db_for_category(category)
    if db_path:
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

    if category == "photos" and content_hash:
        cache = load_s3_hash_index()
        if cache.get(content_hash) != s3_key:
            cache[content_hash] = s3_key
            _s3_hash_dirty = True
