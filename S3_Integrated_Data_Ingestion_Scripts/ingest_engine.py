
import os
import re
import hashlib
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError


# ============================================================
# AWS CONFIGURATION
# Credentials are loaded from your environment or AWS profile.
# Never put your real secret key directly in this script.
# ============================================================

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

PRIMARY_BUCKET = "company-ingest-primary-storage"
SECONDARY_BUCKET = "company-ingest-secondary-backup"

# This is the local folder where your project stores its data.
DEFAULT_LOCAL_ROOT = str(Path.home() / "CompanyIngest")


# ============================================================
# NAMING CONVENTION
# Location Number / Task Type / Employee Code / File Name
# ============================================================

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def valid_folder_name(name):
    """Allow only letters, numbers, underscores, and hyphens."""
    return bool(name and NAME_PATTERN.fullmatch(name))


def validate_s3_key(s3_key):
    """Require at least three folders and a filename."""
    parts = s3_key.replace("\\", "/").split("/")

    if len(parts) < 4:
        return False

    if any(not part or part in (".", "..") for part in parts):
        return False

    if any(not valid_folder_name(part) for part in parts[:3]):
        return False

    # The file must have a filename, not just a folder path.
    if not parts[-1] or parts[-1].endswith("/"):
        return False

    return True


# ============================================================
# CHECKSUM
# ============================================================

def calculate_checksum(file_path):
    """Calculate a file's SHA-256 fingerprint."""
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)  # Read 1 MB at a time.
            if not chunk:
                break
            sha256.update(chunk)

    return sha256.hexdigest()


# ============================================================
# AWS CONNECTION
# ============================================================

def connect_to_aws():
    """Use environment credentials, an AWS profile, or IAM role."""
    session = boto3.Session(region_name=REGION)
    s3 = session.client("s3")

    # Confirm that the credentials can access AWS.
    sts = session.client("sts")
    identity = sts.get_caller_identity()

    print("\nAWS connection successful.")
    print("AWS account:", identity["Account"])
    print("Region:", REGION)

    return s3


# ============================================================
# TERMINAL INPUT HELPERS
# ============================================================

def ask_nonempty(prompt):
    while True:
        value = input(prompt).strip()

        if value:
            return value

        print("This field cannot be empty.")


def choose_from_list(items, prompt):
    """Display directories and return the user's selection."""
    if not items:
        return None

    for index, item in enumerate(items, start=1):
        print(f"{index}. {item.name}")

    print("0. Back")

    while True:
        choice = input(prompt).strip()

        if choice == "0":
            return None

        if choice.isdigit():
            number = int(choice)

            if 1 <= number <= len(items):
                return items[number - 1]

        print("Invalid selection. Please try again.")


def get_directory(prompt, default=None):
    """Ask for a local directory and verify that it exists."""
    while True:
        entered = input(prompt).strip().strip('"')

        if not entered and default:
            entered = default

        if not entered:
            print("Please enter a directory path.")
            continue

        path = Path(entered).expanduser()

        if path.is_dir():
            return path.resolve()

        print("Directory not found. Please try again.")


def get_or_create_root():
    """Select the local data root directory."""
    default = Path(DEFAULT_LOCAL_ROOT)

    print("\nChoose your local data root.")
    print("1. Use the default root:", default)
    print("2. Enter another directory")
    print("3. Create a new root directory")

    while True:
        choice = input("Choose 1, 2, or 3: ").strip()

        if choice == "1":
            default.mkdir(parents=True, exist_ok=True)
            return default.resolve()

        if choice == "2":
            return get_directory("Enter existing root path: ")

        if choice == "3":
            entered = ask_nonempty(
                "Enter the new root directory path: "
            ).strip('"')

            path = Path(entered).expanduser()

            if path.exists() and not path.is_dir():
                print("That path exists but is not a directory.")
                continue

            path.mkdir(parents=True, exist_ok=True)
            return path.resolve()

        print("Invalid selection.")


# ============================================================
# CREATE A NEW FOLDER STRUCTURE
# ============================================================

def create_new_structure(root):
    print("\n--- CREATE NEW FOLDER STRUCTURE ---")

    while True:
        location = ask_nonempty("Location number (e.g. LOC001): ")
        task = ask_nonempty("Task type (e.g. Task_Survey): ")
        employee = ask_nonempty("Employee code (e.g. EMP101): ")

        names = [location, task, employee]

        if not all(valid_folder_name(name) for name in names):
            print(
                "Invalid name. Use only letters, numbers, "
                "underscores, and hyphens."
            )
            continue

        target = root / location / task / employee

        if target.exists():
            print("This employee folder already exists:", target)
            print("1. Use this existing folder")
            print("2. Enter different names")
            print("0. Cancel")

            choice = input("Choose an option: ").strip()

            if choice == "1":
                return target

            if choice == "0":
                return None

            continue

        target.mkdir(parents=True, exist_ok=False)
        print("\nFolder structure created:")
        print(target)
        return target


