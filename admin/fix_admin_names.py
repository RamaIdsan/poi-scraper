"""Perbaiki nama wilayah CSV admin Indonesia (sekali jalan, idempoten).

- Prefix "Kabupaten "/"Kota " dari Kode Kota/Kab (bagian kedua >= 71 -> Kota)
- Perluasan: "Kota Adm. " -> "Kota Administrasi ", "Adm. Kep. Seribu", "Kep. " -> "Kepulauan "
- Buang baris kode rusak (mis. 52.0452.07)
- Rapikan kode lossy (.1 -> .10, .2 -> .20)
- Samakan varian kapitalisasi KECAMATAN (mis. "Kaway Xvi" -> "Kaway XVI")
- Regenerasi kolom gabungan Kecamatan & kota

Jalankan: python admin/fix_admin_names.py [path-csv-lain]
Backup asli: <nama-file>.backup.csv (sekali, tidak ditimpa)
"""
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poi_scraper as s  # noqa: E402

FILE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "data" / "Admin Indonesia - data admin indonesia.csv"
BACKUP = FILE.with_name(f"{FILE.stem}.backup{FILE.suffix}")


def canonical_kecamatan(variants, counts):
    def score(value):
        tokens = value.split()
        roman_up = any(re.fullmatch(r"[IVXL]+", token) for token in tokens)
        segments = [seg for seg in re.split(r"[-\s]+", value) if seg]
        seg_caps = sum(1 for seg in segments if seg[:1].isupper())
        return (roman_up, seg_caps, counts.get(value, 0))

    return max(variants, key=score)


def main():
    df = pd.read_csv(FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = ["Kode Kota/Kab", "KECAMATAN", "KOTA/KAB", "PROVINSI", "Kecamatan", "kota"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise SystemExit(f"Kolom hilang: {missing}")

    rows_before = len(df)
    kode = df["Kode Kota/Kab"].map(s.clean_text)

    bad = kode.str.count(r"\.") > 1
    if bad.any():
        print(f"Buang baris kode rusak: {int(bad.sum())} baris")
        print(df.loc[bad, ["Kode Kota/Kab", "KECAMATAN", "KOTA/KAB"]].drop_duplicates().to_string(index=False))
    df = df[~bad].copy()
    kode = kode[~bad]

    lossy = kode.str.fullmatch(r"\d{2}\.\d")
    if lossy.any():
        df.loc[lossy, "Kode Kota/Kab"] = kode[lossy].str.replace(
            r"^(\d{2})\.(\d)$", r"\1.\g<2>0", regex=True
        )
        kode = df["Kode Kota/Kab"].map(s.clean_text)
        print(f"Rapikan kode lossy: {int(lossy.sum())} baris ({kode[lossy].nunique()} kode unik)")

    before_names = df["KOTA/KAB"].copy()
    df["KOTA/KAB"] = s.normalize_kota_names(df["KOTA/KAB"], kode)
    changed = (before_names != df["KOTA/KAB"]).sum()
    print(f"Nama kota/kabupaten diperbaiki: {int(changed)} baris")

    rename = {}
    group = df.groupby([kode, df["KECAMATAN"].str.lower()])["KECAMATAN"]
    for (kode_val, _low), series in group:
        variants = series.unique()
        if len(variants) > 1:
            counts = series.value_counts().to_dict()
            best = canonical_kecamatan(variants, counts)
            for variant in variants:
                if variant != best:
                    rename[(kode_val, variant)] = best
    if rename:
        keys = list(zip(kode.tolist(), df["KECAMATAN"].tolist()))
        df["KECAMATAN"] = [rename.get((kode_val, kec), kec) for kode_val, kec in keys]
        print(f"Normalisasi kecamatan: {len(rename)} varian -> kanonik")
        for (kode_val, old), new in rename.items():
            print(f"   {kode_val}: {old!r} -> {new!r}")

    df["Kecamatan"] = df["KECAMATAN"] + ", " + df["KOTA/KAB"]
    df["kota"] = df["KOTA/KAB"] + ", " + df["PROVINSI"]

    part2 = pd.to_numeric(kode.str.extract(r"^\d{2}\.(\d+)")[0], errors="coerce")
    unprefixed_kab = part2.lt(71) & ~df["KOTA/KAB"].str.startswith("Kabupaten ")
    unprefixed_kota = part2.ge(71) & ~df["KOTA/KAB"].str.startswith("Kota ")
    print(f"Sisa kabupaten tanpa prefix: {int(unprefixed_kab.sum())}")
    print(f"Sisa kota tanpa prefix: {int(unprefixed_kota.sum())}")

    if not BACKUP.exists():
        shutil.copy2(FILE, BACKUP)
        print(f"Backup dibuat: {BACKUP.name}")
    else:
        print(f"Backup sudah ada (tidak ditimpa): {BACKUP.name}")

    df.to_csv(FILE, index=False, encoding="utf-8-sig")
    print(f"Tulis {FILE.name}: {rows_before} -> {len(df)} baris")
    print(f"Kabupaten: {int(df['KOTA/KAB'].str.startswith('Kabupaten ').sum())} baris, "
          f"Kota: {int(df['KOTA/KAB'].str.startswith('Kota ').sum())} baris")


if __name__ == "__main__":
    main()
