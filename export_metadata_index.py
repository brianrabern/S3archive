#!/usr/bin/env python3
"""Export enriched S3 metadata to a JSON index for photo_viewer."""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from helpers.filter_index import build_filter_index
from helpers.s3 import bucket_name, s3_client

INDEX_KEY = "photos/metadata-index.json"
FILTER_INDEX_KEY = "photos/filter-index.json"
WORKERS = 32


def log(msg):
    print(msg, flush=True)


def load_keys_from_db(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT s3_key FROM metadata_runs WHERE status = 'updated'"
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def load_existing_index():
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=INDEX_KEY)
        data = json.loads(response["Body"].read())
        return data.get("photos") or {}
    except s3_client.exceptions.NoSuchKey:
        return {}
    except Exception as err:
        if getattr(err, "response", {}).get("Error", {}).get("Code") == "NoSuchKey":
            return {}
        log(f"Warning: could not load existing index ({err}); rebuilding from scratch.")
        return {}


def head_metadata(key):
    head = s3_client.head_object(Bucket=bucket_name, Key=key)
    return key, head.get("Metadata", {})


def build_index(keys, existing=None):
    index = dict(existing or {})
    todo = [key for key in keys if key not in index]
    if not todo:
        log(f"All {len(keys)} photos already in index.")
        return index

    log(f"Fetching metadata for {len(todo)} new photos ({len(index)} already cached)...")
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(head_metadata, key): key for key in todo}
        for future in as_completed(futures):
            key, metadata = future.result()
            index[key] = metadata
            done += 1
            if done % 500 == 0 or done == len(todo):
                log(f"Indexed {done}/{len(todo)} new ({len(index)} total)...")
    return index


def upload_index(index):
    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "generated_at": generated_at,
        "count": len(index),
        "photos": index,
    }
    body = json.dumps(payload, indent=2).encode("utf-8")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=INDEX_KEY,
        Body=body,
        ContentType="application/json",
    )
    log(f"Uploaded s3://{bucket_name}/{INDEX_KEY} ({len(index)} photos, {len(body)} bytes)")

    filter_data = build_filter_index(index)
    filter_payload = {
        "generated_at": generated_at,
        "count": len(filter_data["entries"]),
        "entries": filter_data["entries"],
        "locations": filter_data["locations"],
    }
    filter_body = json.dumps(filter_payload, separators=(",", ":")).encode("utf-8")
    s3_client.put_object(
        Bucket=bucket_name,
        Key=FILTER_INDEX_KEY,
        Body=filter_body,
        ContentType="application/json",
    )
    log(
        f"Uploaded s3://{bucket_name}/{FILTER_INDEX_KEY} "
        f"({len(filter_data['entries'])} entries, {len(filter_body)} bytes, "
        f"{len(filter_data['locations'])} locations)"
    )


def run_export(db_path="metadata.db", rebuild=False, dry_run=False):
    keys = load_keys_from_db(db_path)
    if not keys:
        log("No enriched photos in metadata.db (status=updated).")
        return False

    existing = {} if rebuild else load_existing_index()
    log(f"Building index for {len(keys)} enriched photos...")
    index = build_index(keys, existing=existing)
    if dry_run:
        sample = next(iter(index.items()))
        log(f"Dry run. Sample: {sample[0]} -> {list(sample[1].keys())[:5]}")
        return True

    upload_index(index)
    return True


def main():
    parser = argparse.ArgumentParser(description="Export S3 photo metadata index for photo_viewer")
    parser.add_argument("--db", default="metadata.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Ignore existing index on S3 and HEAD every photo",
    )
    args = parser.parse_args()

    run_export(db_path=args.db, rebuild=args.rebuild, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
