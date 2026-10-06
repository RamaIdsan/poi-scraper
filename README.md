# POI Scraper (Indonesia & Philippines)

Sistem scraping POI Google Maps dengan dashboard Google Apps Script dan worker **GitHub Actions** (tanpa biaya, tanpa kartu). Hasil CSV diunggah ke Google Drive.

```
Apps Script Web App (Dashboard + API)
        |  API key / Google login
        v
Google Sheet (Users, Jobs, Logs)  <--update--  GitHub Actions (Playwright)
        ^                                         |
        |                                         v
   output_url (Drive)  <---------------------  Google Drive CSV
```

## Struktur

```
scraper_indonesia.py        # scraper + job-mode (multi-negara)
admin/build_index.py        # generator dropdown
admin/id_index.json         # index Indonesia
admin/ph_index.json         # index Philippines
worker/plan_chunks.py       # pecah spec -> chunk per provinsi
worker/run_job.py           # jalankan 1 chunk + upload + update Sheet
worker/merge_parts.py       # gabung hasil chunk + dedup
worker/cloud.py             # helper Sheets & Drive
.github/workflows/scrape.yml
data/                       # CSV admin
apps-script/                # Code.gs, Index.html, appsscript.json
```

## Pemakaian lokal

Interaktif:
```
python scraper_indonesia.py
```

Job-mode (non-interaktif):
```
python scraper_indonesia.py --job spec.json
```
Contoh `spec.json`:
```json
{
  "job_id": "local-1",
  "country": "philippines",
  "brand": "Alfamart",
  "level": "kota",
  "scope": {"provinsi": ["Abra"], "kota": ["Bangued"]},
  "mode": "expand",
  "tile": 0,
  "params": {"max_scrolls": 100, "filter_relevance": true, "headless": true}
}
```
`mode`: `unit` (unit terpilih) atau `expand` (pecah ke unit terkecil). `tile`: 0/1/2 = 1 / 3x3 / 5x5 query per target.

Regenerate index dropdown: `python admin/build_index.py`

---

## Deploy

### 1. Repo GitHub (public)
1. Buat repo public `poi-scraper` di akun `RamaIdsan`.
2. Push:
```
git remote add origin https://github.com/RamaIdsan/poi-scraper.git
git branch -M main
git push -u origin main
```

### 2. Google Sheet
1. Buat Spreadsheet baru. Catat **SHEET_ID** dari URL:
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`
2. Tab `Users`, `Jobs`, `Logs` akan dibuat otomatis oleh `setup()` di Apps Script.

### 3. Service Account (untuk worker)
1. <https://console.cloud.google.com> → buat Project.
2. APIs & Services → Library → aktifkan **Google Sheets API** dan **Google Drive API**.
3. IAM & Admin → Service Accounts → Create → selesai. Buka → Keys → Add key → JSON → unduh.
4. Buat folder Google Drive untuk hasil, catat **DRIVE_FOLDER_ID** dari URL folder.
5. **Share** Spreadsheet dan folder Drive ke email service account (`...@....iam.gserviceaccount.com`) sebagai **Editor**.

### 4. GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:
| Name | Isi |
|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | isi penuh file JSON service account |
| `SHEET_ID` | ID spreadsheet |
| `DRIVE_FOLDER_ID` | ID folder Drive |
| `SHARE_EMAIL` | `ramaidsan9995@gmail.com` |

### 5. GitHub PAT (untuk Apps Script memicu workflow)
Fine-grained token → Repository access: `poi-scraper` → Permissions:
- **Actions: Read and write**
- **Contents: Read and write**

Simpan token (dipakai di Script Properties `GITHUB_PAT`).

### 6. Apps Script
1. <https://script.google.com> → New project.
2. Project Settings → centang **Show "appsscript.json"**.
3. Salin isi `apps-script/Code.gs` → `Code.gs`; buat file HTML `Index` → isi `Index.html`; ganti `appsscript.json`.
4. Project Settings → Script Properties, tambahkan:
   - `SHEET_ID`
   - `GITHUB_PAT`
   - (opsional) `GITHUB_OWNER=RamaIdsan`, `GITHUB_REPO=poi-scraper`, `GITHUB_REF=main`, `INDEX_BASE`, `ADMIN_EMAILS`, `DEFAULT_QUOTA`, `OAUTH_CLIENT_ID`
   > `setup()` akan mengisi nilai default bila kosong.
5. Jalankan fungsi `setup()` lalu `setupTriggers()` (izinkan akses saat diminta).
6. Deploy → New deployment → Web app:
   - Execute as: **Me**
   - Who has access: **Anyone**
7. Buka URL Web App.

### 7. Bootstrap admin & API key pertama
Panel Admin hanya muncul setelah login, jadi key pertama harus dibuat dari editor:
1. (Bila perlu) jalankan `setAdminEmails("ramaidsan9995@gmail.com")`.
2. Jalankan fungsi **`bootstrapAdmin()`** dari editor Apps Script.
3. Buka **Execution log** (View → Logs) dan salin baris `API KEY : poi_...`.
4. Buka Web App, tempel key di kolom API key → login. Panel Admin muncul untuk membuat key user lain.

> Key hanya tampil di log. Bila lupa, jalankan `bootstrapAdmin()` lagi (membuat key baru) atau cek daftar user dengan `listApiUsers()`.
> Setiap kali mengubah `Code.gs`/`Index.html`, lakukan **Deploy → Manage deployments → Edit → New version** agar Web App memakai kode terbaru.

---

## API (akses program)

POST ke URL Web App Apps Script:

```bash
curl -s -X POST "$WEBAPP_URL" -H "Content-Type: application/json" -d '{
  "action": "jobs.create",
  "api_key": "poi_xxx",
  "payload": {
    "country": "indonesia",
    "brand": "Alfamart",
    "level": "kecamatan",
    "scope": {"provinsi": ["Sumatera Utara"]},
    "mode": "unit",
    "tile": 0
  }
}'
```
Action lain: `jobs.get` (`payload.job_id`), `jobs.list`.

Respon sukses: `{"ok":true,"data":{...}}`; gagal: `{"ok":false,"error":"..."}`.

---

## Catatan & batasan
- GitHub Actions maksimum **6 jam/job**; scope besar otomatis dipecah **per provinsi** lalu digabung.
- Satu job berjalan sekaligus (concurrency `scrape`), sisanya mengantre.
- IP GitHub (Azure) lebih mudah diblokir Google → hasil best-effort (bukan jaminan 100%).
- Public repo: menit Actions gratis; kode + CSV admin publik.
- Google login opsional; bila `OAUTH_CLIENT_ID` kosong, gunakan API key.
