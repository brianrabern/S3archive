#!/usr/bin/env python3
"""Ingest photo attachments from Gmail and upload to S3 with full metadata enrichment."""

import base64
import mimetypes
import os
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from enrich_metadata import init_tracking_db, record_result
from export_metadata_index import run_export
from helpers.exif_tags import (
    enriched_metadata_from_tags,
    extract_searchable_tags,
    year_month_from_tags,
)
from helpers.geocode import init_geocode_cache
from helpers.hash_index import (
    find_existing_key,
    is_hash_index_dirty,
    load_s3_hash_index,
    persist_s3_hash_index,
    register_upload,
)
from helpers.normalize import normalize_file_name
from helpers.object_hash import hash_local_file
from helpers.s3 import bucket_name, s3_client

# ── config ────────────────────────────────────────────────────────────────────

PHOTOS_ALIAS = "brian.rabern+photos@gmail.com"
CATEGORY = "photos"

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

# ── auth ──────────────────────────────────────────────────────────────────────


def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return build("gmail", "v1", credentials=creds)


# ── upload ────────────────────────────────────────────────────────────────────


def add_epoch_timestamp(filename):
    name, ext = os.path.splitext(filename)
    epoch_ms = int(time.time() * 1000)
    return f"{name}_{epoch_ms}{ext}"


def upload_photo(
    file_path,
    original_filename,
    message_id,
    year,
    month,
    tags,
    geocode_conn=None,
    metadata_conn=None,
):
    """Upload to photos/{year}/{month}/ with the same metadata as main.py + enrichment."""
    normalized = add_epoch_timestamp(normalize_file_name(original_filename))
    # Match main.py / existing bucket layout: month is not zero-padded (photos/2019/4/)
    s3_key = f"{CATEGORY}/{year}/{month}/{normalized}"

    content_hash = hash_local_file(file_path)
    existing_key = find_existing_key(CATEGORY, content_hash)
    if existing_key:
        print(f"  skipping duplicate of s3://{bucket_name}/{existing_key}")
        return {"status": "skipped", "existing_key": existing_key}

    original_path = f"email:{message_id}/{original_filename}"
    base_metadata = {
        "category": CATEGORY,
        "year": str(year),
        "month": str(month),
        "original_path": original_path,
        "time_uploaded": str(datetime.now()),
    }

    metadata, _ = enriched_metadata_from_tags(
        tags, base_metadata=base_metadata, geocode_conn=geocode_conn
    )

    content_type, _ = mimetypes.guess_type(original_filename)
    content_type = content_type or "application/octet-stream"

    s3_client.upload_file(
        file_path,
        bucket_name,
        s3_key,
        ExtraArgs={
            "ContentType": content_type,
            "Metadata": metadata,
        },
    )
    register_upload(CATEGORY, s3_key, content_hash, os.path.getsize(file_path))

    if metadata_conn is not None:
        status = "updated" if tags else "no_tags"
        record_result(metadata_conn, s3_key, status, tag_count=len(tags))

    print(f"  uploaded -> s3://{bucket_name}/{s3_key} ({len(tags)} exif tags)")
    return {"status": "uploaded", "s3_key": s3_key, "tag_count": len(tags)}


# ── gmail processing ──────────────────────────────────────────────────────────


def collect_parts(payload):
    """Flatten all MIME parts recursively."""
    parts = []
    for part in payload.get("parts", []):
        if part.get("parts"):
            parts.extend(collect_parts(part))
        else:
            parts.append(part)
    return parts


def extract_tags_and_date(file_path, email_dt):
    """Extract tags once; prefer EXIF date for folder, else email received date."""
    tags = extract_searchable_tags(file_path)
    year, month = year_month_from_tags(tags)
    if year is not None:
        return tags, year, month, "exif"
    return tags, email_dt.year, email_dt.month, "email"


def process_messages():
    service = get_gmail_service()
    geocode_conn = init_geocode_cache("geocode_cache.db")
    metadata_conn = init_tracking_db("metadata.db")
    print("Loading hash index from S3...")
    load_s3_hash_index(force=True)

    results = (
        service.users()
        .messages()
        .list(userId="me", q=f"to:{PHOTOS_ALIAS} is:unread has:attachment")
        .execute()
    )

    messages = results.get("messages", [])
    print(f"Found {len(messages)} unread message(s) to process")

    uploaded = 0
    skipped = 0

    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}

        try:
            email_dt = parsedate_to_datetime(headers.get("Date", ""))
        except Exception:
            email_dt = datetime.now(timezone.utc)

        print(
            f"\nProcessing message {msg_ref['id']} "
            f"({headers.get('Subject', '(no subject)')})"
        )

        for part in collect_parts(msg["payload"]):
            filename = part.get("filename", "")
            if not filename:
                continue

            ext = os.path.splitext(filename)[1].lower()
            if ext not in PHOTO_EXTENSIONS:
                print(f"  skipping non-photo attachment: {filename}")
                continue

            attachment_id = part["body"].get("attachmentId")
            if not attachment_id:
                continue

            attachment = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=msg_ref["id"], id=attachment_id)
                .execute()
            )

            data = base64.urlsafe_b64decode(attachment["data"])

            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name

            try:
                tags, year, month, source = extract_tags_and_date(tmp_path, email_dt)
                print(f"  date for {filename}: {year}/{month} (from {source})")

                result = upload_photo(
                    tmp_path,
                    filename,
                    msg_ref["id"],
                    year,
                    month,
                    tags,
                    geocode_conn=geocode_conn,
                    metadata_conn=metadata_conn,
                )
                if result["status"] == "uploaded":
                    uploaded += 1
                elif result["status"] == "skipped":
                    skipped += 1
            finally:
                os.unlink(tmp_path)

        service.users().messages().modify(
            userId="me",
            id=msg_ref["id"],
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        print("  marked as read")

    geocode_conn.close()
    metadata_conn.close()
    print(f"\nDone. Uploaded {uploaded}, skipped {skipped} duplicate(s).")

    if is_hash_index_dirty():
        print("Updating hash index on S3...")
        persist_s3_hash_index()

    if uploaded > 0:
        print("Updating viewer metadata index...")
        run_export()


if __name__ == "__main__":
    process_messages()
