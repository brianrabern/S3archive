"""Build a compact filter index for photo_viewer date/location filters."""

import re

NO_LOCATION = "__none__"

_DATE_RE = re.compile(
    r"^(\d{4})[:\-](\d{2})[:\-](\d{2})(?:[ T](\d{2})[:\-](\d{2})[:\-](\d{2}))?"
)


def _parse_exif_date_iso(metadata):
    raw = (
        metadata.get("exif_datetimeoriginal")
        or metadata.get("xmp_createdate")
        or metadata.get("composite_digitalcreationdatetime")
    )
    if not raw:
        return None
    match = _DATE_RE.match(str(raw).strip())
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    if hour:
        return f"{year}-{month}-{day}T{hour}:{minute}:{second}"
    return f"{year}-{month}-{day}"


def _location_from_metadata(metadata):
    if metadata.get("location_label"):
        return metadata["location_label"]
    parts = [
        metadata.get("xmp_locationcreatedcity") or metadata.get("iptc_city"),
        metadata.get("xmp_locationcreatedprovincestate")
        or metadata.get("iptc_province-state"),
        metadata.get("xmp_locationcreatedcountryname") or metadata.get("iptc_country"),
    ]
    place = ", ".join(p.strip() for p in parts if p and str(p).strip())
    return place or None


def entry_from_metadata(key, metadata):
    date = _parse_exif_date_iso(metadata)
    if not date:
        return None
    location = _location_from_metadata(metadata)
    entry = {"k": key, "d": date}
    if location:
        entry["l"] = location
    return entry


def build_filter_index(photos_metadata):
    """photos_metadata: dict s3_key -> S3 user metadata."""
    entries = []
    location_counts = {}

    for key, metadata in photos_metadata.items():
        entry = entry_from_metadata(key, metadata)
        if not entry:
            continue
        entries.append(entry)
        loc = entry.get("l") or NO_LOCATION
        location_counts[loc] = location_counts.get(loc, 0) + 1

    entries.sort(key=lambda e: e["d"], reverse=True)
    locations = [
        {"name": name, "count": count}
        for name, count in sorted(
            location_counts.items(),
            key=lambda item: (item[0] == NO_LOCATION, -item[1], item[0]),
        )
    ]
    return {"entries": entries, "locations": locations}
