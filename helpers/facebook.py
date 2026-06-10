import json
import os
from datetime import datetime


def is_facebook_export(root):
    if os.path.basename(root.rstrip("/")) == "your_facebook_activity":
        return True
    return os.path.isdir(os.path.join(root, "posts", "album"))


def _walk_media_timestamps(obj, timestamps):
    if isinstance(obj, dict):
        uri = obj.get("uri")
        ts = obj.get("creation_timestamp")
        if uri and ts:
            timestamps[os.path.basename(uri)] = int(ts)
        for value in obj.values():
            _walk_media_timestamps(value, timestamps)
    elif isinstance(obj, list):
        for item in obj:
            _walk_media_timestamps(item, timestamps)


def load_facebook_timestamps(export_root):
    """Map media basename -> creation_timestamp from Facebook JSON exports."""
    timestamps = {}

    for dirpath, _, files in os.walk(export_root):
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path) as f:
                    _walk_media_timestamps(json.load(f), timestamps)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: could not read Facebook metadata {path}: {e}")

    return timestamps


def year_month_from_facebook(file_path, timestamps=None, export_root=None):
    if timestamps is None and export_root:
        timestamps = load_facebook_timestamps(export_root)

    if timestamps:
        ts = timestamps.get(os.path.basename(file_path))
        if ts:
            dt = datetime.fromtimestamp(ts)
            return dt.year, dt.month

    return None
