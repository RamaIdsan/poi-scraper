import argparse
import csv
import json
import logging
import math
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# CONFIGURATION / OUTPUT COLUMNS
# ============================================================
OUTPUT_COLUMNS = [
    "Brand",
    "Admin",
    "Name_geocode",
    "Local_Name_geocode",
    "Category_geocode",
    "Address_geocode",
    "Building_geocode",
    "Phone_Number_geocode",
    "Operating_Hours_geocode",
    "Latitude_geocode",
    "Longitude_geocode",
    "Business_Status_geocode",
    "Website_geocode",
    "Payment_geocode",
    "Place_ID",
    "Country",
    "Level",
    "Query_Target",
    "Source_URL",
    "Scraped_At",
]

DAY_MAP = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}

# Preset grup pulau Indonesia untuk mempercepat pemilihan provinsi.
INDONESIA_ISLANDS = {
    "Sumatera": [
        "Aceh", "Sumatera Utara", "Sumatera Barat", "Riau", "Kepulauan Riau",
        "Jambi", "Bengkulu", "Sumatera Selatan", "Kepulauan Bangka Belitung", "Lampung",
    ],
    "Jawa": ["DKI Jakarta", "Jawa Barat", "Banten", "Jawa Tengah", "Daerah Istimewa Yogyakarta", "Jawa Timur"],
    "Kalimantan": [
        "Kalimantan Barat", "Kalimantan Tengah", "Kalimantan Selatan",
        "Kalimantan Timur", "Kalimantan Utara",
    ],
    "Sulawesi": [
        "Sulawesi Utara", "Gorontalo", "Sulawesi Tengah", "Sulawesi Barat",
        "Sulawesi Selatan", "Sulawesi Tenggara",
    ],
    "Bali & Nusa Tenggara": ["Bali", "Nusa Tenggara Barat", "Nusa Tenggara Timur"],
    "Maluku": ["Maluku", "Maluku Utara"],
    "Papua": [
        "Papua", "Papua Barat", "Papua Selatan", "Papua Tengah",
        "Papua Pegunungan", "Papua Barat Daya",
    ],
}

# Preset grup pulau Philippines untuk mempercepat pemilihan provinsi.
PHILIPPINES_ISLANDS = {
    "Luzon": [
        "Abra", "Albay", "Apayao", "Aurora", "Bataan", "Batanes", "Batangas",
        "Benguet", "Bulacan", "Cagayan", "Camarines Norte", "Camarines Sur",
        "Catanduanes", "Cavite", "Ifugao", "Ilocos Norte", "Ilocos Sur",
        "Isabela", "Kalinga", "La Union", "Laguna", "Marinduque", "Masbate",
        "Metro Manila", "Mountain Province", "Nueva Ecija", "Nueva Vizcaya",
        "Occidental Mindoro", "Oriental Mindoro", "Palawan", "Pampanga",
        "Pangasinan", "Quezon", "Quirino", "Rizal", "Romblon", "Sorsogon",
        "Tarlac", "Zambales",
    ],
    "Visayas": [
        "Aklan", "Antique", "Biliran", "Bohol", "Capiz", "Cebu", "Eastern Samar",
        "Guimaras", "Iloilo", "Leyte", "Negros Occidental", "Negros Oriental",
        "Northern Samar", "Samar", "Siquijor", "Southern Leyte",
    ],
    "Mindanao": [
        "Agusan del Norte", "Agusan del Sur", "Basilan", "Bukidnon", "Camiguin",
        "Compostela Valley", "Davao Oriental", "Davao del Norte", "Davao del Sur",
        "Dinagat Islands", "Lanao del Norte", "Lanao del Sur", "Maguindanao",
        "Misamis Occidental", "Misamis Oriental", "North Cotabato", "Sarangani",
        "South Cotabato", "Sultan Kudarat", "Sulu", "Surigao del Norte",
        "Surigao del Sur", "Tawi-Tawi", "Zamboanga Sibugay", "Zamboanga del Norte",
        "Zamboanga del Sur",
    ],
}

# ============================================================
# COUNTRY PROFILES
# ============================================================
COUNTRY_PROFILES = {
    "indonesia": {
        "label": "Indonesia",
        "file_tag": "Indonesia",
        "admin_file": "Admin Indonesia - data admin indonesia.csv",
        "timezone": "Asia/Jakarta",
        "query_suffix": "Indonesia",
        "levels": ["provinsi", "kota", "kecamatan", "kelurahan"],
        "level_labels": {
            "provinsi": "Provinsi",
            "kota": "Kota/Kabupaten",
            "kecamatan": "Kecamatan",
            "kelurahan": "Kelurahan",
        },
        "column_map": {
            "provinsi": "PROVINSI",
            "kota": "KOTA/KAB",
            "kecamatan": "KECAMATAN",
            "kelurahan": "KkELURAHAN",
        },
        "radius_by_level": {
            "provinsi": 150.0,
            "kota": 40.0,
            "kecamatan": 15.0,
            "kelurahan": 5.0,
        },
        "islands": INDONESIA_ISLANDS,
        "accept_terms": {
            "indonesia", "jakarta", "surabaya", "bandung", "medan", "semarang",
            "makassar", "palembang", "denpasar", "yogyakarta", "bekasi", "depok",
            "tangerang", "bogor", "malang", "padang", "pekanbaru", "balikpapan",
            "samarinda", "pontianak", "manado", "batam", "aceh", "riau", "jambi",
            "bengkulu", "lampung", "banten", "jawa", "bali", "kalimantan",
            "sulawesi", "papua", "maluku", "gorontalo",
        },
        "reject_terms": {
            "philippines", "manila", "davao", "cebu", "quezon", "luzon",
            "visayas", "mindanao", "taguig", "makati", "pasig", "cagayan",
            "batangas", "baguio", "iloilo", "bacolod", "zamboanga",
        },
    },
    "philippines": {
        "label": "Philippines",
        "file_tag": "Philippines",
        "admin_file": "Admin Philippines - data admin philippines.csv",
        "timezone": "Asia/Manila",
        "query_suffix": "Philippines",
        "levels": ["provinsi", "kota", "kelurahan"],
        "level_labels": {
            "provinsi": "Province",
            "kota": "City/Municipality",
            "kelurahan": "Barangay",
        },
        "column_map": {
            "provinsi": "Province",
            "kota": "City_Municipality",
            "kelurahan": "Barangay",
        },
        "radius_by_level": {
            "provinsi": 150.0,
            "kota": 40.0,
            "kelurahan": 5.0,
        },
        "islands": PHILIPPINES_ISLANDS,
        "accept_terms": {
            "philippines", "manila", "davao", "cebu", "quezon", "luzon",
            "visayas", "mindanao", "taguig", "makati", "pasig", "cagayan",
            "batangas", "baguio", "iloilo", "bacolod", "zamboanga", "pampanga",
            "laguna", "cavite", "bulacan", "pampanga", "bohol", "leyte", "samar",
            "palawan", "bataan", "rizal", "pampanga", "albay", "isabela",
        },
        "reject_terms": {
            "indonesia", "jakarta", "surabaya", "bandung", "medan", "semarang",
            "makassar", "palembang", "denpasar", "yogyakarta", "bekasi", "depok",
            "tangerang", "bogor", "malang", "padang", "pekanbaru", "balikpapan",
        },
    },
}


def get_profile(country):
    key = clean_text(country).lower()
    if key not in COUNTRY_PROFILES:
        key = "indonesia"
    return COUNTRY_PROFILES[key]


def columns_by_level(profile):
    """Return {level: [leaf_internal_col, ..., province_internal_col]} for a profile."""
    levels = profile["levels"]
    result = {}
    for i, lvl in enumerate(levels):
        result[lvl] = list(reversed(levels[: i + 1]))
    return result


