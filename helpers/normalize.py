import re
import os
import random
import string

def normalize_file_name(file_path):
    # Get the base name and extension
    file_name, file_extension = os.path.splitext(os.path.basename(file_path))

    try:
        # Convert to lowercase
        normalized_name = file_name.lower()

        # Replace spaces with underscores
        normalized_name = normalized_name.replace(" ", "_")

        # Remove or replace special characters (e.g., dashes, quotes)
        normalized_name = re.sub(r'[^\w\s-]', '', normalized_name)

        # Replace multiple underscores or hyphens with a single one
        normalized_name = re.sub(r'[_-]+', '_', normalized_name)

        # Optional: Remove timestamps or specific date patterns (e.g., "2024-11-21")
        normalized_name = re.sub(r'\d{4}-\d{2}-\d{2}', '', normalized_name)

        # Final cleanup: Remove leading or trailing underscores
        normalized_name = normalized_name.strip('_')

        # Reattach the extension
        normalized_name += file_extension
    except Exception as e:
        print(f"Error normalizing file name: {e}")
        # If an error occurs, return a random 9 character string + the original extension
        normalized_name = ''.join(random.choices(string.ascii_lowercase + string.digits, k=9)) + file_extension

    # Ensure the file name does not exceed 255 characters
    if len(normalized_name) > 255:
        normalized_name = normalized_name[:255]

    # If the name becomes empty, provide a fallback name
    if len(normalized_name) == 0:
        normalized_name = ''.join(random.choices(string.ascii_lowercase + string.digits, k=9)) + file_extension

    return normalized_name
