import os
from helpers.s3 import s3_client
from datetime import datetime
import time
import mimetypes
from enrich_metadata import init_tracking_db, record_result
from export_hash_index import run_export as run_hash_export
from export_metadata_index import run_export
from helpers.exif_tags import build_enriched_metadata
from helpers.geocode import init_geocode_cache
from helpers.normalize import normalize_file_name
from helpers.hash_index import find_existing_key, index_db_for_category, register_upload
from helpers.facebook import is_facebook_export, load_facebook_timestamps, year_month_from_facebook
from helpers.instagram import is_instagram_export, load_instagram_timestamps, year_month_from_instagram
from helpers.object_hash import hash_local_file

# file types categorization
file_categories = {
    "photos": [
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".heif",
        ".heic",
        ".bpg",
        ".raw",
        ".arw",
        ".nef",
        ".cr2",
    ],
    "videos": [
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".wmv",
        ".flv",
        ".webm",
        ".vob",
        ".3gp",
        ".mpg",
        ".mpeg",
        ".m4v",
        ".m2ts",
        ".ts",
    ],
    "documents": [
        ".pdf",
        ".docx",
        ".doc",
        ".tex",
        ".txt",
        ".xlsx",
        ".pptx",
        ".csv",
        ".md",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".html",
        ".py",
    ],
    "misc": [],
    "ignore": [
        ".DS_Store",
        ".gitignore",
        ".git",
        ".vscode",
        "__pycache__",
        ".idea",
        ".venv",
        ".aux",
        ".log",
        ".bak",
        ".out",
        ".bak",
        ".tmp",
        ".swp",
        ".swm",
    ],  # Ignore system files
}


# categorize files by extension
def get_category(file_name):
    file_ext = os.path.splitext(file_name)[1].lower()
    for category, extensions in file_categories.items():
        if file_ext in extensions:
            return category
    return "misc"  # default to 'misc'


# determine year and month from file metadata (using timestamp)
def get_year_and_month(
    file_path,
    instagram_root=None,
    instagram_timestamps=None,
    facebook_root=None,
    facebook_timestamps=None,
):
    if instagram_root or instagram_timestamps:
        from_instagram = year_month_from_instagram(
            file_path, export_root=instagram_root, timestamps=instagram_timestamps
        )
        if from_instagram:
            return from_instagram

    if facebook_root or facebook_timestamps:
        from_facebook = year_month_from_facebook(
            file_path, export_root=facebook_root, timestamps=facebook_timestamps
        )
        if from_facebook:
            return from_facebook

    try:
        timestamp = os.path.getmtime(file_path)  # Get the file's modification time
        date = datetime.fromtimestamp(timestamp)  # Convert to datetime object
        year = date.year
        month = date.month
    except Exception as e:
        print(f"Error getting year and month for {file_path}: {e}")
        year = "unknown"
        month = "unknown"

    return year, month


# process files in a directory
def process_files(directory):
    file_data = {"photos": [], "videos": [], "documents": [], "misc": [], "logs": []}
    # check if the directory exists
    if not os.path.isdir(directory):
        print("Directory not found.")
        return 0

    instagram_timestamps = None
    instagram_root = None
    facebook_timestamps = None
    facebook_root = None
    if is_instagram_export(directory):
        instagram_root = directory
        instagram_timestamps = load_instagram_timestamps(directory)
        print(
            f"Instagram export detected — using post dates for {len(instagram_timestamps)} media files"
        )
    elif is_facebook_export(directory):
        facebook_root = directory
        facebook_timestamps = load_facebook_timestamps(directory)
        print(
            f"Facebook export detected — using post dates for {len(facebook_timestamps)} media files"
        )

    for root, _, files in os.walk(directory):
        print("Searching in:", root)
        for file in files:
            print("Checking file:", file)
            category = get_category(file)
            if category == "ignore" or category == "misc":
                print(f"Ignoring {file}")
                continue  # Skip ignored files

            file_path = os.path.join(root, file)
            if category == "documents" and (
                root.endswith(os.path.join("your_instagram_activity", "media"))
                or (facebook_root and file.endswith(".json"))
            ):
                print(f"Ignoring export metadata JSON {file}")
                continue

            year, month = get_year_and_month(
                file_path,
                instagram_root=instagram_root,
                instagram_timestamps=instagram_timestamps,
                facebook_root=facebook_root,
                facebook_timestamps=facebook_timestamps,
            )

            # Determine the content type
            content_type, _ = mimetypes.guess_type(file_path)
            if content_type is None:
                content_type = "application/octet-stream"  # Fallback for unknown types

            # Add file details to the corresponding category
            print(f"Adding {file_path} to {category} for year {year} and month {month}")
            file_data[category].append(
                {
                    "file_path": file_path,
                    "year": year,
                    "month": month,
                    "content_type": content_type,
                }
            )
    return file_data


