"""Helper Google Sheets & Drive memakai service account (untuk GitHub Actions)."""
import json
import os
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

JOB_COLUMNS = [
    "job_id", "user", "country", "brand", "spec_json", "status", "progress",
    "created_at", "started_at", "finished_at", "output_file_id", "output_url", "error",
]
COL_INDEX = {name: i for i, name in enumerate(JOB_COLUMNS)}
SHEET_NAME = "Jobs"


def _creds():
    from google.oauth2 import service_account

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON belum diset.")
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _services():
    from googleapiclient.discovery import build

    creds = _creds()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return sheets, drive


def _col_letter(index):
    letter = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letter = chr(65 + rem) + letter
    return letter


def find_job_row(sheet_id, job_id):
    sheets, _ = _services()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{SHEET_NAME}!A2:A"
    ).execute()
    for i, row in enumerate(result.get("values", [])):
        if row and row[0] == job_id:
            return sheets, i + 2
    return sheets, None


def update_job(job_id, fields):
    """Update kolom job berdasarkan job_id. Mengembalikan True bila baris ditemukan."""
    if not job_id:
        return False
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        return False
    sheets, row = find_job_row(sheet_id, job_id)
    if row is None:
        return False
    data = []
    for key, value in fields.items():
        if key in COL_INDEX:
            col = _col_letter(COL_INDEX[key])
            data.append({"range": f"{SHEET_NAME}!{col}{row}", "values": [[value]]})
    if data:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
    return True


def upload_csv(local_path, name=None, share_email=None):
    """Upload file ke folder Drive. Mengembalikan (file_id, webViewLink)."""
    from googleapiclient.http import MediaFileUpload

    folder_id = os.environ.get("DRIVE_FOLDER_ID")
    _, drive = _services()
    local_path = Path(local_path)
    metadata = {"name": name or local_path.name}
    if folder_id:
        metadata["parents"] = [folder_id]
    media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=False)
    file = drive.files().create(
        body=metadata, media_body=media, fields="id,webViewLink", supportsAllDrives=True
    ).execute()
    if share_email:
        try:
            drive.permissions().create(
                fileId=file["id"],
                body={"type": "user", "role": "writer", "emailAddress": share_email},
                sendNotificationEmail=False,
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"WARN gagal share ke {share_email}: {exc}")
    return file["id"], file.get("webViewLink", "")
