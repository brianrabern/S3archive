import os

import boto3

try:
    from config import S3

    aws_access_key_id = S3["keys"]["S3AccessKey"]
    aws_secret_access_key = S3["keys"]["S3SecretKey"]
    region_name = S3["region"]
    bucket_name = S3["bucket"]
except ImportError:
    # GitHub Actions / CI: config.py is gitignored, use env vars
    aws_access_key_id = os.environ["AWS_ACCESS_KEY_ID"]
    aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"]
    region_name = os.environ.get("AWS_REGION", "us-west-2")
    bucket_name = os.environ["S3_BUCKET"]

s3_client = boto3.client(
    "s3",
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=region_name,
)
