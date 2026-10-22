"""Helper Google Sheets & Drive memakai service account (untuk GitHub Actions)."""
import json
import os
import random
import re
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()


def _cell(value):
    """Ubah nilai sel menjadi tipe yang diterima Sheets API."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

JOB_COLUMNS = [
    "job_id", "user", "country", "brand", "spec_json", "status", "progress",
    "created_at", "started_at", "finished_at", "output_file_id", "output_url", "error",
    "result_sheet", "result_gid", "current_target", "listings_found", "records", "eta", "run_url",
    "current_chunk", "total_chunks", "overall_progress", "chunks_json", "filter_stats_json",
    "run_id", "control", "checkpoint_json", "cancelled_at", "zero_targets_json", "updated_at",
]
COL_INDEX = {name: i for i, name in enumerate(JOB_COLUMNS)}
SHEET_NAME = "Jobs"

JOB_CHUNK_TAB = "JobChunks"
JOB_CHUNK_HEADERS = [
    "job_id", "idx", "label", "status", "planned", "done",
    "failed_json", "stats_json", "updated_at",
]
JOB_CHUNK_COL = {name: i for i, name in enumerate(JOB_CHUNK_HEADERS)}


def _is_retryable(exc):
    try:
        from googleapiclient.errors import HttpError

        if isinstance(exc, HttpError):
            status = int(getattr(exc.resp, "status", 0) or 0)
            return status == 429 or 500 <= status < 600
    except Exception:
        pass
    return isinstance(exc, (TimeoutError, OSError))


def _retry_call(fn, *args, max_attempts=5, **kwargs):
    """Retry transien (429/5xx/timeout) dengan exponential backoff + jitter."""
    delay = 2.0
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt >= max_attempts or not _is_retryable(exc):
                raise
            sleep = min(60.0, delay) + random.uniform(0, delay * 0.3)
            print(f"WARN retry {attempt}/{max_attempts} setelah {round(sleep, 1)}s: {exc}")
            time.sleep(sleep)
            delay *= 2


def _creds():
    from google.oauth2 import service_account

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON belum diset.")
    info = json.loads(raw)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


_SHEETS = None
_DRIVE = None
_ROW_CACHE = {}
_TITLE_CACHE = {}


def _services():
    global _SHEETS, _DRIVE
    if _SHEETS is None or _DRIVE is None:
        from googleapiclient.discovery import build

        creds = _creds()
        _SHEETS = build("sheets", "v4", credentials=creds, cache_discovery=False)
        _DRIVE = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _SHEETS, _DRIVE


def _col_letter(index):
    letter = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letter = chr(65 + rem) + letter
    return letter


def find_job_row(sheet_id, job_id):
    """Cari baris job; hasil pemetaan semua job di-cache agar hemat kuota API."""
    sheets, _ = _services()
    cache_key = (sheet_id, job_id)
    if cache_key in _ROW_CACHE:
        return sheets, _ROW_CACHE[cache_key]
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{SHEET_NAME}!A2:A"
    ).execute()
    for i, row in enumerate(result.get("values", [])):
        if row and row[0]:
            _ROW_CACHE[(sheet_id, row[0])] = i + 2
    return sheets, _ROW_CACHE.get(cache_key)


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
    fields = dict(fields)
    fields.setdefault("updated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    data = []
    for key, value in fields.items():
        if key in COL_INDEX:
            col = _col_letter(COL_INDEX[key])
            data.append({"range": f"{SHEET_NAME}!{col}{row}", "values": [[_cell(value)]]})
    if data:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
    return True


def get_job_field(job_id, field):
    """Baca satu kolom job berdasarkan job_id (untuk kontrol pause/stop)."""
    if not job_id or field not in COL_INDEX:
        return ""
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        return ""
    sheets, row = find_job_row(sheet_id, job_id)
    if row is None:
        return ""
    col = _col_letter(COL_INDEX[field])
    values = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"{SHEET_NAME}!{col}{row}"
    ).execute().get("values", [])
    return values[0][0] if values and values[0] else ""


_CHUNK_ROW_CACHE = {}


def _load_chunk_rows(sheets, sid):
    # Pastikan header tab benar (tab bisa dibuat manual/versi lama).
    head = sheets.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{JOB_CHUNK_TAB}!A1:1"
    ).execute().get("values", [])
    if not head or [str(x) for x in head[0]] != JOB_CHUNK_HEADERS:
        sheets.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{JOB_CHUNK_TAB}!A1",
            valueInputOption="RAW", body={"values": [JOB_CHUNK_HEADERS]},
        ).execute()
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{JOB_CHUNK_TAB}!A2:B"
    ).execute()
    for i, row in enumerate(result.get("values", [])):
        if len(row) >= 2:
            _CHUNK_ROW_CACHE[(sid, str(row[0]), str(row[1]))] = i + 2


def upsert_chunk(job_id, idx, fields):
    """Tulis/ubah satu baris chunk di tab JobChunks (tanpa read-modify-write JSON).

    Aman untuk banyak chunk paralel karena setiap proses hanya menulis barisnya sendiri.
    """
    sheet_id = _spreadsheet_id()
    sheets, _ = _services()
    sid = _ensure_sheet(sheets, JOB_CHUNK_TAB, JOB_CHUNK_HEADERS)
    key = (sid, str(job_id), str(idx))
    row = _CHUNK_ROW_CACHE.get(key)
    if row is None:
        _load_chunk_rows(sheets, sid)
        row = _CHUNK_ROW_CACHE.get(key)

    fields = dict(fields)
    fields["job_id"] = job_id
    fields["idx"] = idx
    fields["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    if row is None:
        values = [[_cell(fields.get(header, "")) for header in JOB_CHUNK_HEADERS]]
        result = sheets.spreadsheets().values().append(
            spreadsheetId=sid, range=f"{JOB_CHUNK_TAB}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        updated_range = result.get("updates", {}).get("updatedRange", "")
        match = re.search(r"![A-Z]+(\d+)", updated_range)
        if match:
            _CHUNK_ROW_CACHE[key] = int(match.group(1))
        else:
            _load_chunk_rows(sheets, sid)
        return True

    data = []
    for header, value in fields.items():
        col = JOB_CHUNK_COL.get(header)
        if col is None:
            continue
        data.append({"range": f"{JOB_CHUNK_TAB}!{_col_letter(col)}{row}", "values": [[_cell(value)]]})
    if data:
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": data},
        ).execute()
    return True


def fetch_job_chunks(job_id):
    """Ambil semua baris chunk untuk satu job: {idx: {header: value}}."""
    sheets, _ = _services()
    sid = _spreadsheet_id()
    _ensure_sheet(sheets, JOB_CHUNK_TAB, JOB_CHUNK_HEADERS)
    values = sheets.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{JOB_CHUNK_TAB}!A:I"
    ).execute().get("values", [])
    if not values:
        return {}
    header = [str(h).strip() for h in values[0]]
    out = {}
    for row in values[1:]:
        record = {header[i]: (row[i] if i < len(row) else "") for i in range(len(header))}
        if str(record.get("job_id", "")) != str(job_id):
            continue
        try:
            idx = int(float(str(record.get("idx", "")).strip() or -1))
        except (TypeError, ValueError):
            idx = -1
        if idx >= 0:
            out[idx] = record
    return out


LOG_TAB = "Logs"
LOG_HEADERS = ["timestamp", "job_id", "user", "message"]


def _spreadsheet_id():
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        raise RuntimeError("SHEET_ID belum diset.")
    return sheet_id


def _ensure_sheet(sheets, tab_name, headers):
    sid = _spreadsheet_id()
    key = (sid, tab_name)
    if key not in _TITLE_CACHE:
        meta = sheets.spreadsheets().get(spreadsheetId=sid, fields="sheets.properties.title").execute()
        for s in meta.get("sheets", []):
            _TITLE_CACHE[(sid, s["properties"]["title"])] = True
    if key not in _TITLE_CACHE:
        try:
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=sid,
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()
        except Exception as exc:  # noqa: BLE001
            # Tab mungkin dibuat proses lain; abaikan bila sudah ada.
            print(f"WARN addSheet: {exc}")
        sheets.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{tab_name}!A1",
            valueInputOption="RAW", body={"values": [headers]},
        ).execute()
        _TITLE_CACHE[key] = True
    return sid


def fetch_tab_rows(tab_name):
    """Ambil semua baris tab (termasuk header) sebagai list of list."""
    sheets, _ = _services()
    sid = _spreadsheet_id()
    return sheets.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{tab_name}!A:ZZ"
    ).execute().get("values", [])


def fetch_column_values(tab_name, col_letter):
    """Ambil satu kolom tab (mis. 'R' untuk Query_Target) sebagai list nilai."""
    sheets, _ = _services()
    sid = _spreadsheet_id()
    values = sheets.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{tab_name}!{col_letter}:{col_letter}"
    ).execute().get("values", [])
    return [row[0] if row else "" for row in values]


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
        block = [[_cell(c) for c in row] for row in rows[i:i + chunk_size]]
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
    data = [[_cell(c) for c in headers]] + [[_cell(c) for c in r] for r in rows]
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
    try:
        media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=False)
        file = drive.files().create(
            body=metadata, media_body=media, fields="id,webViewLink", supportsAllDrives=True
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # Fallback: unggah tanpa folder (mis. folder tidak dishare / ID salah).
        print(f"WARN upload ke folder gagal ({exc}); mencoba tanpa folder.")
        metadata.pop("parents", None)
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


def _guarded(fn):
    """RLock (aman lintas-thread) + retry transien untuk fungsi publik."""
    def wrapper(*args, **kwargs):
        with _LOCK:
            return _retry_call(fn, *args, **kwargs)
    wrapper.__name__ = getattr(fn, "__name__", "wrapped")
    return wrapper


for _name in (
    "find_job_row", "update_job", "get_job_field", "_ensure_sheet", "fetch_tab_rows",
    "fetch_column_values", "upsert_chunk", "fetch_job_chunks",
    "ensure_tab", "ensure_header", "append_rows", "overwrite_tab",
    "append_log", "upload_csv",
):
    globals()[_name] = _guarded(globals()[_name])
