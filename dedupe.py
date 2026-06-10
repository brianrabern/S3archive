#!/usr/bin/env python3
"""
S3 duplicate finder and optional cleanup.

Phase 1: inventory — list objects, hash via ETag or streamed download
Phase 2: report   — emit duplicate manifest (CSV/JSON)
Phase 3: delete    — remove non-canonical copies (requires --confirm)
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone

from config import S3
from helpers.dedupe_db import (
    db_session,
    get_duplicate_groups,
    get_inventoried_keys,
    get_objects_by_hash,
    get_stats,
    init_db,
    mark_canonical_and_duplicates,
    mark_deleted,
    upsert_object,
)
from helpers.object_hash import (
    etag_is_md5,
    hash_from_etag,
    pick_canonical_key,
    stream_hash,
)
from helpers.s3 import bucket_name, s3_client

GROUP_SEPARATOR = "\x1c"
COMMIT_EVERY = 50


def hash_object(key, etag, algorithm="md5", retries=3):
    if etag_is_md5(etag):
        return hash_from_etag(etag), "etag", False

    last_error = None
    for attempt in range(retries):
        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=key)
            content_hash = stream_hash(response["Body"], algorithm=algorithm)
            return content_hash, algorithm, True
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  Retry {attempt + 1}/{retries - 1} for {key} after {e} (waiting {wait}s)")
                time.sleep(wait)
    raise last_error


def list_objects(prefix=None):
    paginator = s3_client.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket_name}
    if prefix:
        kwargs["Prefix"] = prefix

    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            yield obj


def inventory(prefix=None, db_path="dedupe.db", algorithm="md5", resume=True):
    init_db(db_path)
    inventoried = 0
    skipped = 0
    streamed = 0

    with db_session(db_path) as conn:
        existing = get_inventoried_keys(conn) if resume else set()

        for obj in list_objects(prefix=prefix):
            key = obj["Key"]
            if resume and key in existing:
                skipped += 1
                continue

            etag = obj.get("ETag", "")
            size = obj.get("Size", 0)
            last_modified = obj["LastModified"].isoformat()

            content_hash, hash_method, was_streamed = hash_object(key, etag, algorithm=algorithm)
            if was_streamed:
                streamed += 1

            upsert_object(
                conn,
                s3_key=key,
                size=size,
                etag=etag,
                content_hash=content_hash,
                hash_method=hash_method,
                last_modified=last_modified,
            )
            inventoried += 1

            if inventoried % COMMIT_EVERY == 0:
                conn.commit()

            if inventoried % 500 == 0 or (streamed and inventoried % 10 == 0):
                print(f"Inventoried {inventoried} objects ({streamed} streamed hashes)...")

        stats = get_stats(conn)

    print(
        f"Done. Inventoried {inventoried} new objects, skipped {skipped} existing. "
        f"Streamed {streamed} multipart/large uploads for hashing."
    )
    print(f"Database stats: {stats}")


def build_duplicate_manifest(db_path="dedupe.db"):
    manifest = []
    with db_session(db_path) as conn:
        groups = get_duplicate_groups(conn)
        for group in groups:
            keys = group["keys_blob"].split(GROUP_SEPARATOR)
            canonical = pick_canonical_key(keys)
            duplicates = [k for k in keys if k != canonical]
            rows = get_objects_by_hash(conn, group["hash"])
            manifest.append(
                {
                    "hash": group["hash"],
                    "count": group["key_count"],
                    "canonical_key": canonical,
                    "duplicate_keys": duplicates,
                    "objects": [dict(row) for row in rows],
                }
            )
    return manifest


def report(db_path="dedupe.db", output_prefix="duplicates"):
    manifest = build_duplicate_manifest(db_path)
    generated_at = datetime.now(timezone.utc).isoformat()

    json_path = f"{output_prefix}.json"
    csv_path = f"{output_prefix}.csv"

    payload = {
        "generated_at": generated_at,
        "bucket": bucket_name,
        "duplicate_groups": len(manifest),
        "groups": manifest,
    }
    with open(json_path, "w") as f:
        json.dump(payload, f, indent=2)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "hash",
                "count",
                "canonical_key",
                "duplicate_key",
                "size",
                "hash_method",
                "last_modified",
            ]
        )
        for group in manifest:
            objects_by_key = {obj["s3_key"]: obj for obj in group["objects"]}
            for dup_key in group["duplicate_keys"]:
                obj = objects_by_key.get(dup_key, {})
                writer.writerow(
                    [
                        group["hash"],
                        group["count"],
                        group["canonical_key"],
                        dup_key,
                        obj.get("size", ""),
                        obj.get("hash_method", ""),
                        obj.get("last_modified", ""),
                    ]
                )

    total_dup_keys = sum(len(g["duplicate_keys"]) for g in manifest)
    print(f"Found {len(manifest)} duplicate groups ({total_dup_keys} deletable keys).")
    print(f"Wrote {json_path} and {csv_path}")
    print("Review the manifest before running delete.")


def delete_duplicates(db_path="dedupe.db", confirm=False, dry_run=False):
    manifest = build_duplicate_manifest(db_path)
    if not manifest:
        print("No duplicate groups found.")
        return

    keys_to_delete = []
    for group in manifest:
        keys_to_delete.extend(group["duplicate_keys"])

    print(f"Would delete {len(keys_to_delete)} objects across {len(manifest)} groups.")

    if dry_run:
        for group in manifest[:10]:
            print(f"  keep: {group['canonical_key']}")
            for key in group["duplicate_keys"]:
                print(f"  delete: {key}")
        if len(manifest) > 10:
            print(f"  ... and {len(manifest) - 10} more groups")
        return

    if not confirm:
        print("Refusing to delete without --confirm. Run report first and spot-check the manifest.")
        sys.exit(1)

    deleted = 0
    batch = []
    with db_session(db_path) as conn:
        for group in manifest:
            mark_canonical_and_duplicates(
                conn,
                canonical_key=group["canonical_key"],
                duplicate_keys=group["duplicate_keys"],
            )

        def flush_batch(batch):
            nonlocal deleted
            if not batch:
                return
            response = s3_client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": batch, "Quiet": False},
            )
            errors = response.get("Errors", [])
            deleted_keys = [item["Key"] for item in response.get("Deleted", [])]
            # With Quiet=True, S3 omits Deleted from the response even on success.
            if not deleted_keys and not errors:
                deleted_keys = [item["Key"] for item in batch]
            deleted += len(deleted_keys)
            mark_deleted(conn, deleted_keys)
            for err in errors:
                print(f"Delete failed: {err.get('Key')}: {err.get('Message')}")

        for key in keys_to_delete:
            batch.append({"Key": key})
            if len(batch) == 1000:
                flush_batch(batch)
                batch = []

        flush_batch(batch)

    print(f"Deleted {deleted} duplicate objects from s3://{bucket_name}")


def main():
    parser = argparse.ArgumentParser(description="Find and optionally remove duplicate S3 objects.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inv = subparsers.add_parser("inventory", help="Phase 1: crawl bucket and hash objects")
    inv.add_argument("--prefix", help="Only inventory keys under this prefix")
    inv.add_argument("--db", default="dedupe.db", help="SQLite database path")
    inv.add_argument("--algorithm", choices=["md5", "sha256"], default="md5")
    inv.add_argument("--no-resume", action="store_true", help="Re-hash all keys")

    rep = subparsers.add_parser("report", help="Phase 2: write duplicate manifest")
    rep.add_argument("--db", default="dedupe.db")
    rep.add_argument("--output", default="duplicates", help="Output prefix (no extension)")

    delete_cmd = subparsers.add_parser("delete", help="Phase 3: delete non-canonical duplicates")
    delete_cmd.add_argument("--db", default="dedupe.db")
    delete_cmd.add_argument("--confirm", action="store_true", help="Required to actually delete")
    delete_cmd.add_argument("--dry-run", action="store_true", help="Show what would be deleted")

    args = parser.parse_args()

    if args.command == "inventory":
        inventory(
            prefix=args.prefix,
            db_path=args.db,
            algorithm=args.algorithm,
            resume=not args.no_resume,
        )
    elif args.command == "report":
        report(db_path=args.db, output_prefix=args.output)
    elif args.command == "delete":
        delete_duplicates(db_path=args.db, confirm=args.confirm, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