# ============================================================
# GENERAL HELPERS
# ============================================================
def clean_text(value):
    if value is None:
        return ""
    value = str(value)
    value = value.replace("\u202f", " ").replace("\u200e", " ").replace("\u200f", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_for_match(value):
    value = clean_text(value).lower()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def format_time(seconds):
    hrs, remainder = divmod(max(0, seconds), 3600)
    mins, secs = divmod(remainder, 60)
    return f"{int(hrs):02}:{int(mins):02}:{int(secs):02}"


def print_progress(current, total, start_time, scraped, duplicates, errors, current_admin=""):
    progress = (current / total) * 100 if total else 100
    elapsed = time.time() - start_time
    eta = (elapsed / current) * (total - current) if current > 0 else 0
    msg = (
        f"\rProgress: {current}/{total} ({progress:.2f}%) | "
        f"Elapsed: {format_time(elapsed)} | ETA: {format_time(eta)} | "
        f"Scraped: {scraped} | Duplicate: {duplicates} | Errors: {errors}"
    )
    if current_admin:
        msg += f" | Current: {clean_text(current_admin)[:70]}"
    sys.stdout.write(msg[:220])
    sys.stdout.flush()


def canonical_url(url):
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except Exception:
        return url.split("#", 1)[0]


def extract_lat_long_from_url(url):
    if not url:
        return None, None

    patterns = [
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
        r"@(-?\d+(?:\.\d+)?),(-?\d+\.?\d*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            try:
                latitude = float(match.group(1))
                longitude = float(match.group(2))
                if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    return latitude, longitude
            except (TypeError, ValueError):
                pass

    return None, None


def extract_place_id(url):
    """Extract Google's stable feature id from a Maps URL (e.g. !1s0x...:0x...)."""
    if not url:
        return ""
    match = re.search(r"!1s([^!&?]+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/maps/place/[^/]+/([^/?#]+)", url)
    if match and match.group(1).startswith("data="):
        return match.group(1)
    return ""


def clean_time_format(time_str):
    clean_time = clean_text(time_str)
    match = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)$", clean_time)
    if not match:
        return clean_time

    hours, minutes, period = match.groups()
    hours = int(hours)
    minutes = int(minutes)

    if period.lower() == "pm" and hours != 12:
        hours += 12
    elif period.lower() == "am" and hours == 12:
        hours = 0

    return f"{hours:02}:{minutes:02}"


def safe_inner_text(locator, timeout=1500):
    try:
        if locator.count() == 0:
            return ""
        return clean_text(locator.first.inner_text(timeout=timeout))
    except Exception:
        return ""


def safe_attribute(locator, attribute, timeout=1500):
    try:
        if locator.count() == 0:
            return ""
        return clean_text(locator.first.get_attribute(attribute, timeout=timeout))
    except Exception:
        return ""


def first_text(page, selectors, timeout=1500):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                text = safe_inner_text(locator, timeout=timeout)
                if text:
                    return text
        except Exception:
            continue
    return ""


def first_attribute(page, selectors, attribute, timeout=1500):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                value = safe_attribute(locator, attribute, timeout=timeout)
                if value:
                    return value
        except Exception:
            continue
    return ""


def grid_points(lat, lon, tile_factor, step_km):
    """Generate a square grid of coordinates around (lat, lon)."""
    if not tile_factor or lat is None or lon is None:
        return [(lat, lon)]

    lat_km = 110.574
    lon_km = 111.320 * math.cos(math.radians(lat))
    if abs(lon_km) < 1e-6:
        lon_km = 1.0

    points = []
    for dy in range(-tile_factor, tile_factor + 1):
        for dx in range(-tile_factor, tile_factor + 1):
            points.append((lat + dy * step_km / lat_km, lon + dx * step_km / lon_km))
    return points


# ============================================================
# ADMIN HIERARCHY (generic, per country profile)
# ============================================================
def resolve_admin_path(filename):
    """Cari file admin di beberapa lokasi (root, folder data/)."""
    candidates = [
        Path(filename),
        Path("data") / filename,
        Path(__file__).resolve().parent / filename,
        Path(__file__).resolve().parent / "data" / filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"File data admin tidak ditemukan: {filename}")


def load_admin_data(profile):
    filename = profile["admin_file"]
    path = resolve_admin_path(filename)

    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    column_map = profile["column_map"]
    missing = [src for src in column_map.values() if src not in df.columns]
    if missing:
        raise ValueError(
            f"Kolom admin tidak lengkap untuk {profile['label']}. Hilang: {missing}. "
            f"Kolom yang ada: {list(df.columns)}"
        )

    out = pd.DataFrame()
    for internal, source in column_map.items():
        out[internal] = df[source].map(clean_text)

    internal_cols = list(column_map.keys())
    for col in internal_cols:
        out = out[out[col] != ""]
    out = out.drop_duplicates().reset_index(drop=True)

    if out.empty:
        raise ValueError("Tidak ada baris admin valid setelah pembersihan.")
    return out


def unique_keys(df, cols):
    subset = df[cols].drop_duplicates()
    records = list(subset.itertuples(index=False, name=None))
    return sorted(records, key=lambda k: tuple(str(x).lower() for x in k))


def filter_by_keys(df, cols, keys):
    if df.empty or not keys:
        return df.iloc[0:0]
    key_frame = pd.DataFrame(list(keys), columns=cols).drop_duplicates()
    row_index = pd.MultiIndex.from_frame(df[cols])
    wanted = pd.MultiIndex.from_frame(key_frame)
    return df[row_index.isin(wanted)]


def format_key(key):
    return ", ".join(str(x) for x in key if str(x).strip())


def infer_level_from_admin(admin, profile):
    parts = [p for p in clean_text(admin).split(",") if p.strip()]
    levels = profile["levels"]
    n = len(parts)
    if 1 <= n <= len(levels):
        return levels[n - 1]
    return levels[-1]


def build_hierarchy_string(key):
    return format_key(key)


# ============================================================
# INTERACTIVE SELECTION
# ============================================================
def parse_selection(raw, n):
    result = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            try:
                a, b = token.split("-", 1)
                a, b = int(a), int(b)
            except ValueError:
                return None
            if a < 1 or b > n or a > b:
                return None
            result.update(range(a, b + 1))
        else:
            try:
                value = int(token)
            except ValueError:
                return None
            if value < 1 or value > n:
                return None
            result.add(value)
    return sorted(result) if result else None


def pick_from_list(display_items, label, allow_all=True, page_size=60):
    n = len(display_items)
    if n == 0:
        return []

    total_pages = (n + page_size - 1) // page_size
    page = 0

    while True:
        start = page * page_size
        end = min(start + page_size, n)
        print(f"\n-- {label} (halaman {page + 1}/{total_pages}, item {start + 1}-{end} dari {n}) --")
        for i in range(start, end):
            print(f"{i + 1:>6}. {display_items[i]}")

        hint = "nomor/rentang (cth: 1,3,5-8)"
        if allow_all:
            hint += " / 'all' / kosong=semua"
        if total_pages > 1:
            hint += " / 'n' halaman berikut / 'p' halaman sebelumnya"

        raw = input(f"Pilih {label} [{hint}]: ").strip().lower()

        if raw in ("all", "semua") and allow_all:
            return list(range(n))
        if raw == "" and allow_all:
            return list(range(n))
        if raw in ("n", "next") and page < total_pages - 1:
            page += 1
            continue
        if raw in ("p", "prev") and page > 0:
            page -= 1
            continue

        parsed = parse_selection(raw, n)
        if parsed:
            return [i - 1 for i in parsed]
        print("Input tidak valid, silakan coba lagi.")


def choose_top_units(current_df, profile):
    """Select top-level (province) scope, optionally via island presets."""
    top_level = profile["levels"][0]
    top_label = profile["level_labels"][top_level]
    cbl = columns_by_level(profile)
    units = [k[0] for k in unique_keys(current_df, cbl[top_level])]

    print(f"\nPilih cakupan {top_label}:")
    print(f"  1. Semua {top_label}")
    print("  2. Pilih berdasarkan pulau")
    print(f"  3. Pilih {top_label} manual")
    choice = input("Pilihan (1/2/3): ").strip()

    if choice == "1":
        return units

    if choice == "2":
        islands = profile.get("islands") or {}
        if not islands:
            print("Preset pulau tidak tersedia, gunakan pilihan manual.")
            choice = "3"
        else:
            island_names = list(islands.keys())
            picked = pick_from_list(island_names, "Pulau", allow_all=False)
            chosen = set()
            for i in picked:
                chosen.update(islands[island_names[i]])
            selected = [p for p in units if p in chosen]
            if not selected:
                print("Tidak ada unit yang cocok dengan pulau tersebut, gunakan semua.")
                return units
            return selected

    picked = pick_from_list(units, top_label, allow_all=True)
    return [units[i] for i in picked]


def choose_coverage_mode(profile, level):
    """Ask how to expand the chosen scope for maximum coverage."""
    leaf = profile["levels"][-1]
    leaf_label = profile["level_labels"][leaf]
    if level == leaf:
        return "unit"

    print("\nMode cakupan (untuk kelengkapan):")
    print(f"  1. Unit terpilih saja ({profile['level_labels'][level]})")
    print(f"  2. Pecah ke {leaf_label} (semua unit terkecil di dalamnya) [Recommended]")
    choice = input("Pilihan (1/2): ").strip()
    return "expand" if choice == "2" else "unit"


def collect_targets(admin_df, brand, country):
    profile = get_profile(country)
    levels = profile["levels"]
    labels = profile["level_labels"]
    cbl = columns_by_level(profile)
    targets = []

    while True:
        print("\nPilih LEVEL target:")
        for i, lvl in enumerate(levels, start=1):
            print(f"  {i}. {labels[lvl]}")
        raw_level = input(f"Pilihan level (1-{len(levels)}): ").strip()
        try:
            level = levels[int(raw_level) - 1]
        except (ValueError, IndexError):
            print("Level tidak valid.")
            continue

        current = admin_df
        parent_levels = levels[:levels.index(level)]

        for parent in parent_levels:
            cols = cbl[parent]
            if parent == levels[0]:
                chosen_names = choose_top_units(current, profile)
                keys = [(name,) for name in chosen_names]
            else:
                keys_all = unique_keys(current, cols)
                display = [format_key(k) for k in keys_all]
                picked = pick_from_list(display, labels[parent], allow_all=True)
                keys = [keys_all[i] for i in picked]

            current = filter_by_keys(current, cols, keys)
            if current.empty:
                print("Tidak ada data pada cakupan yang dipilih.")
                break

        if current.empty:
            continue

        if level == levels[0]:
            chosen_names = choose_top_units(current, profile)
            chosen = [(name,) for name in chosen_names]
        else:
            keys_all = unique_keys(current, cbl[level])
            display = [format_key(k) for k in keys_all]
            picked = pick_from_list(display, labels[level], allow_all=True)
            chosen = [keys_all[i] for i in picked]

        mode = choose_coverage_mode(profile, level)

        if mode == "expand":
            scope = filter_by_keys(current, cbl[level], chosen)
            leaf_level = levels[-1]
            leaf_keys = unique_keys(scope, cbl[leaf_level])
            for key in leaf_keys:
                targets.append({
                    "category": brand,
                    "admin": format_key(key),
                    "level": leaf_level,
                    "country": country,
                })
            print(f"\nDipecah ke {labels[leaf_level]}: {len(leaf_keys)} target")
        else:
            for key in chosen:
                targets.append({
                    "category": brand,
                    "admin": format_key(key),
                    "level": level,
                    "country": country,
                })
            print(f"\nTarget dari pilihan ini: {len(chosen)}")

        print(f"Total target terkumpul: {len(targets)}")
        again = input("Tambah pilihan lain? (y/n): ").strip().lower()
        if again != "y":
            break

    return targets


def print_target_summary(targets):
    profile = get_profile(targets[0]["country"]) if targets else None
    counts = {}
    for t in targets:
        counts[t["level"]] = counts.get(t["level"], 0) + 1
    print("\nRingkasan target yang akan di-scrap:")
    for lvl, cnt in counts.items():
        print(f"  - {profile['level_labels'].get(lvl, lvl)}: {cnt}")
    print(f"  TOTAL: {len(targets)} target")
    print("Contoh query:")
    for t in targets[:5]:
        suffix = profile["query_suffix"]
        print(f"  [{profile['level_labels'].get(t['level'], t['level'])}] {t['category']} in {t['admin']} {suffix}")


def ask_tile_factor(targets):
    print("\nTile density (grid) untuk kelengkapan ekstra:")
    print("  0. Off (1 query per target) [paling cepat]")
    print("  1. 3x3 grid (9 query per target)")
    print("  2. 5x5 grid (25 query per target) [paling menyeluruh]")
    raw = input("Pilihan tile (0/1/2): ").strip()
    tile = int(raw) if raw in ("0", "1", "2") else 0
    if tile > 0 and len(targets) * ((2 * tile + 1) ** 2) > 500:
        warn = input(
            f"Perhatian: {len(targets)} target x {(2 * tile + 1) ** 2} query = "
            f"{len(targets) * ((2 * tile + 1) ** 2)} query. Lanjut? (y/n): "
        ).strip().lower()
        if warn != "y":
            tile = 0
    for t in targets:
        t["tile"] = tile
    return tile


# ============================================================
# INPUT / CHECKPOINT / LOGGING
# ============================================================
def read_target_file(filename, default_country=None):
    path = Path(filename)
    if not path.exists():
        raise FileNotFoundError(f"Input file tidak ditemukan: {filename}")

    if path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(path, dtype=str)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    if df.empty:
        raise ValueError("Input file tidak memiliki data.")

    df.columns = [clean_text(c) for c in df.columns]
    lower_map = {c.lower(): c for c in df.columns}

    category_col = next((lower_map[c] for c in ["category", "brand", "place", "place_name"] if c in lower_map), None)
    admin_col = next((lower_map[c] for c in ["admin", "administrative", "wilayah"] if c in lower_map), None)
    level_col = lower_map.get("level")
    country_col = lower_map.get("country")
    tile_col = lower_map.get("tile")

    if category_col is None or admin_col is None:
        raise ValueError(
            "Input membutuhkan kolom 'category' dan 'admin'. "
            f"Kolom yang ditemukan: {list(df.columns)}"
        )

    if country_col is None and not default_country:
        raise ValueError(
            "Input membutuhkan kolom 'country' (indonesia/philippines). "
            "File target yang dibuat dashboard selalu menyertakannya."
        )

    out = pd.DataFrame()
    out["category"] = df[category_col].fillna("").map(clean_text)
    out["admin"] = df[admin_col].fillna("").map(clean_text)
    out["country"] = (
        df[country_col].fillna("").map(lambda x: clean_text(x).lower()) if country_col else default_country
    )
    fallback_country = default_country or "indonesia"
    out["country"] = out["country"].apply(lambda c: c if c in COUNTRY_PROFILES else fallback_country)
    out["level"] = df[level_col].fillna("").map(clean_text) if level_col else ""
    out["level"] = out.apply(
        lambda r: r["level"] if r["level"] in COUNTRY_PROFILES[r["country"]]["levels"]
        else infer_level_from_admin(r["admin"], get_profile(r["country"])),
        axis=1,
    )
    out["tile"] = (
        df[tile_col].fillna("0").map(lambda x: int(x) if str(x).strip().isdigit() else 0)
        if tile_col else 0
    )
    out = out[(out["category"] != "") & (out["admin"] != "")].reset_index(drop=True)

    if out.empty:
        raise ValueError("Tidak ada baris valid setelah membersihkan category/admin.")

    return out


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_checkpoint(state_file, state):
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    atomic_write_text(state_file, payload)


def load_checkpoint(state_file):
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_to_csv(data, filename):
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(data, columns=OUTPUT_COLUMNS)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(temp_path, index=False, encoding="utf-8-sig")
    os.replace(temp_path, path)


def load_existing_output(filename):
    path = Path(filename)
    if not path.exists() or path.stat().st_size == 0:
        return []

    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        for col in OUTPUT_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[OUTPUT_COLUMNS]
        return df.fillna("").values.tolist()
    except Exception:
        return []


def setup_logger(log_file):
    logger = logging.getLogger("poi_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# ============================================================
# GOOGLE MAPS HELPERS
# ============================================================
def dismiss_common_popups(page):
    candidates = [
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('Got it')",
    ]
    for selector in candidates:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                locator.first.click(timeout=1000)
                page.wait_for_timeout(300)
                break
        except Exception:
            continue


def build_search_url(category, admin, profile, latitude=None, longitude=None, zoom=14):
    """Build a Google Maps search URL anchored to the target area's coordinates."""
    query = f"{clean_text(category)}"
    if latitude is not None and longitude is not None:
        return (
            f"https://www.google.com/maps/search/{quote_plus(query)}"
            f"/@{latitude},{longitude},{zoom}z?hl=en"
        )

    scoped_query = f"{query} in {clean_text(admin)} {profile['query_suffix']}"
    return f"https://www.google.com/maps/search/{quote_plus(scoped_query)}?hl=en"


def resolve_admin_coordinates(page, admin, profile, patience=2, max_scrolls=8, wait_ms=1800, logger=None):
    """Resolve an administrative area to a map coordinate using Google Maps itself."""
    admin_query = f"{clean_text(admin)} {profile['query_suffix']}"
    url = f"https://www.google.com/maps/search/{quote_plus(admin_query)}?hl=en"

    try:
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        dismiss_common_popups(page)

        lat, lon = extract_lat_long_from_url(page.url)
        if lat is not None and lon is not None:
            return lat, lon

        seen = set()
        stale = 0
        scrolls = 0
        while stale < patience and scrolls < max_scrolls:
            before = len(seen)
            try:
                links = page.locator("a[href*='/maps/place/']")
                for i in range(links.count()):
                    try:
                        href = links.nth(i).get_attribute("href", timeout=1000)
                        if not href or href in seen:
                            continue
                        seen.add(href)
                        lat, lon = extract_lat_long_from_url(href)
                        if lat is not None and lon is not None:
                            return lat, lon
                    except Exception:
                        continue
            except Exception:
                pass

            stale = stale + 1 if len(seen) == before else 0
            try:
                feed = page.locator("div[role='feed']").first
                if feed.count() > 0:
                    feed.evaluate("el => { el.scrollTop = el.scrollHeight; }")
                else:
                    page.mouse.wheel(0, 1200)
            except Exception:
                pass
            scrolls += 1
            page.wait_for_timeout(wait_ms)

    except Exception as exc:
        if logger:
            logger.warning(f"Gagal resolve koordinat admin '{admin}': {exc}")

    return None, None


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in kilometers."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def build_country_vocab(admin_df):
    """Bangun kosakata nama wilayah (provinsi+kota) dari data admin untuk country filter."""
    terms = set()
    for col in ("provinsi", "kota"):
        if col in getattr(admin_df, "columns", []):
            for value in admin_df[col].dropna().unique():
                for token in str(value).lower().split():
                    token = re.sub(r"[^a-z0-9]", "", token)
                    if len(token) > 2:
                        terms.add(token)
    return terms


def looks_like_country(address, profile, extra_terms=None):
    """Fallback country check used when coordinates could not be resolved."""
    text = normalize_for_match(address)
    if not text:
        return False
    tokens = set(text.split())
    if tokens & set(profile["reject_terms"]):
        return False
    accept = set(profile["accept_terms"])
    if extra_terms:
        accept |= set(extra_terms)
    return bool(tokens & accept)


def relevance_ok(expected_brand, name, mode="mirip"):
    """Brand match. mode: 'persis' = substring saja, 'mirip' = lentur, 'longgar' = tanpa filter."""
    if mode == "longgar":
        return True
    exp_norm = normalize_for_match(expected_brand)
    name_norm = normalize_for_match(name)
    if not exp_norm:
        return True
    if exp_norm in name_norm:
        return True
    if mode == "persis":
        return False
    exp_compact = exp_norm.replace(" ", "")
    name_compact = name_norm.replace(" ", "")
    if exp_compact and exp_compact in name_compact:
        return True
    exp_tokens = set(exp_norm.split())
    name_tokens = set(name_norm.split())
    if not exp_tokens:
        return True
    overlap = len(exp_tokens & name_tokens) / len(exp_tokens)
    return overlap >= 0.6


def collect_listing_urls(page, patience=3, max_scrolls=100, scroll_wait_ms=2500, logger=None):
    urls = []
    seen_urls = set()
    stale_rounds = 0
    scroll_count = 0

    while stale_rounds < patience and scroll_count < max_scrolls:
        before_count = len(seen_urls)

        try:
            listings = page.locator("a[href*='/maps/place/']")
            total = listings.count()
            for i in range(total):
                try:
                    href = listings.nth(i).get_attribute("href", timeout=1000)
                    if href and "/maps/place/" in href:
                        href = canonical_url(href)
                        if href and href not in seen_urls:
                            seen_urls.add(href)
                            urls.append(href)
                except Exception:
                    continue
        except Exception as exc:
            if logger:
                logger.warning(f"Gagal membaca daftar listing: {exc}")

        after_count = len(seen_urls)

        if after_count == before_count:
            stale_rounds += 1
        else:
            stale_rounds = 0

        scrolled = False
        try:
            feed = page.locator("div[role='feed']").first
            if feed.count() > 0:
                feed.evaluate("el => { el.scrollTop = el.scrollHeight; }")
                scrolled = True
        except Exception:
            scrolled = False

        if not scrolled:
            try:
                page.mouse.wheel(0, 1400)
            except Exception:
                pass

        scroll_count += 1
        page.wait_for_timeout(scroll_wait_ms)

    if logger:
        logger.info(
            f"Hasil pencarian terkumpul: {len(urls)} listing | scrolls={scroll_count} | "
            f"stale_rounds={stale_rounds}"
        )

    return urls


def scrape_operating_hours(page):
    operating_hours = {}

    try:
        rows = page.locator("tbody tr")
        row_count = rows.count()
        for i in range(row_count):
            row = rows.nth(i)
            try:
                cells = row.locator("td")
                if cells.count() == 0:
                    continue

                day = clean_text(cells.first.inner_text(timeout=1000))
                day_key = None
                day_lower = day.lower()
                for full_day, short_day in DAY_MAP.items():
                    if day_lower.startswith(full_day[:3]) or day_lower == full_day:
                        day_key = short_day
                        break

                if not day_key:
                    continue

                times = row.locator("li").all_inner_texts()
                if not times:
                    cell_text = clean_text(row.inner_text(timeout=1000))
                    cell_text = re.sub(re.escape(day), "", cell_text, flags=re.I).strip()
                    times = [x.strip() for x in re.split(r"–|-|,", cell_text) if x.strip()]

                formatted = [clean_time_format(x) for x in times if clean_text(x)]
                if formatted:
                    operating_hours[day_key] = formatted
            except Exception:
                continue
    except Exception:
        pass

    return operating_hours


def scrape_status(page):
    try:
        closed_locator = page.locator(
            "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'temporarily closed') "
            "or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'permanently closed')]"
        )
        if closed_locator.count() > 0:
            return "Closed"
    except Exception:
        pass
    return "Open"


def scrape_payment(page):
    payment_values = []

    try:
        page.wait_for_selector("div[role='tablist'] button[role='tab']", timeout=1500)
        about_button = page.locator("button[role='tab']", has_text=re.compile(r"^About$", re.I))
        if about_button.count() > 0:
            about_button.first.click(timeout=1500)
            page.wait_for_timeout(500)
    except Exception:
        pass

    selectors = [
        "div.iP2t7d[class*='fontBodyMedium'] h2:has-text('Payments')",
        "div[class*='iP2t7d'][class*='fontBodyMedium'] h2:has-text('Payments')",
    ]

    payment_section = None
    for selector in selectors:
        try:
            h2 = page.locator(selector).first
            if h2.count() > 0:
                parent = h2.locator("..")
                if parent.count() > 0:
                    payment_section = parent
                    break
        except Exception:
            continue

    if payment_section is not None:
        try:
            payment_spans = payment_section.locator("span").all_inner_texts()
            for value in payment_spans:
                value = clean_text(value)
                if value and value.lower() != "payments" and value not in payment_values:
                    payment_values.append(value)
        except Exception:
            pass

    if not payment_values:
        try:
            payments_section = page.locator(
                "//div[contains(@class, 'iP2t7d') and .//h2[contains(., 'Payments')]]"
            )
            payments_locator = payments_section.locator(
                "//div[contains(@class, 'iNvpkb')]//span"
            )
            for i in range(payments_locator.count()):
                value = clean_text(payments_locator.nth(i).inner_text(timeout=1000))
                if value and value not in payment_values:
                    payment_values.append(value)
        except Exception:
            pass

    return ", ".join(payment_values)


def scrape_listing(page, expected_brand="", filter_relevance=False, relevance_mode="mirip", logger=None):
    try:
        current_url = canonical_url(page.url)
        latitude, longitude = extract_lat_long_from_url(current_url)

        name = first_text(page, [
            "h1.DUwDvf.lfPIob",
            "div.TIHn2 h1",
            "h1",
        ], timeout=2500)

        local_name = first_text(page, [
            "div.TIHn2 h2.bwoZTb span",
            "div.TIHn2 h2 span",
        ], timeout=1500)

        category = first_text(page, [
            "div.LBgpqf button.DkEaL",
            "div[class*='LBgpqf'] button",
        ], timeout=1500)

        address = first_text(page, [
            "button[data-item-id='address'] div.fontBodyMedium",
            "button[data-item-id='address']",
        ], timeout=1500)

        website = first_attribute(page, [
            "a[data-item-id='authority'][href^='http']",
            "a[data-item-id='authority']",
        ], "href", timeout=1500)

        phone = first_text(page, [
            "button[data-tooltip='Copy phone number']",
            "button[data-item-id='phone']",
            "a[href^='tel:']",
        ], timeout=2500)
        if phone.startswith("tel:"):
            phone = phone[4:]
        phone = re.sub(r"[^\d+\s()\-]", "", phone).strip()
        phone = re.sub(r"\s+", " ", phone)

        building = ""
        try:
            building_elements = page.locator("div.AeaXub div.Io6YTe.fontBodyMedium")
            for i in range(building_elements.count()):
                text = clean_text(building_elements.nth(i).inner_text(timeout=1000))
                if re.search(r"^Located in:\s*", text, re.I):
                    building = re.sub(r"^Located in:\s*", "", text, flags=re.I).strip()
                    break
        except Exception:
            pass

        if not building:
            try:
                all_text = page.locator("body").inner_text(timeout=2000)
                match = re.search(r"Located in:\s*([^\n]+)", all_text, flags=re.I)
                if match:
                    building = clean_text(match.group(1))
            except Exception:
                pass

        hours = scrape_operating_hours(page)
        status = scrape_status(page)
        payment = scrape_payment(page)

        if filter_relevance and not relevance_ok(expected_brand, name, relevance_mode):
            return None, {
                "reason": "relevance_filter_not_contains",
                "name": name,
                "expected_brand": expected_brand,
                "category": category,
                "address": address,
            }

        return {
            "name": name,
            "local_name": local_name,
            "category": category,
            "address": address,
            "building": building,
            "phone": phone,
            "hours": hours,
            "latitude": latitude,
            "longitude": longitude,
            "status": status,
            "website": website,
            "payment": payment,
            "url": current_url,
            "place_id": extract_place_id(current_url),
        }, None

    except Exception as exc:
        if logger:
            logger.warning(f"Error processing listing: {exc}")
        return None, {"reason": "exception", "error": str(exc)}


# ============================================================
# DEDUPLICATION
# ============================================================
def legacy_identity(name, address, category):
    return (
        normalize_for_match(name),
        normalize_for_match(address),
        normalize_for_match(category),
    )


def record_signature(scraped):
    """Stable identity: place_id first, then geo proximity + name, then legacy."""
    pid = extract_place_id(scraped.get("url", ""))
    if pid:
        return ("pid", pid)

    name = normalize_for_match(scraped.get("name", ""))
    lat = scraped.get("latitude")
    lon = scraped.get("longitude")
    if name and lat is not None and lon is not None:
        return ("geo", name, round(float(lat), 4), round(float(lon), 4))

    return ("legacy",) + legacy_identity(name, scraped.get("address", ""), scraped.get("category", ""))


def build_seen_sets(data):
    """Rebuild dedup sets from previously saved output rows."""
    seen_legacy = set()
    seen_sig = set()
    for row in data:
        if len(row) < 14:
            continue
        name, category, address = row[2], row[4], row[5]
        legacy = legacy_identity(name, address, category)
        if any(legacy):
            seen_legacy.add(legacy)

        lat, lon = row[9], row[10]
        norm_name = normalize_for_match(name)
        try:
            if norm_name and lat and lon:
                seen_sig.add(("geo", norm_name, round(float(lat), 4), round(float(lon), 4)))
        except (TypeError, ValueError):
            pass
    return seen_legacy, seen_sig


# ============================================================
# MAIN SCRAPER
# ============================================================
def main(
    input_file,
    save_interval,
    save_filename,
    patience,
    resume,
    state_file,
    headless,
    page_timeout,
    search_wait,
    detail_wait,
    scroll_wait,
    max_scrolls,
    retries,
    filter_relevance,
    admin_radius_km,
    progress_cb=None,
    data_cb=None,
    relevance_mode="mirip",
    keep_no_coords=False,
    country_vocab=None,
):
    df = read_target_file(input_file)
    total_rows = len(df)
    start_time = time.time()

    output_path = Path(save_filename)
    state_path = Path(state_file) if state_file else Path(str(output_path) + ".state.json")
    log_path = Path(str(output_path) + ".log")
    logger = setup_logger(str(log_path))

    data = load_existing_output(save_filename) if resume else []
    seen_legacy, seen_sig = build_seen_sets(data)

    checkpoint = load_checkpoint(state_path) if resume else None
    start_row = 0
    if checkpoint:
        start_row = max(0, int(checkpoint.get("next_row_index", 0)))
        if start_row >= total_rows:
            start_row = total_rows

    if not resume:
        data = []
        seen_legacy, seen_sig = set(), set()

    first_country = df.iloc[0]["country"] if total_rows else "indonesia"
    profile = get_profile(first_country)

    logger.info(f"Input: {input_file}")
    logger.info(f"Country: {profile['label']} | Total input rows: {total_rows}")
    logger.info(f"Output: {save_filename}")
    logger.info(f"Resume: {resume} | Start row: {start_row}")
    logger.info(f"Relevance filter (Brand): {filter_relevance}")
    logger.info(
        "Admin geographic radius: "
        + (f"fixed {admin_radius_km} km" if admin_radius_km is not None else "adaptif per level")
    )

    stats = {
        "rows_completed": start_row,
        "listings_found": 0,
        "scraped": 0,
        "duplicates": 0,
        "relevance_filtered": 0,
        "geo_filtered": 0,
        "errors": 0,
        "zero_result_targets": 0,
    }

    last_saved_index = len(data)
    seen_url_ids = set()

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, args=["--lang=en-US"])
            results_page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id=profile["timezone"])
            detail_page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id=profile["timezone"])

            results_page.set_default_timeout(page_timeout)
            detail_page.set_default_timeout(page_timeout)

            for index in range(start_row, total_rows):
                category = df.iloc[index]["category"]
                admin = df.iloc[index]["admin"]
                level = df.iloc[index]["level"]
                row_country = df.iloc[index]["country"]
                row_profile = get_profile(row_country)
                tile = int(df.iloc[index]["tile"])
                radius_km = admin_radius_km if admin_radius_km is not None else row_profile["radius_by_level"].get(level, 25.0)

                search_query = f"{category} in {admin} {row_profile['query_suffix']}"
                target_lat, target_lon = resolve_admin_coordinates(
                    results_page,
                    admin,
                    row_profile,
                    patience=2,
                    max_scrolls=8,
                    wait_ms=max(1000, min(search_wait, 3000)),
                    logger=logger,
                )

                if target_lat is not None and target_lon is not None:
                    logger.info(
                        f"Target coordinate resolved: {admin} -> "
                        f"{target_lat:.6f},{target_lon:.6f} | level={level} | radius={radius_km} km | tile={tile}"
                    )
                else:
                    logger.warning(
                        f"Target coordinate NOT resolved for '{admin}'. "
                        "Fallback query mode akan digunakan."
                    )

                step_km = max(1.0, radius_km * 0.8)
                offsets = grid_points(target_lat, target_lon, tile, step_km)

                listing_urls = []
                row_succeeded = False

                for grid_idx, (glat, glon) in enumerate(offsets, start=1):
                    search_url = build_search_url(category, admin, row_profile, glat, glon)
                    grid_succeeded = False

                    for attempt in range(1, retries + 1):
                        try:
                            logger.info(
                                f"[{index + 1}/{total_rows}] Search: {search_query} | "
                                f"tile {grid_idx}/{len(offsets)} | attempt {attempt}/{retries}"
                            )
                            results_page.goto(search_url, timeout=page_timeout, wait_until="domcontentloaded")
                            results_page.wait_for_timeout(search_wait)
                            dismiss_common_popups(results_page)

                            found = collect_listing_urls(
                                results_page, patience=patience, max_scrolls=max_scrolls,
                                scroll_wait_ms=scroll_wait, logger=logger,
                            )

                            for url in found:
                                url_id = extract_place_id(url) or canonical_url(url)
                                if url_id in seen_url_ids:
                                    continue
                                seen_url_ids.add(url_id)
                                listing_urls.append(url)

                            grid_succeeded = True
                            break
                        except (PlaywrightTimeoutError, Exception) as exc:
                            stats["errors"] += 1
                            logger.warning(f"Search gagal untuk '{search_query}' (tile {grid_idx}) pada attempt {attempt}: {exc}")
                            if attempt < retries:
                                time.sleep(1.5 * attempt)

                    if not grid_succeeded:
                        break

                if not grid_succeeded:
                    logger.error(f"Koneksi terputus total pada pencarian '{search_query}'. Script dihentikan agar progress dapat dilanjutkan dengan aman (Resume).")
                    stats["rows_completed"] = index
                    save_checkpoint(state_path, {
                        "next_row_index": index,
                        "last_completed_admin": df.iloc[index - 1]["admin"] if index > 0 else "",
                        "last_query": search_query,
                        "country": row_country,
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "stats": stats,
                    })
                    sys.exit(1)

                stats["listings_found"] += len(listing_urls)
                if not listing_urls:
                    stats["zero_result_targets"] += 1
                    zero_list = stats.setdefault("zero_targets", [])
                    zero_list.append(admin)
                    if len(zero_list) > 500:
                        del zero_list[:-500]
                    logger.warning(f"0 listing untuk target '{admin}' (level={level}).")

                for listing_no, listing_url in enumerate(listing_urls, start=1):
                    try:
                        detail_page.goto(listing_url, timeout=page_timeout, wait_until="domcontentloaded")
                        detail_page.wait_for_timeout(detail_wait)
                        dismiss_common_popups(detail_page)

                        scraped, error = scrape_listing(
                            detail_page, expected_brand=category, filter_relevance=filter_relevance,
                            relevance_mode=relevance_mode, logger=logger
                        )

                        if error:
                            if "relevance_filter" in error.get("reason", ""):
                                stats["relevance_filtered"] += 1
                            else:
                                stats["errors"] += 1
                                logger.warning(f"Listing gagal [{listing_no}/{len(listing_urls)}] {listing_url}: {error}")
                            continue

                        name = scraped["name"]
                        address = scraped["address"]
                        mapped_category = scraped["category"]

                        if target_lat is not None and target_lon is not None:
                            distance_km = haversine_km(
                                target_lat, target_lon,
                                scraped["latitude"], scraped["longitude"]
                            )
                            if distance_km is None:
                                if not keep_no_coords:
                                    stats["geo_filtered"] += 1
                                    logger.info(f"Geo-filter: REJECT '{name}' | tanpa koordinat")
                                    continue
                            elif distance_km > radius_km:
                                stats["geo_filtered"] += 1
                                logger.info(
                                    f"Geo-filter: REJECT '{name}' | "
                                    f"distance={distance_km:.2f} km | target={admin} | radius={radius_km} km"
                                )
                                continue
                        elif not looks_like_country(address, row_profile, country_vocab):
                            stats["geo_filtered"] += 1
                            logger.info(f"Country-filter: REJECT '{name}' | address='{address}'")
                            continue

                        signature = record_signature(scraped)
                        legacy = legacy_identity(name, address, mapped_category)
                        if signature in seen_sig or (any(legacy) and legacy in seen_legacy):
                            stats["duplicates"] += 1
                            continue

                        seen_sig.add(signature)
                        if any(legacy):
                            seen_legacy.add(legacy)

                        data.append([
                            category, admin, name, scraped["local_name"], mapped_category,
                            address, scraped["building"], scraped["phone"], scraped["hours"],
                            scraped["latitude"], scraped["longitude"], scraped["status"],
                            scraped["website"], scraped["payment"],
                            scraped.get("place_id", ""), row_country, level, admin,
                            scraped.get("url", ""), time.strftime("%Y-%m-%d %H:%M:%S"),
                        ])
                        stats["scraped"] += 1

                        if len(data) - last_saved_index >= save_interval:
                            save_to_csv(data, save_filename)
                            last_saved_index = len(data)
                            logger.info(f"Auto-save: {len(data)} record -> {save_filename}")
                            if data_cb:
                                try:
                                    data_cb(data)
                                except Exception:
                                    pass

                    except (PlaywrightTimeoutError, Exception) as exc:
                        stats["errors"] += 1
                        logger.warning(f"Error processing listing {listing_no}/{len(listing_urls)}: {exc}")

                    print_progress(index + 1, total_rows, start_time, len(data), stats["duplicates"], stats["errors"], admin)

                stats["rows_completed"] = index + 1
                save_checkpoint(state_path, {
                    "next_row_index": index + 1,
                    "last_completed_admin": admin,
                    "last_query": search_query,
                    "country": row_country,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "stats": stats,
                })

                if progress_cb:
                    try:
                        progress_cb(stats, index + 1, total_rows, len(data), admin)
                    except Exception:
                        pass

                if len(data) > last_saved_index:
                    save_to_csv(data, save_filename)
                    last_saved_index = len(data)
                    if data_cb:
                        try:
                            data_cb(data)
                        except Exception:
                            pass

                print_progress(index + 1, total_rows, start_time, len(data), stats["duplicates"], stats["errors"], admin)

            if len(data) > last_saved_index or not Path(save_filename).exists():
                save_to_csv(data, save_filename)

            if data_cb:
                try:
                    data_cb(data)
                except Exception:
                    pass

            save_checkpoint(state_path, {
                "next_row_index": total_rows,
                "last_completed_admin": df.iloc[-1]["admin"] if total_rows else "",
                "last_query": f"{df.iloc[-1]['category']} in {df.iloc[-1]['admin']} {get_profile(df.iloc[-1]['country'])['query_suffix']}" if total_rows else "",
                "country": first_country,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stats": stats,
                "completed": True,
            })

            logger.info("\n========================================")
            logger.info("SCRAPING SELESAI")
            logger.info(f"Total output records : {len(data)}")
            logger.info(f"Listings found       : {stats['listings_found']}")
            logger.info(f"Duplicates skipped   : {stats['duplicates']}")
            logger.info(f"Relevance filtered   : {stats['relevance_filtered']}")
            logger.info(f"Geo filtered         : {stats['geo_filtered']}")
            logger.info(f"Targets 0 hasil      : {stats['zero_result_targets']}")
            logger.info(f"Errors               : {stats['errors']}")
            logger.info(f"Output file          : {save_filename}")
            logger.info("========================================")

            return data

    except KeyboardInterrupt:
        logger.warning("\nScraping dihentikan secara manual (Ctrl+C). Progress terakhir telah tersimpan, jalankan fitur Resume untuk melanjutkan.")
        return data
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        sys.stdout.write("\n")
        sys.stdout.flush()