def add_epoch_timestamp(filename):
    # Extract the base name (file name) and extension
    name, file_extension = os.path.splitext(filename)
    # Get the current epoch time (including fractional part for more precision)
    epoch_timestamp = int(time.time() * 1000)  # Millisecond precision (times 1000)

    # Create a new file name with the epoch timestamp appended
    new_file_name = f"{name}_{epoch_timestamp}{file_extension}"

    return new_file_name


# function to upload a file to S3
def upload_file(
    bucket_name,
    file_path,
    category,
    year,
    month,
    content_type,
    skip_duplicates=True,
    geocode_conn=None,
    metadata_conn=None,
):
    filename = os.path.basename(file_path)
    normalized_filename = add_epoch_timestamp(normalize_file_name(filename))
    s3_key = f"{category}/{year}/{month}/{normalized_filename}"
    content_hash = None

    if skip_duplicates and index_db_for_category(category):
        try:
            content_hash = hash_local_file(file_path)
            existing_key = find_existing_key(category, content_hash)
            if existing_key:
                print(
                    f"Skipping duplicate of s3://{bucket_name}/{existing_key}: {file_path}"
                )
                return {"status": "skipped", "existing_key": existing_key, "file_path": file_path}
        except Exception as e:
            print(f"Duplicate check failed for {file_path}, uploading anyway: {e}")

    try:
        if content_hash is None:
            content_hash = hash_local_file(file_path)

        base_metadata = {
            "category": category,
            "year": str(year),
            "month": str(month),
            "original_path": file_path,
            "time_uploaded": str(datetime.now()),
        }
        tags = {}
        if category == "photos":
            metadata, tags = build_enriched_metadata(
                file_path, base_metadata=base_metadata, geocode_conn=geocode_conn
            )
        else:
            metadata = base_metadata

        s3_client.upload_file(
            file_path,
            bucket_name,
            s3_key,
            ExtraArgs={
                "ContentType": content_type,
                "Metadata": metadata,
            },
        )
        register_upload(category, s3_key, content_hash, os.path.getsize(file_path))

        if metadata_conn is not None and category == "photos":
            status = "updated" if tags else "no_tags"
            record_result(metadata_conn, s3_key, status, tag_count=len(tags))

        tag_note = f" ({len(tags)} exif tags)" if category == "photos" else ""
        print(f"Uploaded {file_path} to s3://{bucket_name}/{s3_key}{tag_note}")
        return {"status": "uploaded", "s3_key": s3_key, "file_path": file_path}
    except Exception as e:
        print(f"Error uploading {file_path}: {e}")
        return {"status": "error", "file_path": file_path, "error": str(e)}


