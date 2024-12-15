import socket

# Get the device name (hostname)
device_name = socket.gethostname()
print(f"Device name: {device_name}")