# ============================================================
# JOB MODE (non-interactive, untuk API / GitHub Actions / otomasi)
# ============================================================
def _spec_scope(spec, key):
    scope = spec.get("scope") or {}
    values = scope.get(key) or []
    return [clean_text(v) for v in values if clean_text(v)]


def spec_to_targets(spec, admin_df, profile):
    """Bangun daftar target (dict) dari spec JSON, tanpa interaksi terminal."""
    levels = profile["levels"]
    cbl = columns_by_level(profile)
    country = clean_text(spec.get("country", "indonesia")).lower()
    brand = clean_text(spec.get("brand", ""))
    level = spec.get("level")
    if level not in levels:
        level = levels[-1]
    mode = spec.get("mode", "unit")
    tile = int(spec.get("tile", 0) or 0)

    explicit = [clean_text(x) for x in (spec.get("explicit_admins") or []) if clean_text(x)]
    if explicit:
        out = []
        seen = set()
        for adm in explicit:
            if adm in seen:
                continue
            seen.add(adm)
            lvl = spec.get("level") if spec.get("level") in levels else infer_level_from_admin(adm, profile)
            out.append({"category": brand, "admin": adm, "level": lvl, "country": country, "tile": tile})
        return out

    current = admin_df
    for parent in levels[:levels.index(level)]:
        name_col = cbl[parent][0]
        sel = _spec_scope(spec, parent)
        if sel and name_col in current.columns:
            current = current[current[name_col].isin(set(sel))]

    target_cols = cbl[level]
    keys = unique_keys(current, target_cols)
    sel = _spec_scope(spec, level)
    if sel:
        sel_set = set(sel)
        keys = [k for k in keys if k[0] in sel_set]

    out_level = level
    if mode == "expand" and level != levels[-1]:
        leaf_cols = cbl[levels[-1]]
        scope_df = filter_by_keys(current, target_cols, keys)
        keys = unique_keys(scope_df, leaf_cols)
        out_level = levels[-1]

    return [
        {"category": brand, "admin": format_key(k), "level": out_level, "country": country, "tile": tile}
        for k in keys
    ]


