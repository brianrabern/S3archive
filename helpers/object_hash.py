import hashlib
import re

ORGANIZED_PREFIX = re.compile(r"^(photos|videos|documents)/\d{4}/\d{1,2}/")


def normalize_etag(etag):
    if not etag:
        return None
    return etag.strip('"')


def etag_is_md5(etag):
    normalized = normalize_etag(etag)
    if not normalized:
        return False
    return "-" not in normalized


def hash_from_etag(etag):
    return normalize_etag(etag)


def hash_local_file(file_path, algorithm="md5", chunk_size=8 * 1024 * 1024):
    hasher = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def stream_hash(body, algorithm="md5", chunk_size=8 * 1024 * 1024):
    hasher = hashlib.new(algorithm)
    while True:
        chunk = body.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest()


def pick_canonical_key(keys):
    """Prefer organized category/year/month paths, then shortest key, then earliest lexicographically."""

    def score(key):
        organized = 1 if ORGANIZED_PREFIX.match(key) else 0
        return (-organized, len(key), key)

    return sorted(keys, key=score)[0]
