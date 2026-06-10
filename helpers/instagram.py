import json
import os
import re
from datetime import datetime

YYYYMM_IN_PATH = re.compile(r"/(?:posts|reels|stories)/(\d{4})(\d{2})/")


def is_instagram_export(root):
    return os.path.isdir(os.path.join(root, "your_instagram_activity"))


def load_instagram_timestamps(export_root):
    """Map media basename -> creation_timestamp from Instagram JSON exports."""
    media_dir = os.path.join(export_root, "your_instagram_activity", "media")
    if not os.path.isdir(media_dir):
        return {}

    timestamps = {}

    def walk(obj):
        if isinstance(obj, dict):
            uri = obj.get("uri")
            ts = obj.get("creation_timestamp")
            if uri and ts:
                timestamps[os.path.basename(uri)] = int(ts)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for name in os.listdir(media_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(media_dir, name)
        try:
            with open(path) as f:
                walk(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: could not read Instagram metadata {path}: {e}")

    return timestamps


def year_month_from_instagram(file_path, export_root=None, timestamps=None):
    match = YYYYMM_IN_PATH.search(file_path.replace("\\", "/"))
    if match:
        return int(match.group(1)), int(match.group(2))

    if timestamps is None and export_root:
        timestamps = load_instagram_timestamps(export_root)

    if timestamps:
        ts = timestamps.get(os.path.basename(file_path))
        if ts:
            dt = datetime.fromtimestamp(ts)
            return dt.year, dt.month

    return None