def plan_chunks(spec, profile, threshold=40):
    """Pecah spec menjadi beberapa chunk per provinsi bila target besar (> threshold)."""
    admin_df = load_admin_data(profile)
    full_targets = spec_to_targets(spec, admin_df, profile)
    if len(full_targets) <= threshold or spec.get("explicit_admins"):
        return [spec], [len(full_targets)]

    top_level = profile["levels"][0]
    top_col = columns_by_level(profile)[top_level][0]
    provinces = _spec_scope(spec, top_level)
    if not provinces:
        provinces = [k[0] for k in unique_keys(admin_df, columns_by_level(profile)[top_level])]

    chunks = []
    counts = []
    for prov in provinces:
        chunk = json.loads(json.dumps(spec))
        chunk.setdefault("scope", {})
        chunk["scope"][top_level] = [prov]
        targets = spec_to_targets(chunk, admin_df, profile)
        if not targets:
            continue
        chunks.append(chunk)
        counts.append(len(targets))
    return chunks, counts


def run_job(spec_path, progress_callback=None, data_callback=None):
    """Jalankan satu job dari file spec JSON. Mengembalikan manifest."""
    spec_path = Path(spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    country = clean_text(spec.get("country", "indonesia")).lower()
    profile = get_profile(country)
    brand = clean_text(spec.get("brand", ""))
    if not brand:
        raise ValueError("Spec tidak memiliki 'brand'.")

    admin_df = load_admin_data(profile)
    targets = spec_to_targets(spec, admin_df, profile)
    if not targets:
        raise ValueError("Spec tidak menghasilkan target.")

    params = spec.get("params") or {}
    chunk_index = spec.get("chunk_index")
    clean_brand = brand.replace(" ", "_")
    tag = profile["file_tag"]
    out_dir = Path(spec.get("output_dir", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_chunk{chunk_index}" if chunk_index not in (None, "") else ""
    input_file = out_dir / f"Input_{clean_brand}_{tag}{suffix}.csv"
    save_filename = spec.get("save_filename") or str(out_dir / f"Output_{clean_brand}_{tag}{suffix}.csv")

    pd.DataFrame(targets, columns=["category", "admin", "level", "country", "tile"]).to_csv(
        input_file, index=False, encoding="utf-8-sig"
    )

    def _cb(stats, current, total, scraped, admin=""):
        if progress_callback:
            progress_callback(stats, current, total, scraped, admin)

    def _dcb(data_rows):
        if data_callback:
            data_callback(data_rows)

    relevance_mode = clean_text(params.get("relevance_mode", "mirip")).lower()
    if relevance_mode not in {"persis", "mirip", "longgar"}:
        relevance_mode = "mirip"
    if params.get("filter_relevance") is False:
        relevance_mode = "longgar"

    data = main(
        input_file=str(input_file),
        save_interval=int(params.get("save_interval", 5)),
        save_filename=save_filename,
        patience=int(params.get("patience", 3)),
        resume=bool(params.get("resume", False)),
        state_file=params.get("state_file", ""),
        headless=bool(params.get("headless", True)),
        page_timeout=int(params.get("page_timeout", 60000)),
        search_wait=int(params.get("search_wait", 5000)),
        detail_wait=int(params.get("detail_wait", 1800)),
        scroll_wait=int(params.get("scroll_wait", 2500)),
        max_scrolls=int(params.get("max_scrolls", 100)),
        retries=int(params.get("retries", 3)),
        filter_relevance=bool(params.get("filter_relevance", True)) and relevance_mode != "longgar",
        admin_radius_km=params.get("radius_km"),
        progress_cb=_cb,
        data_cb=_dcb,
        relevance_mode=relevance_mode,
        keep_no_coords=bool(params.get("keep_no_coords", False)),
        country_vocab=build_country_vocab(admin_df),
    )

    manifest = {
        "job_id": spec.get("job_id", ""),
        "chunk_index": chunk_index,
        "country": country,
        "brand": brand,
        "level": spec.get("level", ""),
        "targets": len(targets),
        "output": save_filename,
        "records": len(data),
    }
    Path(str(save_filename) + ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


# ============================================================
# CLI & INTERACTIVE MENU
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="Google Maps POI scraper (Indonesia & Philippines) berbasis Playwright."
    )

    parser.add_argument(
        "-if", "--input_file", type=str, required=False,
        help="Path CSV/XLSX target (category, admin, [level, country, tile]). Jika kosong, masuk mode interaktif."
    )
    parser.add_argument(
        "--job", type=str, default="", help="Path spec JSON untuk job-mode non-interaktif (dipakai otomasi/API)."
    )
    parser.add_argument("-si", "--save_interval", type=int, default=5, help="Simpan CSV setiap N record. Default: 5")
    parser.add_argument("-sf", "--save_filename", type=str, default="", help="Nama file output CSV.")
    parser.add_argument("--state_file", type=str, default="", help="Path checkpoint JSON.")
    parser.add_argument("--patience", type=int, default=3, help="Toleransi scroll kosong. Default: 3")
    parser.add_argument("--max_scrolls", type=int, default=100, help="Batas maksimum scroll. Default: 100")
    parser.add_argument("--retries", type=int, default=3, help="Maksimal percobaan buka URL. Default: 3")
    parser.add_argument("--page_timeout", type=int, default=60000, help="Timeout ms. Default: 60000")
    parser.add_argument("--search_wait", type=int, default=5000, help="Wait setelah search ms. Default: 5000")
    parser.add_argument("--detail_wait", type=int, default=1800, help="Wait detail ms. Default: 1800")
    parser.add_argument("--scroll_wait", type=int, default=2500, help="Wait scroll ms. Default: 2500")
    parser.add_argument("--headless", action="store_true", help="Jalankan tanpa GUI browser.")
    parser.add_argument("--resume", action="store_true", help="Lanjutkan dari state_file terakhir.")
    parser.add_argument("--filter_relevance", "--filter-relevance", action="store_true", help="Buang hasil tidak relevan.")
    parser.add_argument("--no_filter_relevance", "--no-filter-relevance", action="store_true", help="Matikan filter relevansi brand.")
    parser.add_argument(
        "--admin_radius_km", type=float, default=None,
        help="Paksa radius geo-filter tetap (km). Jika kosong, radius adaptif per level."
    )

    return parser.parse_args()


def run_resume_flow(args):
    state_files = [f for f in os.listdir('.') if f.endswith('.state.json')]
    if not state_files:
        print("Error: Tidak ada file histori resume (*.state.json) yang ditemukan.")
        sys.exit(1)

    print("\nPilih progress terputus yang ingin dilanjutkan:")
    for i, sf in enumerate(state_files, 1):
        print(f"{i}. {sf.replace('.state.json', '')}")

    try:
        pilihan = int(input("\nMasukkan angka pilihan: ").strip())
        selected_sf = state_files[pilihan - 1]
    except (ValueError, IndexError):
        print("Pilihan tidak valid.")
        sys.exit(1)

    args.save_filename = selected_sf.replace('.state.json', '')
    args.input_file = args.save_filename.replace('Output_', 'Input_')

    if not os.path.exists(args.input_file):
        print(f"Error: File input referensi '{args.input_file}' tidak ditemukan!")
        sys.exit(1)

    state = load_checkpoint(selected_sf) or {}
    args.resume = True
    args.filter_relevance = True
    print(f"\nMelanjutkan scraping secara aman untuk: {args.save_filename}...\n")
    return state.get("country", "")


def choose_country():
    keys = list(COUNTRY_PROFILES.keys())
    print("\nPilih NEGARA data admin:")
    for i, key in enumerate(keys, start=1):
        print(f"  {i}. {COUNTRY_PROFILES[key]['label']}")
    raw = input(f"Pilihan negara (1-{len(keys)}): ").strip()
    try:
        return keys[int(raw) - 1]
    except (ValueError, IndexError):
        print("Pilihan tidak valid. Default: Indonesia.")
        return keys[0]


def run_new_flow(args):
    print("\n" + "=" * 60)
    print("   Google Maps POI Scraper - Interaktif (Indonesia & Philippines)")
    print("=" * 60)

    country = choose_country()
    profile = get_profile(country)

    brand = input("\nMasukkan BRAND/KATA KUNCI lokasi yang ingin di-scrap (contoh: Alfamart): ").strip()
    if not brand:
        print("Error: Input tidak boleh kosong.")
        sys.exit(1)

    try:
        resolved_admin = resolve_admin_path(profile["admin_file"])
    except FileNotFoundError:
        print(f"Error: File data admin '{profile['admin_file']}' tidak ditemukan (root atau folder data/).")
        sys.exit(1)

    print(f"\nMembaca data admin {profile['label']} dari '{resolved_admin}'...")
    try:
        admin_df = load_admin_data(profile)
    except Exception as exc:
        print(f"Gagal membaca data admin: {exc}")
        sys.exit(1)

    summary = ", ".join(
        f"{admin_df[col].nunique()} {profile['level_labels'].get(col, col)}" for col in profile["levels"]
    )
    print(f"Data admin dimuat: {summary}.")

    targets = collect_targets(admin_df, brand, country)
    if not targets:
        print("Tidak ada target yang dipilih. Keluar.")
        sys.exit(1)

    print_target_summary(targets)
    tile = ask_tile_factor(targets)

    confirm = input("\nMulai scraping sekarang? (y/n): ").strip().lower()
    if confirm != "y":
        print("Dibatalkan.")
        sys.exit(0)

    clean_brand = brand.replace(' ', '_')
    tag = profile["file_tag"]
    args.input_file = f"Input_{clean_brand}_{tag}.csv"
    args.save_filename = f"Output_{clean_brand}_{tag}.csv"

    df_input = pd.DataFrame(targets, columns=["category", "admin", "level", "country", "tile"])
    df_input.to_csv(args.input_file, index=False, encoding="utf-8-sig")

    print(f"\nFile target tersimpan sebagai : {args.input_file}")
    print(f"File hasil akan disimpan di   : {args.save_filename}")
    if tile:
        print(f"Mode tile: {(2 * tile + 1)}x{(2 * tile + 1)} grid per target.")
    print()

    args.resume = False
    args.filter_relevance = True
    return country


if __name__ == "__main__":
    args = parse_args()
    default_country = "indonesia"

    if args.job:
        def _emit_progress(stats, current, total, scraped, admin=""):
            line = {
                "type": "progress",
                "current_row": current,
                "total_rows": total,
                "records": scraped,
                "progress": round((current / total) * 100, 2) if total else 100.0,
                "stats": stats,
            }
            print("PROGRESS " + json.dumps(line, ensure_ascii=False), flush=True)

        _manifest = run_job(args.job, progress_callback=_emit_progress)
        print("DONE " + json.dumps(_manifest, ensure_ascii=False), flush=True)
        sys.exit(0)

    if not args.input_file:
        print("\n" + "=" * 50)
        print("    Google Maps POI Scraper - Interaktif")
        print("=" * 50)
        print("1. Mulai Scraping Baru")
        print("2. Lanjutkan Scraping (Resume progress terputus)")
        mode = input("\nPilih mode (1/2): ").strip()

        if mode == '2':
            default_country = run_resume_flow(args) or default_country
        else:
            default_country = run_new_flow(args)
    else:
        if not args.save_filename:
            stem = Path(args.input_file).stem
            args.save_filename = f"Output_{stem}.csv"

    if args.no_filter_relevance:
        args.filter_relevance = False

    if args.save_interval < 1:
        raise SystemExit("--save_interval harus >= 1")
    if args.patience < 1:
        raise SystemExit("--patience harus >= 1")
    if args.max_scrolls < 1:
        raise SystemExit("--max_scrolls harus >= 1")
    if args.retries < 1:
        raise SystemExit("--retries harus >= 1")

    main(
        input_file=args.input_file,
        save_interval=args.save_interval,
        save_filename=args.save_filename,
        patience=args.patience,
        resume=args.resume,
        state_file=args.state_file,
        headless=args.headless,
        page_timeout=args.page_timeout,
        search_wait=args.search_wait,
        detail_wait=args.detail_wait,
        scroll_wait=args.scroll_wait,
        max_scrolls=args.max_scrolls,
        retries=args.retries,
        filter_relevance=args.filter_relevance,
        admin_radius_km=args.admin_radius_km,
    )
