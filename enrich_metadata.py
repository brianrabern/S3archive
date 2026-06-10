#!/usr/bin/env python3
"""
Enrich S3 photo objects with searchable EXIF/IPTC/XMP metadata.

Downloads each image temporarily, extracts tags via ExifTool (same approach as
the Box connector), and writes them to S3 object user metadata.
"""

import argparse
import os
import sqlite3
from datetime import datetime, timezone

from helpers.exif_tags import enrich_object_metadata, write_tags_sidecar
from helpers.geocode import init_geocode_cache, location_label_from_s3_metadata
from helpers.s3 import bucket_name, s3_client

PHOTO_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
    ".heif",
    ".heic",
    ".raw",
    ".arw",
    ".nef",
    ".cr2",
}

METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_runs (
    s3_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    tag_count INTEGER DEFAULT 0,
    processed_at TEXT NOT NULL,
    error TEXT
);
"""


def is_photo_key(key):
    ext = os.path.splitext(key)[1].lower()
    return ext in PHOTO_EXTENSIONS


def list_photo_objects(prefix=None):
    paginator = s3_client.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket_name}
    if prefix:
        kwargs["Prefix"] = prefix

    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if is_photo_key(key):
                yield key


def init_tracking_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(METADATA_SCHEMA)
    conn.commit()
    return conn


def get_processed_keys(conn):
    rows = conn.execute(
        "SELECT s3_key FROM metadata_runs WHERE status IN ('updated', 'no_tags')"
    ).fetchall()
    return {row[0] for row in rows}


def record_result(conn, key, status, tag_count=0, error=None):
    conn.execute(
        """
        INSERT INTO metadata_runs (s3_key, status, tag_count, processed_at, error)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(s3_key) DO UPDATE SET
            status = excluded.status,
            tag_count = excluded.tag_count,
            processed_at = excluded.processed_at,
            error = excluded.error
        """,
        (
            key,
            status,
            tag_count,
            datetime.now(timezone.utc).isoformat(),
            error,
        ),
    )
    conn.commit()


def run(
    prefix=None,
    db_path="metadata.db",
    geocode_db="geocode_cache.db",
    resume=True,
    dry_run=False,
    sidecar=None,
    limit=None,
):
    conn = init_tracking_db(db_path)
    geocode_conn = init_geocode_cache(geocode_db)
    processed = get_processed_keys(conn) if resume else set()
    sidecar_data = {}
    updated = 0
    skipped = 0
    failed = 0

    for i, key in enumerate(list_photo_objects(prefix=prefix), start=1):
        if limit and i > limit:
            break
        if resume and key in processed:
            skipped += 1
            continue

        try:
            head = s3_client.head_object(Bucket=bucket_name, Key=key)
            existing = head.get("Metadata", {})
            result = enrich_object_metadata(
                s3_client,
                bucket_name,
                key,
                existing_metadata=existing,
                dry_run=dry_run,
                geocode_conn=geocode_conn,
            )
            status = result["status"]
            tag_count = len(result.get("tags", {}))
            if not dry_run:
                record_result(conn, key, status, tag_count=tag_count)
            if status in ("updated", "dry_run"):
                updated += 1
                if sidecar:
                    sidecar_data[key] = result.get("tags", {})
            elif status == "no_tags":
                if not dry_run:
                    record_result(conn, key, status, tag_count=0)
            print(f"[{status}] {key} ({tag_count} tags)")
        except Exception as e:
            failed += 1
            if not dry_run:
                record_result(conn, key, "error", error=str(e))
            print(f"[error] {key}: {e}")

        if i % 100 == 0:
            print(f"Progress: {i} photos checked, {updated} enriched, {skipped} skipped...")

    if sidecar and sidecar_data:
        write_tags_sidecar(sidecar_data, sidecar)

    conn.close()
    geocode_conn.close()
    print(
        f"Done. Enriched {updated}, skipped {skipped}, failed {failed}. "
        f"Tracking DB: {db_path}, geocode cache: {geocode_db}"
    )


def backfill_location_labels(
    prefix=None, db_path="metadata.db", geocode_db="geocode_cache.db", limit=None
):
    """Add location_label to enriched photos using GPS already on S3 (no re-download)."""
    conn = init_tracking_db(db_path)
    geocode_conn = init_geocode_cache(geocode_db)
    rows = conn.execute(
        "SELECT s3_key FROM metadata_runs WHERE status = 'updated'"
    ).fetchall()
    updated = 0
    skipped = 0

    for i, (key,) in enumerate(rows, start=1):
        if limit and i > limit:
            break
        if prefix and not key.startswith(prefix):
            continue
        try:
            head = s3_client.head_object(Bucket=bucket_name, Key=key)
            existing = head.get("Metadata", {})
            if existing.get("location_label"):
                skipped += 1
                continue
            label = location_label_from_s3_metadata(existing, geocode_conn=geocode_conn)
            if not label:
                skipped += 1
                continue
            merged = dict(existing)
            merged["location_label"] = label[:200]
            s3_client.copy_object(
                Bucket=bucket_name,
                Key=key,
                CopySource={"Bucket": bucket_name, "Key": key},
                Metadata=merged,
                MetadataDirective="REPLACE",
                ContentType=head.get("ContentType", "application/octet-stream"),
            )
            updated += 1
            print(f"[location] {key} -> {label}")
        except Exception as e:
            print(f"[error] {key}: {e}")

    conn.close()
    geocode_conn.close()
    print(f"Done. Added location_label to {updated} photos, skipped {skipped}.")


def main():
    parser = argparse.ArgumentParser(description="Enrich S3 photos with searchable EXIF metadata.")
    parser.add_argument("--prefix", help="Only process keys under this prefix (e.g. photos/)")
    parser.add_argument("--db", default="metadata.db", help="SQLite tracking database")
    parser.add_argument(
        "--geocode-db",
        default="geocode_cache.db",
        help="SQLite cache for reverse-geocoded coordinates",
    )
    parser.add_argument("--no-resume", action="store_true", help="Re-process all keys")
    parser.add_argument("--dry-run", action="store_true", help="Extract tags but do not update S3")
    parser.add_argument("--sidecar", help="Also write extracted tags to a JSON sidecar file")
    parser.add_argument("--limit", type=int, help="Process at most N photos (for testing)")
    parser.add_argument(
        "--backfill-location",
        action="store_true",
        help="Add location_label to already-enriched photos (uses GPS on S3, no re-download)",
    )
    args = parser.parse_args()

    if args.backfill_location:
        backfill_location_labels(
            prefix=args.prefix,
            db_path=args.db,
            geocode_db=args.geocode_db,
            limit=args.limit,
        )
        return

    run(
        prefix=args.prefix,
        db_path=args.db,
        geocode_db=args.geocode_db,
        resume=not args.no_resume,
        dry_run=args.dry_run,
        sidecar=args.sidecar,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
