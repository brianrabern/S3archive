import os
from helpers.s3 import s3_client
from datetime import datetime
import time
import mimetypes
from helpers.normalize import normalize_file_name

# file types categorization
file_categories = {
	'photos': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.heif', '.heic', '.bpg', '.raw', '.arw', '.nef', '.cr2'],
	'videos': ['.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.vob', '.3gp', '.mpg', '.mpeg', '.m4v', '.m2ts', '.ts'],
	'documents': ['.pdf','.docx', '.doc','.tex','.txt','.xlsx', '.pptx', '.csv', '.md', '.json', '.xml', '.yaml', '.yml', '.html', '.py'],
	'misc': [] ,
	'ignore': ['.DS_Store', '.gitignore', '.git', '.vscode', '__pycache__', '.idea', '.venv', '.aux','.log', '.bak','.out', '.bak', '.tmp', '.swp', '.swm'] # Ignore system files
}

# categorize files by extension
def get_category(file_name):
	file_ext = os.path.splitext(file_name)[1].lower()
	for category, extensions in file_categories.items():
		if file_ext in extensions:
			return category
	return 'misc'  # default to 'misc'

# determine year and month from file metadata (using timestamp)
def get_year_and_month(file_path):
	try:
		timestamp = os.path.getmtime(file_path)  # Get the file's modification time
		date = datetime.fromtimestamp(timestamp)  # Convert to datetime object
		year = date.year
		month = date.month
	except Exception as e:
		print(f"Error getting year and month for {file_path}: {e}")
		year = 'unknown'
		month = 'unknown'

	return year, month

# process files in a directory
def process_files(directory):
	file_data = {
		"photos": [],
		"videos": [],
		"documents": [],
		"misc": [],
		"logs": []
	}
	# check if the directory exists
	if not os.path.isdir(directory):
		print("Directory not found.")
		return 0

	for root, _, files in os.walk(directory):
		print("Searching in:", root)
		for file in files:
			print("Checking file:", file)
			category = get_category(file)
			if category == 'ignore' or category == 'misc':
				print(f"Ignoring {file}")
				continue  # Skip ignored files

			file_path = os.path.join(root, file)
			year, month = get_year_and_month(file_path)

			  # Determine the content type
			content_type, _ = mimetypes.guess_type(file_path)
			if content_type is None:
				content_type = "application/octet-stream"  # Fallback for unknown types


			# Add file details to the corresponding category
			print(f"Adding {file_path} to {category} for year {year} and month {month}")
			file_data[category].append({
				"file_path": file_path,
				"year": year,
				"month": month,
				"content_type": content_type
			})
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
def upload_file(bucket_name,file_path, category, year, month, content_type):
	filename = os.path.basename(file_path)
	normalized_filename = add_epoch_timestamp(normalize_file_name(filename))
	s3_key = f'{category}/{year}/{month}/{normalized_filename}'
	try:
		s3_client.upload_file(
			file_path,
			bucket_name,
			s3_key,
			ExtraArgs={
				'ACL': 'public-read',
				'ContentType': content_type,
				'ContentDisposition': 'inline',
				'Metadata': {
					'category': category,
					'year': str(year),
					'month': str(month),
					'original_path': file_path,
					'time_uploaded': str(datetime.now())
				}
			}
		 )
		print(f'Uploaded {file_path} to s3://{bucket_name}/{s3_key}')
	except Exception as e:
		print(f"Error uploading {file_path}: {e}")


# function to upload files based on dictionary and file type filter
def upload_files(bucket_name,file_data, file_types = ['photos']): # default to photos
	file_types = [ft.lower() for ft in file_types] + ['logs']  # Include logs in the file types

	for category in file_types:
		if category in file_data:
			for file_info in file_data[category]:
				file_path = file_info['file_path']
				year = file_info['year']
				month = file_info['month']
				content_type = file_info['content_type']

				upload_file(
					bucket_name,
					file_path,
					category,
					year,
					month,
					content_type
				)

if __name__ == '__main__':
	from config import S3
	from config import password
	from helpers.log import create_upload_log

	password = input("Enter the password: ")

	if password != 'password':
		print("Incorrect password. Terminating program.")
		exit()

	 # Prompt user for directory to scan
	directory_to_scan = input("Enter the directory to scan for files (/Users/brianrabern): ")
	if not directory_to_scan:
		print("Using default directory.")
		directory_to_scan = '/Users/brianrabern/Desktop/crawlMe'

	# Prompt user for file types (allowing multiple selections, separated by commas)
	file_types_input = input("Enter file types to upload (photos, videos, documents, misc): ")

	if not file_types_input:
		print("Using default file type: photos")
		file_types_input = 'photos'

	# Parse the file types input
	file_types = [ft.strip() for ft in file_types_input.split(',')]
	file_types = [ft.lower() for ft in file_types]

	# Validate file types
	valid_file_types = ['photos', 'videos', 'documents']
	file_types = [ft for ft in file_types if ft in valid_file_types]

	if not file_types:
		print("Invalid file types entered. Terminating program.")

	else:
		print("Selected file types:", file_types)
		# S3 bucket to upload files to
		bucket_name = S3['bucket']

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
		file_data['logs'] = [{'file_path': log_file_path, 'year': datetime.now().year, 'month': datetime.now().month, 'content_type': 'application/json'}]

		# Upload files to S3
		upload_files(bucket_name, file_data, file_types=file_types)  # Upload the selected file types
		print("Upload complete.")
