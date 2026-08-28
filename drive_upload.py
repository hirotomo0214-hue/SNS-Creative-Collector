import json
import mimetypes
import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


def escape_q(value: str) -> str:
    return value.replace("'", "\\'")


def get_service():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_JSON is missing")
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def find_child_folder(service, parent_id: str, name: str):
    q = (
        f"name = '{escape_q(name)}' and mimeType = '{FOLDER_MIME}' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name)",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def create_child_folder(service, parent_id: str, name: str):
    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    created = service.files().create(
        body=body,
        fields="id,name",
        supportsAllDrives=True,
    ).execute()
    return created["id"]


def find_file(service, parent_id: str, name: str):
    q = (
        f"name = '{escape_q(name)}' and '{parent_id}' in parents "
        "and trashed = false"
    )
    res = service.files().list(
        q=q,
        fields="files(id,name,mimeType)",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    return files[0] if files else None


def upload_or_update(service, parent_id: str, path: Path):
    mime_type, _ = mimetypes.guess_type(path.name)
    media = MediaFileUpload(str(path), mimetype=mime_type or "application/octet-stream", resumable=True)
    existing = find_file(service, parent_id, path.name)
    if existing:
        updated = service.files().update(
            fileId=existing["id"],
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        print(f"Updated: {updated['name']} ({updated['id']})")
    else:
        created = service.files().create(
            body={"name": path.name, "parents": [parent_id]},
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        ).execute()
        print(f"Uploaded: {created['name']} ({created['id']})")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python drive_upload.py <folder_name> <local_dir>")

    folder_name = sys.argv[1]
    local_dir = Path(sys.argv[2])
    root_id = os.environ.get("DRIVE_ROOT_FOLDER_ID", "")
    if not root_id:
        raise SystemExit("DRIVE_ROOT_FOLDER_ID is missing")
    if not local_dir.exists():
        raise SystemExit(f"Local dir not found: {local_dir}")

    service = get_service()
    folder_id = find_child_folder(service, root_id, folder_name)
    if not folder_id:
        folder_id = create_child_folder(service, root_id, folder_name)
        print(f"Created folder: {folder_name} ({folder_id})")
    else:
        print(f"Using folder: {folder_name} ({folder_id})")

    files = [p for p in local_dir.iterdir() if p.is_file()]
    if not files:
        raise SystemExit("No files to upload")

    for path in files:
        upload_or_update(service, folder_id, path)


if __name__ == "__main__":
    main()
