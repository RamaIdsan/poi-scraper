"""Bangun index dropdown (provinsi -> kota -> kecamatan) dari CSV admin.

Jalankan: python admin/build_index.py
Menghasilkan: admin/id_index.json, admin/ph_index.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import scraper_indonesia as s  # noqa: E402


def build(country):
    profile = s.get_profile(country)
    df = s.load_admin_data(profile)
    provinces = sorted(df["provinsi"].unique().tolist())

    tree = {}
    for prov in provinces:
        sub = df[df["provinsi"] == prov]
        cities = {}
        for city in sorted(sub["kota"].unique().tolist()):
            sub_city = sub[sub["kota"] == city]
            if "kecamatan" in sub_city.columns and sub_city["kecamatan"].nunique() > 0:
                kecamatan = sorted(sub_city["kecamatan"].unique().tolist())
            else:
                kecamatan = []
            cities[city] = kecamatan
        tree[prov] = cities

    return {
        "country": country,
        "label": profile["label"],
        "levels": profile["levels"],
        "level_labels": profile["level_labels"],
        "provinces": provinces,
        "tree": tree,
    }


def main():
    out_dir = Path(__file__).resolve().parent
    for country, filename in [("indonesia", "id_index.json"), ("philippines", "ph_index.json")]:
        data = build(country)
        path = out_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        total_cities = sum(len(v) for v in data["tree"].values())
        print(f"wrote {path.name}: {len(data['provinces'])} provinsi, {total_cities} kota")


if __name__ == "__main__":
    main()
