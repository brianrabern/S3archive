import boto3
from config import S3

# set up s3 client
aws_access_key_id = S3['keys']['S3AccessKey']
aws_secret_access_key = S3['keys']['S3SecretKey']
region_name = S3['region']
bucket_name = S3['bucket']

s3_client = boto3.client(
	's3',
	aws_access_key_id=aws_access_key_id,
	aws_secret_access_key=aws_secret_access_key,
	region_name=region_name
)
