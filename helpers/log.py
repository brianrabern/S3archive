import json
import os
import socket



def create_upload_log(file_data, log_file='upload_log.json'):
	"""
	Dumps the file data dictionary to a JSON log file and returns the file path.
	"""
	# Ensure the log file path is absolute or relative to the current working directory
	log_file_path = os.path.join(os.getcwd(), log_file)

	# add the device name
	device_name = socket.gethostname() if socket.gethostname() else 'unknown'
	file_data['device'] = device_name

	# Create or open the log file and write the dictionary to it
	with open(log_file_path, 'w') as f:
		json.dump(file_data, f, indent=4)

	print(f"Log successfully saved to {log_file_path}")

	return log_file_path
