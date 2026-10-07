# Dokumentasi Teknis & Panduan Penggunaan — POI Scraper

Sistem scraping POI Google Maps untuk **Indonesia & Philippines** dengan dashboard
Google Apps Script dan worker **GitHub Actions** (gratis, tanpa server sendiri dan
tanpa laptop menyala).

---

## 1. Ringkasan sistem

```
Dashboard Apps Script (Web App)  ──►  Google Sheet (Users, Jobs, Logs, Result_*)
        │                                        ▲
        │ trigger (PAT)                          │ update progress/hasil
        ▼                                        │
GitHub Actions (Playwright)  ──►  Google Drive (CSV final)
```

- **Dashboard/API**: Google Apps Script Web App + Google Sheet + Google Drive.
- **Worker**: GitHub Actions menjalankan Playwright (scraping) di cloud.
- **Auth**: API key per user (SHA-256) **atau** Google login (opsional).
- **Hasil**: tab `Result_...` di spreadsheet (streaming) + file CSV di Drive.
- **Antrean**: maksimum `MAX_PARALLEL` (default 3) job jalan bersamaan.

---

## 2. Persiapan awal (sekali saja)

Lihat `README.md` bagian **Deploy**. Ringkasnya:

1. Push repo ke GitHub (`RamaIdsan/poi-scraper`).
2. Buat Google Sheet (`SHEET_ID`), folder Drive (`DRIVE_FOLDER_ID`), dan **service account**.
3. Share Sheet + folder ke email service account (Editor).
4. Set GitHub Secrets: `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `DRIVE_FOLDER_ID`, `SHARE_EMAIL`.
5. Buat **GitHub PAT** (Actions: write, Contents: read).
6. Di Apps Script: tempel `Code.gs`, `Index.html`, `appsscript.json`; set Script Properties
   (`SHEET_ID`, `GITHUB_PAT`); jalankan `setup()` → `bootstrapAdmin()`; Deploy Web App.
7. Salin **API KEY** dari Execution log.

> Setiap kali `Code.gs`/`Index.html` diubah: **Deploy → Manage deployments → Edit → New version**.

---

## 3. Panduan Dashboard (membuat job)

> **Tampilan baru (v2026-10-08.6)**: UI modern dengan Tailwind + Lucide, tema
> gelap/terang (ikut sistem + toggle), 4 kartu KPI, level target berupa kartu pilihan,
> multi-pilih wilayah berbentuk chip, modal **Preview**, **Logs**, dan **Job Detail**.
> Konfigurasi Lanjutan kini punya **Preset cakupan** (Cepat/Seimbang/Maksimal),
> **Radius geo-filter** (Auto/Manual 1–300 km), **Mode pencarian** (Persis/Mirip/Longgar),
> dan opsi **simpan listing tanpa koordinat**.

### 3.1 Negara
`Indonesia` atau `Philippines`. Menentukan data wilayah admin yang dipakai:
- Indonesia: Provinsi → Kota/Kabupaten → Kecamatan → Kelurahan (4 tingkat).
- Philippines: Province → City/Municipality → Barangay (3 tingkat, tanpa kecamatan).

### 3.2 Brand / kata kunci
Nama tempat yang dicari di Google Maps. Contoh: `Alfamart`, `Indomaret`, `Jollibee`.
Pencocokan bersifat lentur (mis. "Alfa Mart" tetap cocok dengan "Alfamart") dan kini
mencocokkan **nama ATAU kategori Google** — sehingga kata kunci generik seperti
`Traditional Market` / `Bus Station` tidak lagi membuang hasil.

**Alias**: tulis beberapa nama dipisah `|`, mis. `Alfamart | Alfa Mart | Alfamidi`.
Alias dipakai untuk **pencocokan** (query tetap satu) sehingga recall naik tanpa menambah waktu.

> Untuk kata kunci generik, sistem menyarankan mode **Longgar** atau **Mirip**.

### 3.3 Level target
**Tingkat wilayah yang dijadikan titik pencarian.** Sistem akan membuat satu target
pencarian untuk setiap unit pada level ini (di dalam wilayah yang Anda pilih).

| Level | Arti | Contoh query |
|---|---|---|
| Provinsi/Province | pencarian per provinsi | `Alfamart in Aceh Indonesia` |
| Kota/Kabupaten atau City/Municipality | per kota | `Alfamart in Bangued, Abra Philippines` |
| Kecamatan *(hanya Indonesia)* | per kecamatan | `Alfamart in Arongan Lambalek, Aceh Barat, Aceh Indonesia` |
| Kelurahan/Barangay | per kelurahan/barangay (paling detail) | `Alfamart in Angad, Bangued, Abra Philippines` |

**Pedoman:** makin kecil level, makin lengkap hasilnya (karena Google Maps membatasi
jumlah hasil per pencarian), tetapi makin lama.

### 3.4 Provinsi / Kota / Kecamatan (multi-pilih bertingkat)
Filter wilayah dengan **dropdown checkbox**:
- Klik kotak untuk membuka daftar; centang **satu atau lebih** unit.
- Ada **kotak cari** (daftar panjang) dan tombol **Pilih semua / Bersihkan**.
- Kosong = `(semua)` untuk level itu.
- Bertingkat: daftar Kota = gabungan kota dari provinsi terpilih; Kecamatan =
  gabungan dari (provinsi, kota) terpilih.
- Ganti pilihan induk → pilihan turunan otomatis direset.

Contoh scope multi-nilai:
```json
{"provinsi": ["Sumatera Utara", "Riau"], "kota": ["Kota Medan", "Kota Pekanbaru"]}
```
Catatan: nama kota dipakai apa adanya; bila dua provinsi terpilih punya kota bernama
sama, memilih nama itu berlaku untuk keduanya (kasus jarang).

### 3.5 Mode cakupan
| Mode | Arti | Kapan dipakai |
|---|---|---|
| **Unit terpilih** | 1 pencarian per unit level target yang dipilih | Cepat; cocok untuk level kecil |
| **Pecah ke unit terkecil** | Otomatis dipecah ke **seluruh kelurahan/barangay** di dalam wilayah pilihan | Hasil **paling lengkap**; cocok bila level target masih besar (provinsi/kota) |

### 3.6 Tile density
Menambah titik pencarian berbentuk grid pada tiap target (mengatasi batas hasil Google):
- `Off` = 1 pencarian per target.
- `3x3` = 9 pencarian per target.
- `5x5` = 25 pencarian per target.

Gunakan bila hasil terasa kurang lengkap, terutama di area padat. Semakin tinggi,
semakin lama dan semakin banyak permintaan.

### 3.7 Mulai Scraping
Job dibuat (status `queued`) dan file target disimpan otomatis. Job akan dipicu ke
GitHub Actions saat ada slot (lihat `MAX_PARALLEL`).

### 3.8 Contoh skenario
- **Semua kecamatan di Sumatera Utara**: Negara=Indonesia, Level=Kecamatan,
  Provinsi=Sumatera Utara, Mode=Unit terpilih.
- **Seluruh barangay di Kota Bangued** (paling lengkap): Negara=Philippines,
  Level=City/Municipality, Provinsi=Abra, Kota=Bangued, Mode=Pecah ke unit terkecil.
- **Cari di area padat**: tambahkan Tile density `3x3`.

> Dashboard kini menampilkan **perkiraan jumlah target × pencarian** tepat di atas
> tombol **Mulai Scraping** — berubah otomatis mengikuti pilihan Anda.

### 3.9 Contoh angka nyata

**Level target** — brand contoh `Alfamart` di **Sumatera Utara** (tanpa mempersempit kota):

| Level target | Jumlah target | Contoh yang dijalankan |
|---|---|---|
| Provinsi | **1** | `Alfamart in Sumatera Utara Indonesia` |
| Kota/Kabupaten | **33** | `Alfamart in Kota Medan, Sumatera Utara Indonesia` |
| Kecamatan | **455** | `Alfamart in Medan Amplas, Kota Medan, Sumatera Utara Indonesia` |
| Kelurahan | **6.109** | `Alfamart in Amplas, Medan Amplas, Kota Medan, Sumatera Utara Indonesia` |

**Philippines** — `Jollibee` di **Abra**:

| Level | Jumlah target | Contoh |
|---|---|---|
| Province | **1** | `Jollibee in Abra Philippines` |
| City/Municipality | **27** | `Jollibee in Bangued, Abra Philippines` |
| Barangay | **302** | `Jollibee in Angad, Bangued, Abra Philippines` |

**Dropdown wilayah mempersempit level target:**
- Level=Kecamatan + Provinsi=Sumatera Utara + Kota=`(semua)` → **455** target.
- Level=Kecamatan + Provinsi=Sumatera Utara + Kota=`Kota Medan` → **21** target.
- Level=Kelurahan + Kota=`Kota Medan` → **151** target.

**Mode cakupan** (contoh: Level=Kota/Kabupaten, Provinsi=Sumatera Utara, Kota=`(semua)`):

| Mode | Arti | Jumlah target |
|---|---|---|
| Unit terpilih | 1 pencarian per **kota/kabupaten** | **33** |
| Pecah ke unit terkecil | turun ke **semua kelurahan** se-Sumut | **6.109** |

Contoh PH (Level=City, Provinsi=Abra, Kota=Bangued): Unit=**1** target; Expand=**30** target barangay.

**Tile density** (pengali pencarian per target):

| Tile | Pencarian/target | 21 target (kecamatan Medan) | 6.109 target (kelurahan Sumut) |
|---|---|---|---|
| Off | 1 | 21 | 6.109 |
| 3x3 | 9 | 189 | 54.981 |
| 5x5 | 25 | 525 | 152.725 |

Gunakan tile hanya bila hasil kurang lengkap — semakin besar, semakin lama dan
semakin besar risiko rate-limit Google.

---

## 4. Status job & kolom monitoring

| Status | Arti |
|---|---|
| `queued` | Menunggu antrean (belum dipicu) |
| `dispatched` | Sedang dikirim ke worker GitHub |
| `running` | Sedang scraping |
| `done` | Selesai; hasil tersedia |
| `failed` | Gagal; lihat tooltip **err** |

Kolom di tabel **Job Saya**:

| Kolom | Arti |
|---|---|
| Brand | Kata kunci job |
| User | Pemilik job (admin melihat semua) |
| Status | Lihat tabel status di atas |
| Progress | Untuk job ber-chunk: `Chunk x/y · z%` (progress **gabungan** dari seluruh chunk) |
| Target | Wilayah yang sedang diproses |
| Records | Jumlah POI yang sudah tersimpan |
| ETA | Perkiraan sisa waktu |
| Aksi | **Logs** · **Tab** · **Preview** · **CSV** · **Detail** |

Tombol **Detail** membuka modal berisi: status, progress total, posisi chunk,
records/listings, ETA, **statistik filter gabungan semua chunk**
(relevance/geo/duplicates dst.), daftar **target 0 hasil**, **coverage per wilayah**
(chunk selesai + bar + jumlah Zero per provinsi), **fill-rate kolom** (contoh 500 baris:
phone/website/hours/payment/building dst.), dan tombol
**Ulangi hanya target 0 hasil** (membuat job baru khusus target yang kosong).

**Notifikasi**: tombol lonceng di topbar mengaktifkan notifikasi browser; toast
otomatis muncul saat job berubah ke `done`/`failed`/`paused`/`cancelled`.

> Dedup hasil memakai **Place_ID** lebih dulu (antar-chunk), lalu fallback
> brand/nama/alamat/koordinat. Pemanggilan Sheets API di worker di-cache agar
> hemat kuota saat beberapa job paralel.

Tabel menyegar otomatis setiap 10 detik.

### 4.1 Kontrol job (Stop / Pause / Resume)
Tombol kontrol muncul pada baris job sesuai status:
- **Pause** (job `running`): worker memeriksa kolom `control` tiap ±15–30 dtk, lalu berhenti
  **rapi** setelah target yang sedang berjalan. Status jadi `paused`.
- **Stop/Cancel** (job `running`/`queued`): membatalkan run di GitHub + status `cancelled`.
  Data yang sudah ter-stream tetap aman di tab hasil.
- **Resume** (job `paused`/`cancelled`/`failed`): menjalankan ulang job; chunk yang sudah
  `done` dilewati, dan hasil lama di tab digabung + dedup saat merge, sehingga tidak hilang.

Progres gabungan memakai kolom `overall_progress` (dihitung dari posisi chunk), jadi label
`Chunk x/y · z%` mencerminkan total, bukan 100% per chunk.

---

## 5. Di mana hasil disimpan?

1. **Tab `Result_<brand>_<tanggal>_<job8>`** di spreadsheet yang sama.
   - Selama scraping, baris **mengalir (streaming)** ke tab sehingga bisa dipantau.
   - Saat selesai, job *merge* menulis ulang tab dengan data **bersih (dedup)**.
   - Bisa ada **duplikat sementara** selama proses, hilang saat selesai.
2. **File CSV final** di folder Google Drive (`Output_<brand>_<job8>.csv`).
   - Dibuat oleh **Apps Script** (akun Anda) via `exportCompleted()` karena service
     account tidak punya kuota Drive pribadi. Trigger berjalan tiap 5 menit.
   - Link tersedia di kolom **csv**. Bila belum siap, tombol **csv** otomatis memakai
     export URL dari tab hasil sehingga tetap bisa diunduh.
3. **Tab `Logs`**: catatan milestone (mulai chunk, selesai, error).
4. **Log lengkap** ada di GitHub Actions (tombol **log**).
5. **Kolom output tambahan** (untuk audit): `Place_ID`, `Country`, `Level`,
   `Query_Target`, `Source_URL`, `Scraped_At`.

> Batas Google Sheets: **10 juta sel/spreadsheet**. Untuk hasil sangat besar, gunakan
> CSV Drive.

---

## 6. Multi-user & API key

- **Admin** (email di `ADMIN_EMAILS`) melihat **semua** job dan mengelola user.
- **User biasa** melihat **job miliknya** saja di dashboard.
- Membuat user: panel **Admin - API Key** → isi email + kuota (`0` = unlimited) →
  **Buat / Reset API Key**. Kunci ditampilkan dan bisa disalin ulang kapan saja.
- Tabel user punya aksi: **Copy key** (tampilkan key tersimpan), **Kuota** (ubah tanpa
  reset), **Reset** (buat key baru), dan **Aktifkan/Nonaktifkan**.
- API key disimpan **plaintext** di kolom `Users.api_key` (Sheet hanya diakses owner +
  service account) agar bisa disalin kembali; hash tetap dipakai untuk validasi.
- Saat key dibuat, spreadsheet otomatis di-share sebagai **Viewer** ke email user
  (sehingga mereka bisa membuka tab hasil — namun secara teknis bisa melihat semua tab).
- Kuota dihitung per job yang dibuat.

### Fungsi Apps Script yang berguna (jalankan dari editor)
| Fungsi | Fungsi |
|---|---|
| `setup()` | Sinkron header tab + isi default properti |
| `bootstrapAdmin()` | Membuat/mengembalikan API key admin (tampil di log) |
| `listApiUsers()` | Daftar user + kuota/pemakaian |
| `hashApiKey(key)` | Hash sebuah key (debug) |
| `setAdminEmails("a@x,b@y")` | Ganti daftar admin |
| `setMaxParallel(n)` | Atur jumlah job paralel |
| `setDriveFolder("id_folder")` | Set folder Drive untuk ekspor CSV |
| `admin.getKey(email)` (UI) | Tampilkan/ salin API key user (plaintext) |
| `admin.setQuota(email, quota)` (UI) | Ubah kuota tanpa reset key |
| `exportCompleted()` | Ekspor CSV semua job `done` yang belum punya `output_url` |
| `exportJobCsv("job_id")` | Ekspor CSV satu job tertentu |
| `setupTriggers()` | Pasang trigger dispatcher (1 mnt) + exporter CSV (5 mnt) |

---

## 7. API program (akses eksternal)

POST JSON ke URL Web App Apps Script:

```bash
curl -s -X POST "$WEBAPP_URL" -H "Content-Type: application/json" -d '{
  "action": "jobs.create",
  "api_key": "poi_xxx",
  "payload": {
    "country": "indonesia",
    "brand": "Alfamart",
    "level": "kecamatan",
    "scope": {"provinsi": ["Sumatera Utara", "Riau"]},
    "mode": "unit",
    "tile": 0
  }
}'
```

> `scope` menerima **array** — bisa banyak provinsi/kota/kecamatan, mis.
> `{"provinsi":["Sumatera Utara","Riau"],"kota":["Kota Medan","Kota Pekanbaru"]}`.

| Action | Payload | Keterangan |
|---|---|---|
| `jobs.create` | lihat contoh | Membuat job, mengembalikan `job_id` |
| `jobs.get` | `job_id` | Detail satu job (milik sendiri / admin) |
| `jobs.list` | – | Daftar job (milik sendiri / admin) |
| `jobs.preview` | `job_id`, `limit` | Sampel baris tab hasil (untuk modal Preview) |
| `jobs.logs` | `job_id`, `limit` | Log job dari tab `Logs` |
| `jobs.stats` | – | Agregat KPI (done/active/records) |
| `jobs.pause` | `job_id` | Minta pause (berhenti rapi setelah target berjalan) |
| `jobs.resume` | `job_id` | Lanjutkan job paused/cancelled/failed (lewati chunk selesai) |
| `jobs.cancel` | `job_id` | Batalkan run di GitHub + status `cancelled` |
| `jobs.rerunZero` | `job_id` | Buat job baru hanya untuk target 0 hasil |
| `admin.createKey` | `email`, `quota` | Hanya admin (buat/reset key) |
| `admin.getKey` | `email` | Hanya admin; ambil API key untuk disalin |
| `admin.setQuota` | `email`, `quota` | Hanya admin; ubah kuota tanpa reset key |
| `admin.setActive` | `email`, `active` | Hanya admin; aktif/nonaktifkan user |
| `admin.users` | – | Hanya admin |
| `admin.diag` | – | Hanya admin; diagnostik spreadsheet |

Respon sukses: `{"ok":true,"data":{...}}` · gagal: `{"ok":false,"error":"..."}`.

---

## 8. Cara kerja di balik layar

1. Dashboard membuat baris `Jobs` + tab hasil kosong → `queued`.
2. Trigger per menit / submit memanggil `dispatch()` yang memicu workflow GitHub
   untuk job `queued` selama slot < `MAX_PARALLEL`.
3. GitHub Actions:
   - `plan_chunks.py` menghitung target & memecah per provinsi bila besar.
   - Untuk tiap chunk: `run_job.py` menjalankan scraper, **streaming** baris ke tab
     hasil, menulis `progress/current_target/listings_found/records/eta/run_url` dan `Logs`.
     Semua penulisan Sheets dilakukan lewat **thread latar (non-blocking)** agar proses
     scraping tidak menunggu jaringan (hemat ±2–7% waktu).
   - `merge_parts.py` menggabungkan semua chunk, dedup, menulis ulang tab, upload CSV Drive,
     set `status=done`.
4. Dashboard memantau lewat kolom-kolom tersebut.

**Judul job** diberi nama `Result_<brand>_<YYYYMMDD_HHMM>_<job8>` agar mudah dikenali.

---

## 9. Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| **"API key tidak valid"** | Tab `Users` kosong. Jalankan `bootstrapAdmin()` di editor, salin key dari Execution log. |
| **Tidak bisa login sama sekali (deadlock)** | Panel Admin hanya muncul setelah login; key pertama harus dari `bootstrapAdmin()`. |
| **"Google login belum dikonfigurasi"** | `OAUTH_CLIENT_ID` kosong. Isi untuk pakai login Google, atau pakai API key. |
| **"Job Saya: Gagal ..."** | Klik **Diagnostik** (panel Admin) lalu kirim hasilnya. Umumnya header tab belum sinkron → jalankan `setup()`. |
| **Job lama `queued`** | Antrean penuh atau trigger belum jalan. Jalankan `setupTriggers()`, atau panggil `dispatch()` manual. |
| **Job `failed`** | Lihat tombol **log** (GitHub Actions) dan tab `Logs`. |
| **Hasil kosong / sedikit** | Perkecil level target, pakai **Pecah ke unit terkecil**, atau naikkan **Tile density**. Google juga bisa memblokir IP GitHub. |
| **Tab hasil tidak terisi** | Pastikan service account masih **Editor** di spreadsheet; cek tab `Logs`. Catatan: fitur streaming/kolom detail hanya jalan jika **repo GitHub sudah di-push** (worker terbaru). |
| **`WARN streaming gagal: ... struct_value`** | Nilai non-skalar (mis. jam operasional) terkirim ke Sheets. Sudah diperbaiki: jam disimpan sebagai JSON string + sanitasi sel di worker. Push & redeploy worker terbaru. |
| **`Gagal memuat detail: jobs.get gagal: null`** | Baris lama lebih pendek dari header → nilai `undefined` tidak bisa diserialisasi. Sudah diperbaiki (`readTable_` mengisi sel kosong + `getJob_` disanitasi). Redeploy Apps Script. |
| **`output_url` kosong / CSV tidak ada** | Penyebab umum: **service account tidak punya kuota Drive pribadi**. Solusi: CSV diekspor oleh **Apps Script** (akun Anda). Set `DRIVE_FOLDER_ID` (Script Property / `setDriveFolder(id)`) dan pastikan trigger `exportCompleted` aktif (`setupTriggers()`), atau jalankan `exportCompleted()` manual. Tombol **csv** di dashboard juga bisa mengunduh langsung dari tab hasil (export URL) sebelum file Drive siap. |
| **Progress format lama `x% (n records) \| a/b`** | Worker di GitHub masih versi lama → `git push origin main` lalu jalankan job baru. |
| **Perubahan kode tidak muncul** | Redeploy: **Deploy → Manage deployments → Edit → New version**. |

---

## 10. Batasan & catatan

- GitHub Actions: maksimum **6 jam/job**; scope besar otomatis dipecah per provinsi.
- Google Sheets: **10 juta sel/spreadsheet**.
- IP GitHub (Azure) lebih mudah diblokir Google → hasil bersifat **best-effort**.
- Streaming memunculkan duplikat sementara; dibersihkan saat selesai.
- **Jam operasional**: kosong = sel kosong; `{"24h":true}` = buka 24 jam;
  `{"closed":true}` = tutup sementara/permanen.
- **Listing tanpa koordinat** tetap diambil (lat/long kosong sebagai penanda) —
  default `keep_no_coords=true`.
- Paralel 3 (default) menaikkan peluang rate-limit; bisa diturunkan via `setMaxParallel(n)`.
- Public repo: menit Actions gratis; kode + CSV admin publik.

---

## 11. Struktur repo

```
poi_scraper.py         # scraper + job-mode
admin/build_index.py   # generator index dropdown
admin/id_index.json    # index Indonesia (provinsi/kota/kecamatan)
admin/ph_index.json    # index Philippines (province/city)
worker/plan_chunks.py  # pecah spec -> chunk provinsi
worker/run_job.py      # jalankan 1 chunk + streaming + update Sheet
worker/merge_parts.py  # gabung + dedup + tab hasil + Drive
worker/cloud.py        # helper Sheets & Drive
.github/workflows/scrape.yml
data/                  # CSV admin
apps-script/           # Code.gs, Index.html, appsscript.json
DOKUMENTASI.md         # file ini
README.md              # panduan deploy
```
