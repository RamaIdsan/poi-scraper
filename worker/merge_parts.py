"""Gabungkan CSV hasil tiap chunk, dedup, audit cakupan, tulis ke tab hasil, upload Drive."""
import argparse
import copy
import glob
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poi_scraper as s  # noqa: E402
import cloud  # noqa: E402

DEDUP_KEYS = [
    "Brand", "Name_geocode", "Address_geocode",
    "Latitude_geocode", "Longitude_geocode",
]


def parse_env_list(name):
    try:
        value = json.loads(os.environ.get(name) or "[]")
    except Exception:  # noqa: BLE001
        return []
    return value if isinstance(value, list) else []


def parse_json(value, fallback):
    try:
        parsed = json.loads(value or "")
    except Exception:  # noqa: BLE001
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def entry_targets(spec, profile, admin_df, entry):
    """Hitung daftar target (admin) untuk satu entry plan."""
    if not entry or entry.get("chunk") == "__SKIP__":
        return set()
    start = entry.get("start")
    end = entry.get("end")
    if start is None and end is None:
        targets = s.spec_to_targets(spec, admin_df, profile)
    else:
        chunk_spec = copy.deepcopy(spec)
        top_level = profile["levels"][0]
        chunk_spec.setdefault("scope", {})
        chunk_spec["scope"][top_level] = [entry["prov"]]
        targets = s.spec_to_targets(chunk_spec, admin_df, profile)
        targets = targets[int(start or 0): int(end) if end is not None else None]
    return {t["admin"] for t in targets}


def best_record_dedup(full):
    """Dedup Place_ID dengan memilih baris terlengkap + mengisi kolom kosong dari duplikat."""
    if full.empty:
        return full
    full = full.copy()
    full["Place_ID"] = full["Place_ID"].astype(str).str.strip()
    pid = full["Place_ID"]
    has_pid = pid != ""
    part = full[has_pid].copy()
    rest = full[~has_pid].copy()

    if not part.empty:
        part = part.replace("", pd.NA)
        score_cols = [c for c in s.OUTPUT_COLUMNS if c != "Place_ID"]
        part["_score"] = part[score_cols].notna().sum(axis=1)
        part["_order"] = range(len(part))
        part = part.sort_values(["_score", "_order"], ascending=[False, True], kind="stable")
        part = part.drop(columns=["_order"])
        merged = part.groupby("Place_ID", as_index=False, sort=False).first()
        merged = merged.drop(columns=["_score"], errors="ignore").fillna("")
        rest = rest.fillna("")
        full = pd.concat([merged, rest], ignore_index=True)

    full = full.reindex(columns=s.OUTPUT_COLUMNS).fillna("")

    keys = [c for c in DEDUP_KEYS if c in full.columns]
    if keys:
        full = full.drop_duplicates(subset=keys)

    names = full["Name_geocode"].astype(str).str.strip().str.lower()
    lat = pd.to_numeric(full["Latitude_geocode"], errors="coerce")
    lon = pd.to_numeric(full["Longitude_geocode"], errors="coerce")
    valid = lat.notna() & lon.notna() & (names != "")
    if valid.any():
        sub = full[valid].copy()
        sub["_coord_key"] = (
            names[valid] + "|" + lat[valid].round(4).astype(str) + "|" + lon[valid].round(4).astype(str)
        )
        sub = sub.drop_duplicates(subset=["_coord_key"]).drop(columns=["_coord_key"])
        full = pd.concat([full[~valid], sub], ignore_index=True)

    return full.reindex(columns=s.OUTPUT_COLUMNS).fillna("")


