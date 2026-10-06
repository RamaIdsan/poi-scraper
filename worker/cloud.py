"""Helper Google Sheets & Drive memakai service account (untuk GitHub Actions)."""
import json
import os
import time
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


LOG_TAB = "Logs"
LOG_HEADERS = ["timestamp", "job_id", "user", "message"]


def _spreadsheet_id():
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        raise RuntimeError("SHEET_ID belum diset.")
    return sheet_id


def _ensure_sheet(sheets, tab_name, headers):
    sid = _spreadsheet_id()
    meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties.title").execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if tab_name not in titles:
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{tab_name}!A1",
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()
    return sid


def ensure_tab(tab_name, headers):
    """Buat tab bila belum ada, lalu kosongkan isinya (sisakan header)."""
    sheets, _ = _services()
    sid = _ensure_sheet(sheets, tab_name, headers)
    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"{tab_name}!A2:ZZ").execute()
    return sid


def ensure_header(tab_name, headers):
    """Pastikan tab ada dan punya baris header, tanpa menghapus data."""
    sheets, _ = _services()
    sid = _ensure_sheet(sheets, tab_name, headers)
    first = sheets.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{tab_name}!A1:1"
    ).execute().get("values", [])
    if not first:
        sheets.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{tab_name}!A1",
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()
    return sid


def append_rows(tab_name, rows, chunk_size=1000):
    """Append baris ke tab (batched). Mengembalikan jumlah baris terkirim."""
    if not rows:
        return 0
    sheets, _ = _services()
    sid = _spreadsheet_id()
    total = 0
    for i in range(0, len(rows), chunk_size):
        block = rows[i:i + chunk_size]
        sheets.spreadsheets().values().append(
            spreadsheetId=sid, range=f"{tab_name}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": block},
        ).execute()
        total += len(block)
    return total


def overwrite_tab(tab_name, headers, rows):
    """Tulis ulang seluruh tab (header + rows) — dipakai merge final."""
    sheets, _ = _services()
    sid = _ensure_sheet(sheets, tab_name, headers)
    sheets.spreadsheets().values().clear(spreadsheetId=sid, range=f"{tab_name}!A1:ZZ").execute()
    data = [headers] + [list(r) for r in rows]
    for i in range(0, len(data), 1000):
        block = data[i:i + 1000]
        sheets.spreadsheets().values().append(
            spreadsheetId=sid, range=f"{tab_name}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": block},
        ).execute()


def append_log(job_id, user, message):
    try:
        sheets, _ = _services()
        sid = _ensure_sheet(sheets, LOG_TAB, LOG_HEADERS)
        sheets.spreadsheets().values().append(
            spreadsheetId=sid, range=f"{LOG_TAB}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [[time.strftime("%Y-%m-%d %H:%M:%S"), job_id, user, message]]},
        ).execute()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN append_log gagal: {exc}")


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
