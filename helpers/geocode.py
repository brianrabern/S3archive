"""Reverse geocode GPS coordinates to a human-readable place name."""

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "S3archive/1.0 (family photo archive)"
MIN_REQUEST_INTERVAL = 1.1

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS geocode_cache (
    coord_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
"""

_last_request_at = 0.0


def _parse_gps(value):
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coord_key(lat, lon):
    return f"{round(lat, 3):.3f},{round(lon, 3):.3f}"


def _place_from_tags(tags):
    city = tags.get("XMP:LocationCreatedCity") or tags.get("IPTC:City")
    state = (
        tags.get("XMP:LocationCreatedProvinceState")
        or tags.get("IPTC:Province-State")
    )
    country = (
        tags.get("XMP:LocationCreatedCountryName") or tags.get("IPTC:Country")
    )
    parts = [p.strip() for p in (city, state, country) if p and str(p).strip()]
    return ", ".join(parts) if parts else None


def _format_nominatim_result(data):
    address = data.get("address") or {}
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("suburb")
        or address.get("county")
    )
    if city:
        city = city.removeprefix("City of ").strip()
    state = address.get("state") or address.get("region")
    country = address.get("country")
    if country in ("United States", "Canada", "Australia") and state:
        parts = [p for p in (city, state, country) if p]
    else:
        parts = [p for p in (city, country) if p]
    if parts:
        return ", ".join(parts)
    display = data.get("display_name") or ""
    if display:
        return ", ".join(p.strip() for p in display.split(",")[:3])
    return None


def _rate_limit():
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_at = time.monotonic()


def _reverse_geocode_api(lat, lon):
    params = urllib.parse.urlencode(
        {"lat": lat, "lon": lon, "format": "json", "zoom": 10}
    )
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    _rate_limit()
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return _format_nominatim_result(data)


def init_geocode_cache(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(CACHE_SCHEMA)
    conn.commit()
    return conn


def lookup_cached_label(conn, lat, lon):
    row = conn.execute(
        "SELECT label FROM geocode_cache WHERE coord_key = ?",
        (_coord_key(lat, lon),),
    ).fetchone()
    return row[0] if row else None


def cache_label(conn, lat, lon, label):
    conn.execute(
        """
        INSERT INTO geocode_cache (coord_key, label, cached_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(coord_key) DO UPDATE SET
            label = excluded.label,
            cached_at = excluded.cached_at
        """,
        (_coord_key(lat, lon), label),
    )
    conn.commit()


def _place_from_s3_metadata(metadata):
    city = metadata.get("xmp_locationcreatedcity") or metadata.get("iptc_city")
    state = (
        metadata.get("xmp_locationcreatedprovincestate")
        or metadata.get("iptc_province-state")
    )
    country = (
        metadata.get("xmp_locationcreatedcountryname") or metadata.get("iptc_country")
    )
    parts = [p.strip() for p in (city, state, country) if p and str(p).strip()]
    return ", ".join(parts) if parts else None


def location_label_from_s3_metadata(metadata, geocode_conn=None):
    """Build location_label from existing S3 user metadata (no image download)."""
    if metadata.get("location_label"):
        return metadata["location_label"]
    place = _place_from_s3_metadata(metadata)
    if place:
        return place
    lat = _parse_gps(
        metadata.get("composite_gpslatitude") or metadata.get("exif_gpslatitude")
    )
    lon = _parse_gps(
        metadata.get("composite_gpslongitude") or metadata.get("exif_gpslongitude")
    )
    if lat is None or lon is None:
        return None
    if geocode_conn is not None:
        cached = lookup_cached_label(geocode_conn, lat, lon)
        if cached:
            return cached
    try:
        label = _reverse_geocode_api(lat, lon)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  geocode failed ({lat}, {lon}): {e}")
        return None
    if label and geocode_conn is not None:
        cache_label(geocode_conn, lat, lon, label)
    return label


def location_label_from_tags(tags, geocode_conn=None):
    """Return a display-friendly place name from EXIF tags and optional GPS."""
    place = _place_from_tags(tags)
    if place:
        return place

    lat = _parse_gps(
        tags.get("Composite:GPSLatitude") or tags.get("EXIF:GPSLatitude")
    )
    lon = _parse_gps(
        tags.get("Composite:GPSLongitude") or tags.get("EXIF:GPSLongitude")
    )
    if lat is None or lon is None:
        return None

    if geocode_conn is not None:
        cached = lookup_cached_label(geocode_conn, lat, lon)
        if cached:
            return cached

    try:
        label = _reverse_geocode_api(lat, lon)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  geocode failed ({lat}, {lon}): {e}")
        return None

    if label and geocode_conn is not None:
        cache_label(geocode_conn, lat, lon, label)
    return label
