# s3archive script v1.0

It's a thing.

## Description

More specifically, its a Python script that recursively scans a directory for files and uploads them to an S3 bucket. The script categorizes files into photos, videos, documents, and miscellaneous files, and adds metadata for the upload.

The user can select which directory to scan and which file types to upload. The script generates a log file containing the details of the upload and uploads it as well to the bucket (in the logs folder).

Categories are defined in the `file_categories` dictionary in the `main.py` file:

```python
file_categories = {
'photos': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.heif', '.heic', '.bpg', '.raw', '.arw', '.nef', '.cr2'],
'videos': ['.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.vob', '.3gp', '.mpg', '.mpeg', '.m4v', '.m2ts', '.ts'],
'documents': ['.pdf','.docx', '.doc','.tex','.txt','.xlsx', '.pptx', '.csv', '.md', '.json', '.xml', '.yaml', '.yml', '.html', '.py'],
'misc': [] ,
'ignore': ['.DS_Store', '.gitignore', '.git', '.vscode', '__pycache__', '.idea', '.venv', '.aux','.log', '.bak','.out', '.bak', '.tmp', '.swp', '.swm']
}
```

Note: Files are given unique names base on the current timestamp and the file's original name. The log file keeps track of the original file path and name and the time of upload

## Installation

1. Clone the repository
2. Create a virtual environment using `python -m venv venv`, and activate it `source venv/bin/activate`.
3. Install the required packages using `pip install -r requirements.txt`
4. Create a `config.py` file in the root directory and add the following:

    ```python
    password = 'your_password'

    S3 = {
        'keys' : {
            'S3AccessKey' : 'your_access_key',
            'S3SecretKey' : 'your_secret_key'
        },
        'bucket' : 'your_bucket_name',
        'region' : 'your_region'
    }
    ```

5. Run the script using `python main.py`

6. Follow the prompts to select the directory and file types to upload
   - An input of 'photoslibrary' will find and scan the Photos Library on MacOS. (Might require manual input of the correct path)
   - Valid file types: `photos`, `videos`, `documents`, `misc`

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. But I really don't care.

## Acknowledgements

Thanks to boto3, and thanks to python, and thanks to the internet, and thanks to the computer, and thanks to the electricity for existing, and thanks to Liebniz for binary numbers, and thanks to the universe for existing.

Oh, and thanks to the `README.md` for existing. And thanks to the LICENSE for existing. And thanks to the `config.py` for existing. And thanks to the `requirements.txt` for existing. And thanks to the `venv` for existing. And thanks to the `.gitignore` for existing, and protecting my access keys. And thanks to the `main.py`, and all the helper functions it appeals to for existing -- without which this script would not run.

And thanks to the compiler for compiling the code. And thanks to the operating system for running the code. And thanks to Alan Turing, and the logicians who came before him (Frege, Aritotle, etc.), and the mathematicians who came before them, and the philosophers who came before them,  and the primordial soup that came before them, and so on.

And thanks to the meta-universe for existing, and thanks to the meta-meta-universe for existing, and thanks to the meta-meta-meta-universe for existing, and thanks to the meta-meta-meta-meta-universe for existing, and thanks to the meta-meta-meta-meta-meta-universe for existing, and thanks to the meta-meta-meta-meta-meta-meta-universe for existing, and thanks to the AI for existing...