# function to upload files based on dictionary and file type filter
def upload_files(
    bucket_name, file_data, file_types=["photos"], skip_duplicates=True
):
    file_types = [ft.lower() for ft in file_types] + [
        "logs"
    ]  # Include logs in the file types
    stats = {"uploaded": 0, "skipped": 0, "errors": 0, "skipped_files": []}

    enrich_photos = "photos" in file_types
    geocode_conn = init_geocode_cache("geocode_cache.db") if enrich_photos else None
    metadata_conn = init_tracking_db("metadata.db") if enrich_photos else None
    try:
        for category in file_types:
            if category in file_data:
                for file_info in file_data[category]:
                    file_path = file_info["file_path"]
                    year = file_info["year"]
                    month = file_info["month"]
                    content_type = file_info["content_type"]

                    result = upload_file(
                        bucket_name,
                        file_path,
                        category,
                        year,
                        month,
                        content_type,
                        skip_duplicates=skip_duplicates and category != "logs",
                        geocode_conn=geocode_conn,
                        metadata_conn=metadata_conn,
                    )
                    if result["status"] == "uploaded":
                        stats["uploaded"] += 1
                    elif result["status"] == "skipped":
                        stats["skipped"] += 1
                        stats["skipped_files"].append(result)
                    else:
                        stats["errors"] += 1
    finally:
        if geocode_conn is not None:
            geocode_conn.close()
        if metadata_conn is not None:
            metadata_conn.close()

    return stats


if __name__ == "__main__":
    from config import S3
    from config import password
    from helpers.log import create_upload_log

    password = input("Enter the password: ")

    if password != "password":
        print("Incorrect password. Terminating program.")
        exit()

        # Prompt user for directory to scan
    directory_to_scan = input(
        "Enter the directory to scan for files (/Users/brianrabern): "
    )
    if not directory_to_scan:
        print("Using default directory.")
        directory_to_scan = "/Users/brianrabern/Desktop/crawlMe"

    directory = directory_to_scan.strip().lower()

    if directory == "photoslibrary":
        photos_library_path = os.path.expanduser(
            "~/Pictures/Photos Library.photoslibrary"
        )
        print(f"Photos Library path: {photos_library_path}")
        directory_to_scan = photos_library_path
        # directory_to_scan='/Users/brianrabern/Pictures/Photos Library.photoslibrary/resources/derivatives/masters/'

    # Prompt user for file types (allowing multiple selections, separated by commas)
    file_types_input = input(
        "Enter file types to upload (photos, videos, documents, misc): "
    )

    if not file_types_input:
        print("Using default file type: photos")
        file_types_input = "photos"

    # Parse the file types input
    file_types = [ft.strip() for ft in file_types_input.split(",")]
    file_types = [ft.lower() for ft in file_types]

    # Validate file types
    valid_file_types = ["photos", "videos", "documents"]
    file_types = [ft for ft in file_types if ft in valid_file_types]

    if not file_types:
        print("Invalid file types entered. Terminating program.")

    else:
        print("Selected file types:", file_types)

        skip_dupes_input = input(
            "Skip files already in S3 (matched by content hash)? (Y/n): "
        ).strip().lower()
        skip_duplicates = skip_dupes_input != "n"
        if skip_duplicates:
            for ft in file_types:
                db = index_db_for_category(ft)
                if db:
                    print(f"  {ft}: using {db} for duplicate detection")
                else:
                    print(f"  {ft}: no hash index — duplicates not checked")

        # S3 bucket to upload files to
        bucket_name = S3["bucket"]

        # Crawl the directory and categorize files
        print(f"Scanning directory: {directory_to_scan}")
        file_data = process_files(directory_to_scan)
        print("Categorized file data:", file_data)
        # the file_data should only inclides the file types selected by the user

        file_data = {k: v for k, v in file_data.items() if k in file_types}
        current_year = datetime.now().year
        current_month = datetime.now().month

        # make a json file of the file data and add it
        log_file_path = create_upload_log(file_data)
        file_data["logs"] = [
            {
                "file_path": log_file_path,
                "year": datetime.now().year,
                "month": datetime.now().month,
                "content_type": "application/json",
            }
        ]

        # Upload files to S3
        stats = upload_files(
            bucket_name,
            file_data,
            file_types=file_types,
            skip_duplicates=skip_duplicates,
        )

        if stats["uploaded"] > 0 and "photos" in file_types:
            print("Updating viewer metadata index...")
            run_export()
            print("Updating hash index on S3...")
            run_hash_export()

        for file_type in file_types:
            print(f"Scanned {len(file_data[file_type])} {file_type} for upload")
        print(
            f"Done. Uploaded {stats['uploaded']}, skipped {stats['skipped']} duplicates, "
            f"{stats['errors']} errors."
        )
