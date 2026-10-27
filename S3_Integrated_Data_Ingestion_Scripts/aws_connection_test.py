
import boto3
from botocore.exceptions import ClientError, BotoCoreError

try:
    print("Connecting to AWS...")

    aws = boto3.client("sts", region_name="us-east-1")

    identity = aws.get_caller_identity()

    print("Connection successful!")
    print("AWS Account ID:", identity["Account"])
    print("Connected identity:", identity["Arn"])

except (ClientError, BotoCoreError) as error:
    print("Connection failed.")
    print("Error:", error)