# ============================================================
# NAVIGATE EXISTING FOLDERS
# ============================================================

def select_existing_structure(root):
    print("\n--- SELECT EXISTING FOLDER ---")

    locations = sorted(
        [p for p in root.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower()
    )

    location = choose_from_list(
        locations, "Select location number: "
    )

    if location is None:
        return None

    tasks = sorted(
        [p for p in location.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower()
    )

    task = choose_from_list(tasks, "Select task type: ")

    if task is None:
        return None

    employees = sorted(
        [p for p in task.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower()
    )

    employee = choose_from_list(
        employees, "Select employee code: "
    )

    if employee is None:
        return None

    print("\nSelected folder:", employee)
    return employee


# ============================================================
# FILE DISCOVERY AND TWO-COPY UPLOAD
# ============================================================

def process_ingest_file(s3, file_path, s3_key):
    """Upload a file to both buckets and verify stored contents."""

    if not validate_s3_key(s3_key):
        print(f"[REJECTED] Invalid naming convention: {s3_key}")
        return False

    try:
        local_hash = calculate_checksum(file_path)
        file_size = file_path.stat().st_size

        print(f"\n[PROCESSING] {s3_key}")
        print(f"File size: {file_size} bytes")
        print(f"Local SHA-256: {local_hash}")

        # Upload each copy and verify by downloading it to a
        # temporary file, then comparing the SHA-256 checksum.
        for copy_number, bucket in enumerate(
            [PRIMARY_BUCKET, SECONDARY_BUCKET], start=1
        ):
            print(f"[COPY {copy_number}] Uploading to {bucket}")

            s3.upload_file(str(file_path), bucket, s3_key)

            with tempfile.TemporaryDirectory() as temp_dir:
                downloaded_file = Path(temp_dir) / "verification_file"

                s3.download_file(
                    bucket, s3_key, str(downloaded_file)
                )

                uploaded_hash = calculate_checksum(downloaded_file)

            if uploaded_hash != local_hash:
                print(
                    f"[FAILED] Checksum mismatch in bucket {bucket}"
                )
                return False

            print(
                f"[VERIFIED] Copy {copy_number}: "
                f"SHA-256 matches in {bucket}"
            )

        print(f"[SUCCESS] Both copies verified: {s3_key}")
        return True

    except (ClientError, BotoCoreError, OSError) as error:
        print(f"[ERROR] Could not process {s3_key}: {error}")
        return False


def ingest_directory(s3, root, employee_folder):
    """Upload all files under the selected employee folder."""

    root = root.resolve()
    employee_folder = employee_folder.resolve()

    # Prevent selecting a folder outside the chosen data root.
    if not employee_folder.is_relative_to(root):
        print("[ERROR] Selected folder is outside the data root.")
        return

    files = sorted(
        [p for p in employee_folder.rglob("*") if p.is_file()]
    )

    if not files:
        print("\nNo files found in:", employee_folder)
        return

    print(f"\nFound {len(files)} file(s).")
    print("Files will be uploaded to both S3 buckets.")

    confirm = input("Continue with upload? (yes/no): ").strip().lower()

    if confirm != "yes":
        print("Upload cancelled.")
        return

    success_count = 0
    failure_count = 0

    for file_path in files:
        # Build the S3 key relative to the data root.
        s3_key = file_path.relative_to(root).as_posix()

        if process_ingest_file(s3, file_path, s3_key):
            success_count += 1
        else:
            failure_count += 1

    print("\n========== INGESTION SUMMARY ==========")
    print("Successfully processed:", success_count)
    print("Failed:", failure_count)
    print("=======================================")


# ============================================================
# MAIN TERMINAL MENU
# ============================================================

def main():
    print("=" * 48)
    print("   AWS DATA INGESTION & BACKUP SYSTEM")
    print("=" * 48)

    try:
        s3 = connect_to_aws()
    except (ClientError, BotoCoreError, Exception) as error:
        print("\n[ERROR] AWS connection failed:", error)
        print(
            "Check your AWS credentials, network, region, "
            "and IAM permissions."
        )
        return

    root = get_or_create_root()
    print("\nCurrent data root:", root)

    while True:
        print("\n========== MAIN MENU ==========")
        print("1. Create a new folder structure")
        print("2. Continue with an existing folder")
        print("3. Change local root directory")
        print("4. Exit")

        choice = input("Select an option: ").strip()

        if choice == "1":
            employee_folder = create_new_structure(root)

            if employee_folder:
                ingest_directory(s3, root, employee_folder)

        elif choice == "2":
            employee_folder = select_existing_structure(root)

            if employee_folder:
                ingest_directory(s3, root, employee_folder)

        elif choice == "3":
            root = get_or_create_root()
            print("Current data root:", root)

        elif choice == "4":
            print("Exiting data ingestion system.")
            break

        else:
            print("Invalid selection. Choose 1, 2, 3, or 4.")


if __name__ == "__main__":
    main()
