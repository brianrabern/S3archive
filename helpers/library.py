import os

def handle_photos_library(directory):
    """
    Special case to process Photos Library.photoslibrary package.
	/Users/username/Pictures/Photos Library.photoslibrary
    /Users/brianrabern/Pictures/Photos
    """
    print("Special case detected: Scanning Photos Library package.")

    # Prompt the user to specify the subdirectory ('Masters' or 'Originals') if known
    subdir = input("Do you know if the path is the 'Masters' or 'Originals' directory? Leave blank to auto-detect: ").strip()

    if not os.path.isdir(directory):
        print(f"Error: Specified path is not a directory: {directory}")
        exit()

    if subdir:
        # User provided subdirectory ('Masters' or 'Originals')
        directory_to_scan = os.path.join(directory, subdir)
        if not os.path.isdir(directory_to_scan):
            print(f"Error: Specified subdirectory '{subdir}' is not valid.")
            exit()
    else:
        # Auto-detect 'Masters' or 'Originals'
        print("Checking for 'Masters' or 'Originals' directory in Photos Library package.")
        directory_to_scan = os.path.join(directory, 'Masters')
        if not os.path.isdir(directory_to_scan):
            directory_to_scan = os.path.join(directory, 'Originals')
            if not os.path.isdir(directory_to_scan):
                print("Error: Photos Library package is missing the 'Masters' or 'Originals' directory.")
                exit()

    print(f"Scanning Photos Library at: {directory_to_scan}")
    return directory_to_scan