def audit_coverage(chunks, entries, spec, profile, admin_df):
    """Periksa cakupan berdasarkan status chunk (bukan baris hasil)."""
    missing = []
    missing_all = []
    expected_total = 0
    done_total = 0
    for entry in entries:
        if not entry or entry.get("chunk") == "__SKIP__":
            continue
        planned = len(entry_targets(spec, profile, admin_df, entry))
        expected_total += planned
        try:
            idx = int(entry.get("idx"))
        except (TypeError, ValueError):
            idx = -1
        record = chunks.get(idx)
        label = entry.get("chunk") or entry.get("prov") or f"chunk {idx}"
        if record is None:
            missing.append(f"{label}: belum berjalan")
            continue
        try:
            done_total += min(planned, int(float(str(record.get("done", 0)) or 0)))
        except (TypeError, ValueError):
            pass
        status = str(record.get("status", "")).strip().lower()
        if status == "done":
            continue
        if status == "partial":
            failed = parse_json(record.get("failed_json"), [])
            if failed:
                missing.extend(str(name) for name in failed if str(name).strip())
            continue
        missing.append(f"{label}: {status or 'tanpa status'}")
    missing_all = list(missing)
    return expected_total, done_total, missing, missing_all


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--parts", default="parts")
    parser.add_argument("--workflow-result", default="success")
    parser.add_argument("--plan-result", default="success")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    job_id = spec.get("job_id", "")
    user = spec.get("user", "")
    result_sheet = spec.get("result_sheet", "") or f"Result_{job_id}"

    try:
        cloud.update_job(job_id, {})
    except Exception:  # noqa: BLE001
        pass

    if args.plan_result != "success":
        cloud.update_job(job_id, {
            "status": "failed",
            "error": "Plan gagal — cek log GitHub Actions (job 'plan'). Tidak ada chunk yang dijalankan.",
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        cloud.append_log(job_id, user, "merge: plan gagal, tidak ada chunk dijalankan")
        print("MERGE SKIPPED: plan failed", flush=True)
        return

    files = [
        f for f in glob.glob(os.path.join(args.parts, "**", "*.csv"), recursive=True)
        if Path(f).name.lower().startswith("output_")
    ]

    frames = []
    for path in files:
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
            if not frame.empty:
                frames.append(frame)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN gagal baca {path}: {exc}")

    # Sertakan data yang sudah ada di tab hasil (penting untuk resume/pause).
    try:
        tab_rows = cloud.fetch_tab_rows(result_sheet)
        if len(tab_rows) > 1:
            header = tab_rows[0]
            body = tab_rows[1:]
            try:
                tab_frame = pd.DataFrame(body, columns=header).reindex(columns=s.OUTPUT_COLUMNS).fillna("")
                if not tab_frame.empty:
                    frames.append(tab_frame)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN tab frame gagal: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARN fetch tab gagal: {exc}")

    finished = time.strftime("%Y-%m-%d %H:%M:%S")
    status = "done" if args.workflow_result == "success" else "failed"

    full = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=s.OUTPUT_COLUMNS)
    full = full.reindex(columns=s.OUTPUT_COLUMNS).fillna("")
    before = len(full)

    full = best_record_dedup(full)

    # Audit cakupan: berbasis status chunk (JobChunks), bukan keberadaan baris hasil
    # (dedup Place_ID bisa menghapus baris milik target lain yang menemukan toko sama).
    coverage_line = ""
    missing_todo = []
    all_entries = parse_env_list("ALL_ENTRIES")
    todo_entries = parse_env_list("PLAN_ENTRIES") or all_entries
    if all_entries:
        try:
            profile = s.get_profile(spec.get("country", "indonesia"))
            admin_df = s.load_admin_data(profile)
            chunks = cloud.fetch_job_chunks(job_id)
            if chunks:
                expected, covered, missing_todo, missing_all = audit_coverage(
                    chunks, todo_entries, spec, profile, admin_df
                )
                coverage_line = f"cakupan chunk: {covered}/{expected} target"
                if missing_all:
                    sample = ", ".join(sorted(set(missing_all))[:10])
                    coverage_line += f" · belum terverifikasi {len(set(missing_all))} (mis. {sample})"
                cloud.append_log(job_id, user, "audit " + coverage_line)
                print("AUDIT " + coverage_line, flush=True)
            else:
                cloud.append_log(
                    job_id, user,
                    "audit: data chunk belum tersedia (job lama) — cakupan tidak difailkan",
                )
                print("AUDIT legacy: JobChunks kosong, audit informatif saja", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN audit cakupan gagal: {exc}")

    rows = full.values.tolist()
    try:
        cloud.overwrite_tab(result_sheet, list(s.OUTPUT_COLUMNS), rows)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN overwrite_tab gagal: {exc}")

    try:
        cloud.update_job(job_id, {})
    except Exception:  # noqa: BLE001
        pass

    out_path = f"Output_final_{job_id or 'result'}.csv"
    full.to_csv(out_path, index=False, encoding="utf-8-sig")

    file_id, link = "", ""
    try:
        file_id, link = cloud.upload_csv(
            out_path, name=out_path, share_email=os.environ.get("SHARE_EMAIL")
        )
    except Exception as exc:  # noqa: BLE001
        # Tidak fatal: Apps Script (akun pemilik) akan mengekspor CSV dari tab hasil.
        print(f"WARN upload Drive gagal (akan diekspor oleh Apps Script): {exc}")

    progress_text = f"100% ({len(full)} records)"
    if coverage_line:
        progress_text += " · " + coverage_line
    fields = {
        "status": status,
        "progress": progress_text,
        "overall_progress": 100,
        "finished_at": finished,
        "output_file_id": file_id,
        "output_url": link,
        "result_sheet": result_sheet,
        "records": len(full),
    }
    unique_missing = sorted(set(missing_todo))
    if status == "done" and unique_missing:
        status = "failed"
        fields["status"] = status
        fields["error"] = (
            f"Audit cakupan: {len(unique_missing)} target/chunk belum terverifikasi "
            f"(mis. {', '.join(unique_missing[:5])}). Klik Resume untuk melanjutkan — "
            "data tetap tersimpan di tab hasil ini."
        )
    if status == "failed":
        # Jangan menimpa pesan error spesifik dari worker bila sudah ada.
        try:
            existing_error = cloud.get_job_field(job_id, "error")
        except Exception:  # noqa: BLE001
            existing_error = ""
        if not existing_error and "error" not in fields:
            fields["error"] = (
                "Sebagian chunk gagal / tidak berjalan (workflow-result=" + args.workflow_result +
                "). Cek log GitHub Actions."
            )
    cloud.update_job(job_id, fields)
    cloud.append_log(job_id, user, f"selesai: {before} -> {len(full)} records (dedup), status={status}")
    print(f"MERGED {before} -> {len(full)} records | {link}", flush=True)


if __name__ == "__main__":
    main()
