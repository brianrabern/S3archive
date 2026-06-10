import json
import os
import tempfile
import time

import exiftool

from helpers.geocode import location_label_from_tags

# Search-oriented subset — titles, dates, people, locations, keywords, camera basics.
SEARCHABLE_TAGS = {
    "EXIF:DateTimeOriginal",
    "EXIF:ImageDescription",
    "EXIF:Artist",
    "EXIF:Copyright",
    "EXIF:Make",
    "EXIF:Model",
    "EXIF:LensModel",
    "EXIF:GPSLatitude",
    "EXIF:GPSLongitude",
    "EXIF:GPSAltitude",
    "Composite:GPSLatitude",
    "Composite:GPSLongitude",
    "Composite:DigitalCreationDateTime",
    "IPTC:ObjectName",
    "IPTC:Headline",
    "IPTC:Caption-Abstract",
    "IPTC:Keywords",
    "IPTC:City",
    "IPTC:Province-State",
    "IPTC:Country",
    "IPTC:By-line",
    "IPTC:CopyrightNotice",
    "XMP:Title",
    "XMP:Description",
    "XMP:Subject",
    "XMP:Creator",
    "XMP:CreateDate",
    "XMP:LocationCreatedCity",
    "XMP:LocationCreatedProvinceState",
    "XMP:LocationCreatedCountryName",
    "XMP:PersonInImage",
    "XMP:Rating",
    "File:FileType",
    "File:MIMEType",
}

# S3 user metadata keys must be lowercase; values max 2KB each, 2KB total for all keys.
S3_METADATA_PREFIX = "x-amz-meta-"


def _s3_metadata_key(tag):
    key = tag.replace(":", "_").lower()
    return key[:80]


def _stringify(value):
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def extract_searchable_tags(file_path):
    with exiftool.ExifToolHelper() as et:
        metadata = et.get_metadata([file_path])[0]

    tags = {}
    for tag, value in metadata.items():
        if tag not in SEARCHABLE_TAGS:
            continue
        text = _stringify(value).strip()
        if text:
            tags[tag] = text[:2000]
    return tags


def tags_to_s3_metadata(tags):
    """Convert EXIF tags to S3-compatible user metadata keys."""
    result = {}
    for tag, value in tags.items():
        result[_s3_metadata_key(tag)] = value[:2000]
    return result


def merge_s3_metadata(existing_metadata, new_metadata):
    merged = dict(existing_metadata or {})
    merged.update(new_metadata)
    return merged


def year_month_from_tags(tags):
    """Return (year, month) ints from extracted EXIF/XMP tags, or (None, None)."""
    import re

    raw = (
        tags.get("EXIF:DateTimeOriginal")
        or tags.get("XMP:CreateDate")
        or tags.get("Composite:DigitalCreationDateTime")
    )
    if not raw:
        return None, None
    match = re.match(r"^(\d{4})[:\-](\d{2})", str(raw).strip())
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def enriched_metadata_from_tags(tags, base_metadata=None, geocode_conn=None):
    """Build S3 user metadata from already-extracted ExifTool tags."""
    s3_meta = tags_to_s3_metadata(tags) if tags else {}
    if tags:
        location_label = location_label_from_tags(tags, geocode_conn=geocode_conn)
        if location_label:
            s3_meta["location_label"] = location_label[:200]
    merged = merge_s3_metadata(base_metadata, s3_meta)
    return merged, tags


def build_enriched_metadata(file_path, base_metadata=None, geocode_conn=None):
    """Extract EXIF/IPTC/XMP tags and merge with upload baseline metadata."""
    tags = extract_searchable_tags(file_path)
    return enriched_metadata_from_tags(tags, base_metadata, geocode_conn)


def download_s3_object_to_temp(s3_client, bucket, key, retries=3):
    suffix = os.path.splitext(key)[1] or ".bin"
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp.close()
    last_error = None
    for attempt in range(retries):
        try:
            s3_client.download_file(bucket, key, temp.name)
            return temp.name
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_error


def enrich_object_metadata(
    s3_client,
    bucket,
    key,
    existing_metadata=None,
    dry_run=False,
    geocode_conn=None,
):
    temp_path = None
    try:
        temp_path = download_s3_object_to_temp(s3_client, bucket, key)
        tags = extract_searchable_tags(temp_path)
        if not tags:
            return {"key": key, "status": "no_tags", "tags": {}}

        s3_meta = tags_to_s3_metadata(tags)
        location_label = location_label_from_tags(tags, geocode_conn=geocode_conn)
        if location_label:
            s3_meta["location_label"] = location_label[:200]
        merged = merge_s3_metadata(existing_metadata, s3_meta)

        if dry_run:
            return {"key": key, "status": "dry_run", "tags": tags, "s3_metadata": merged}

        head = s3_client.head_object(Bucket=bucket, Key=key)
        content_type = head.get("ContentType", "application/octet-stream")

        s3_client.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": key},
            Metadata=merged,
            MetadataDirective="REPLACE",
            ContentType=content_type,
        )
        return {"key": key, "status": "updated", "tags": tags}
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def write_tags_sidecar(tags_by_key, output_path):
    with open(output_path, "w") as f:
        json.dump(tags_by_key, f, indent=2)
