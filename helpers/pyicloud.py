from helpers.pyicloud import PyiCloudService
from ..config import pyicloud

api = PyiCloudService(pyicloud['username'], pyicloud['password'])

if api.requires_2fa:
    print("Two-factor authentication required.")
    code = input("Enter the code you received of one of your approved devices: ")
    result = api.validate_2fa_code(code)
    print("Code validation result: %s" % result)

# Get the iCloud Photos library
photos_library = api.photos.all

count=0
for photo in photos_library:
    print(count, photo.filename)
    count+=1

# Count the number of photos in the library
total_photos = len(photos_library)
print("Total photos in the library:", total_photos)
