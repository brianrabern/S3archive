#!/usr/bin/env python3
"""Export photo content hashes to S3 for duplicate detection in CI/email ingest."""

import argparse

import helpers.hash_index as hash_index_module
from helpers.hash_index import build_hash_map_from_db, upload_hash_index


def run_export(db_path="dedupe.db", dry_run=False):
    hashes = build_hash_map_from_db(db_path)
    if not hashes:
        print(f"No hashes in {db_path}.")
        return False

    print(f"Built hash index with {len(hashes)} entries from {db_path}")
    if dry_run:
        sample = next(iter(hashes.items()))
        print(f"Dry run sample: {sample[0][:8]}... -> {sample[1]}")
        return True

    upload_hash_index(hashes)
    hash_index_module._s3_hash_cache = hashes
    hash_index_module._s3_hash_dirty = False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Export photo hash index to S3 for cross-environment dedupe"
    )
    parser.add_argument("--db", default="dedupe.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_export(db_path=args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
