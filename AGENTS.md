# AGENTS.md — POI Scraper Studio (handoff antar-session)

Dokumen ini untuk agent/AI (dan manusia) agar pekerjaan bisa dilanjutkan lintas session
tanpa kehilangan konteks. **Baca ini dulu sebelum mengubah apa pun.**

## Ringkasan sistem
Sistem scraping POI Google Maps untuk **Indonesia & Philippines**, dijalankan di cloud
(GitHub Actions), dikendalikan dari **dashboard Google Apps Script**, hasil disimpan ke
**Google Sheet (tab per job) + Google Drive (CSV)**.

```
Apps Script Web App (dashboard + API)  ──►  Google Sheet (Users, Jobs, Logs, Result_*)
        │ trigger (PAT)                              ▲
        ▼                                           │ update progress/hasil
GitHub Actions: plan → scrape (matrix per provinsi) → merge
        │
        └──► Google Drive (CSV final)
```

## Peta file
| File | Peran |
|---|---|
| `poi_scraper.py` | Engine scraping + job-mode (`run_job`, `spec_to_targets`, `plan_chunks`). Semua filter: relevansi (nama ATAU kategori + alias), radius geo, dedup, jam, phone, dll. |
| `worker/plan_chunks.py` | Baca spec → output `idxs` + `entries` (chunk per provinsi × batch ±80 target, `{idx,chunk,total,prov,start,end}`) + `all_entries` (untuk audit cakupan). |
| `worker/run_job.py` | Jalankan 1 chunk (scope provinsi + irisan target): stream ke tab, progress, kontrol pause/stop, writer thread non-blocking, resume per-target (kolom `Query_Target`). |
| `worker/merge_parts.py` | Gabung artifact + baris tab, dedup (Place_ID → fallback), audit cakupan target, tulis tab final, upload Drive, tandai status. |
| `worker/cloud.py` | Helper Sheets/Drive (service account), cache row/tab, sanitasi sel, RLock. |
| `.github/workflows/scrape.yml` | Workflow: plan → scrape (matrix `idx`) → merge. |
| `apps-script/Code.gs` | API + DB (Users/Jobs/Logs), dispatch, kontrol job, export CSV, notifikasi email. |
| `apps-script/Index.html` | Dashboard (Tailwind+Lucide), multi-pilih wilayah, preset, modal, mobile tabs. |
| `admin/build_index.py` | Generate `id_*.json`/`ph_*.json` untuk dropdown + `counts` (estimasi target). |
| `admin/fix_admin_names.py` | Perbaiki nama wilayah CSV admin Indonesia (prefix Kabupaten/Kota, abreviasi, kode lossy, varian kecamatan). Sekali jalan, idempoten. |
| `data/*.csv` | CSV admin (Indonesia & Philippines). |
| `DOKUMENTASI.md` | Panduan pengguna lengkap. |
| `README.md` | Panduan deploy. |

## Konvensi penting
- UI & pesan **bahasa Indonesia**; kode tanpa komentar berlebihan (kecuali blok penjelas).
- Output CSV: `OUTPUT_COLUMNS` (20 kolom; 6 kolom terakhir: `Place_ID, Country, Level,
  Query_Target, Source_URL, Scraped_At`). **Jangan mengubah urutan 14 kolom pertama** —
  dedup legacy memakai indeks `row[2]/[4]/[5]/[7]/[9]/[10]`.
- `Jobs` header di `Code.gs` **harus sama urutannya** dengan `JOB_COLUMNS` di `worker/cloud.py`.
  Setelah mengubah header → jalankan `setup()` di Apps Script (sinkron header).
- Spec job: `{job_id,user,country,brand,aliases?,level,scope,mode,tile,params:{radius_km?,
  relevance_mode?,keep_no_coords?,filter_relevance?},result_sheet,explicit_admins?,resume_done_chunks?}`.
- `result_sheet` = tab `Result_<brand>_<stamp>_<job8>`; streaming mengisi bertahap, merge
  menulis ulang final (dedup). Resume **tidak** membuat tab/CSV baru — semua menuju tab yang sama.
- Kontrol: `Jobs.control` = `run|pause|stop`; worker cek tiap ~15 dtk.
- Untuk job besar: `plan_chunks` bisa mengembalikan (chunks, counts) dengan pecahan `chunk_size=80`.
  Uji: `plan_chunks` Jabar level=kecamatan → 8 chunk; guard matrix ≤200 chunk (GitHub maks 256).
- Status chunk di `chunks_json`: `running|done|partial|paused|cancelled|failed`. `partial` = ada
  target gagal → tidak dilewati saat Resume. Resume per-target memakai kolom `Query_Target` (kolom R)
  tab hasil: target yang ada barisnya dilewati, target terakhir diulang, sisanya dikerjakan.
- Audit cakupan di merge: `expected` (dari `entries`/`all_entries`) vs `present` + `zero_targets_json`;
  bila target dari chunk yang dijalankan belum terverifikasi → `status=failed` + pesan agar Resume.
