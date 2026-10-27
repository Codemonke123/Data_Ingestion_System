import boto3

# Replace with your actual credentials
AWS_ACCESS_KEY = "YOUR_ACCESS_KEY_ID"
AWS_SECRET_KEY = "YOUR_SECRET_ACCESS_KEY"
REGION = "us-east-1"  # Change to your primary region if different

s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=REGION
)

try:
    # List buckets
    response = s3.list_buckets()
    buckets = [b['Name'] for b in response['Buckets']]
    print("Successfully connected! Buckets found:", buckets)

    # Upload a test file to primary bucket
    s3.put_object(
        Bucket='company-ingest-primary-storage',
        Key='test.txt',
        Body=b'Verification Test File'
    )
    print("Upload successful: 'test.txt' uploaded to company-ingest-primary-storage")

except Exception as e:
    print("Error during verification:", e)