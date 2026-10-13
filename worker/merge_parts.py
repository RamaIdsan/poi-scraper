"""Gabungkan CSV hasil tiap chunk, dedup, tulis ke tab hasil, upload Drive."""
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


def parse_env_list(name):
    try:
        value = json.loads(os.environ.get(name) or "[]")
    except Exception:  # noqa: BLE001
        return []
    return value if isinstance(value, list) else []


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

    # 1) Dedup berdasarkan Place_ID (bila ada) agar duplikat lintas-chunk pasti hilang.
    if "Place_ID" in full.columns:
        pid = full["Place_ID"].astype(str).str.strip()
        has_pid = pid != ""
        by_pid = full[has_pid].drop_duplicates(subset=["Place_ID"])
        rest = full[~has_pid]
        full = pd.concat([by_pid, rest], ignore_index=True)

    # 2) Dedup fallback berdasarkan brand/nama/alamat/koordinat.
    keys = [c for c in DEDUP_KEYS if c in full.columns]
    full = full.drop_duplicates(subset=keys) if keys else full.drop_duplicates()

    # 3) Audit cakupan target: bandingkan target yang direncanakan vs yang ada di hasil.
    coverage_line = ""
    missing_todo = set()
    all_entries = parse_env_list("ALL_ENTRIES")
    todo_entries = parse_env_list("PLAN_ENTRIES") or all_entries
    if all_entries:
        try:
            profile = s.get_profile(spec.get("country", "indonesia"))
            admin_df = s.load_admin_data(profile)
            expected_all = set()
            for entry in all_entries:
                expected_all |= entry_targets(spec, profile, admin_df, entry)
            expected_todo = set()
            for entry in todo_entries:
                expected_todo |= entry_targets(spec, profile, admin_df, entry)
            present = {
                str(v).strip() for v in full.get("Query_Target", pd.Series(dtype=str))
                if str(v).strip()
            }
            zeros = set()
            try:
                zeros = set(json.loads(cloud.get_job_field(job_id, "zero_targets_json") or "[]") or [])
            except Exception:  # noqa: BLE001
                zeros = set()
            verified = present | {str(z).strip() for z in zeros if str(z).strip()}
            missing_all = expected_all - verified
            missing_todo = expected_todo - verified
            total_expected = len(expected_all)
            covered = total_expected - len(missing_all)
            coverage_line = f"cakupan target: {covered}/{total_expected}"
            if missing_all:
                sample = ", ".join(sorted(missing_all)[:10])
                coverage_line += f" · belum terverifikasi {len(missing_all)} (mis. {sample})"
            cloud.append_log(job_id, user, "audit " + coverage_line)
            print("AUDIT " + coverage_line, flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN audit cakupan gagal: {exc}")

    rows = full.values.tolist()
    try:
        cloud.overwrite_tab(result_sheet, list(s.OUTPUT_COLUMNS), rows)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN overwrite_tab gagal: {exc}")

    out_path = f"Output_final_{job_id or 'result'}.csv"
    full.to_csv(out_path, index=False, encoding="utf-8-sig")

    file_id, link = "", ""
    try:
        file_id, link = cloud.upload_csv(
            out_path, name=out_path, share_email=os.environ.get("SHARE_EMAIL")
        )
    except Exception as exc:  # noqa: BLE001
        # Tidak fatal: Apps Script (akun pemilik) akan mengekspor CSV dari tab hasil
        # via exportCompleted(), karena service account tidak punya kuota Drive.
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
    }
    if status == "done" and missing_todo:
        # Ada target dari chunk yang dijalankan dispatch ini yang belum terverifikasi
        # (gagal/terhenti). Tandai failed agar tombol Resume bisa mengejarnya.
        status = "failed"
        fields["status"] = status
        fields["error"] = (
            f"Audit cakupan: {len(missing_todo)} target belum terverifikasi dari chunk yang "
            "dijalankan. Klik Resume untuk melanjutkan — data tetap tersimpan di tab hasil ini."
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