- Nama wilayah Indonesia **wajib berprefix** `Kabupaten `/`Kota ` (mis. `Kabupaten Tangerang`),
  diturunkan dari `Kode Kota/Kab` (segmen kedua ≥ 71 → Kota). `load_admin_data` menormalkan
  otomatis (safety net) dan `admin/fix_admin_names.py` merapikan CSV-nya. `resolve_admin_path`
  memilih file pertama yang ada dari CWD → jalankan engine dari **dalam repo** agar memakai `data/` repo.

## Perintah verifikasi (WAJIB sebelum commit)
```powershell
# dari folder root project (yang berisi folder poi-scraper)
& ".\venv\Scripts\python.exe" -m py_compile "poi-scraper\poi_scraper.py" "poi-scraper\worker\run_job.py" "poi-scraper\worker\merge_parts.py" "poi-scraper\worker\plan_chunks.py" "poi-scraper\worker\cloud.py" "poi-scraper\admin\build_index.py" "poi-scraper\admin\fix_admin_names.py"
# JS: ekstrak blok <script> dari Index.html lalu node --check; Code.gs juga node --check
```
- Cek YAML: `python -c "import yaml;yaml.safe_load(open('poi-scraper/.github/workflows/scrape.yml',encoding='utf-8'))"`.
- Uji cepat target: `spec_to_targets` + `plan_chunks` untuk skenario (mis. level=kota,
  semua provinsi → 516 target, 38 chunk).

## Deploy (setiap perubahan)
1. `git -C poi-scraper push origin main` (repo **public**, owner `RamaIdsan`).
2. Apps Script: replace `Code.gs` + `Index.html` → Save.
3. Jika header `Jobs`/`Users` berubah → jalankan `setup()`.
4. **Deploy → Manage deployments → Edit → Version: New version**.
5. Hard refresh dashboard; cek versi di header (`· vYYYY-MM-DD.x`).
Catatan: perubahan **worker/workflow saja** (bukan header/`Code.gs`) cukup langkah 1 (push).

## Status saat ini
- Versi aplikasi terakhir: **v2026-10-09.2** (mobile viewport + force-mobile + notifikasi toast).
- Matrix workflow **diubah ke 1 kunci** (`idx`) + `name` eksplisit karena error GitHub
  "Unable to create a unique name" pada matrix multi-kunci. Merge tahan tanpa artifact.
- Scraper: target gagal **dilewati** (bukan abort); abort hanya bila 8 gagal berturut-turut.
- Notifikasi browser tidak mungkin di sandbox Apps Script → pakai toast (+ email opsional via `exportCompleted`).
- **Anti-timeout (fix Jabar/Jateng 5j50m)**: chunk = per provinsi × batch **±80 target**
  (`chunk_label` unik), `max-parallel: 3`, `timeout-minutes: 360`, guard matrix ≤200 chunk,
  actions di-bump ke node24 (`checkout@v7`, `setup-python@v7`, `upload/download-artifact@v7`,
  `cache@v6`) + cache pip & browser Playwright. Resume per-target via kolom `Query_Target`
  + audit cakupan di merge (target belum terverifikasi → `failed` + ajakan Resume).
  Uji: Jabar kecamatan 627 target → 8 chunk; slice gabungan == 627 target (verified).
- **Nama wilayah Indonesia dibakukan**: 78.200 baris kabupaten berprefix `Kabupaten `, DKI
  diperluas (`Kota Administrasi …`, `Kabupaten Administrasi Kepulauan Seribu`), 1 baris kode
  rusak `52.0452.07` dibuang, 24 kode lossy (`.1/.2` → `.10/.20`) dirapikan, 3 varian kecamatan
  diseragamkan. Index kini 38 provinsi / **516 kota**. Duplikat stale (Sorong, Deiyai, Nabire,
  Paniai) **sengaja disimpan** (tampil di dua provinsi; dedup Place_ID tetap mencegah duplikat baris).

## Known issues / backlog
- Kelengkapan Google Maps tetap best-effort (cap ±120/query); IP GitHub bisa diblokir.
- Sheets 10 juta sel; `previewJob_`/`exportCompleted` membaca seluruh tab (perlu optimasi bila >50k baris).
- Dedup "first wins" (belum merge-field/record terbaik).
- Keamanan: spreadsheet di-share Viewer ke user → tab `Users` berisi API key plaintext (P0).
- Stuck job belum ada heartbeat/timeout otomatis.
- Scope raksasa (mis. expand seluruh Indonesia = 83.422 target) tetap berat: ±200 chunk ×
  (±4-5 jam di cap guard) — sebaiknya pecah per pulau/provinsi dari dashboard.
- `jobs.get` mengembalikan baris mentah (sudah disanitasi di `getJob_`).

## Catatan lintas-session
Jika memulai session baru: baca `AGENTS.md` ini + `DOKUMENTASI.md`, lalu jalankan
`git -C poi-scraper log --oneline -5` dan `git -C poi-scraper status` untuk tahu posisi.
Semua keputusan besar (negara ganda, chunk, kontrol job, UI mobile) sudah terdokumentasi.
