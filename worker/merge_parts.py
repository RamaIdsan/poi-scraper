"""Gabungkan CSV hasil tiap chunk, dedup, tulis ke tab hasil, upload Drive."""
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import scraper_indonesia as s  # noqa: E402
import cloud  # noqa: E402

DEDUP_KEYS = [
    "Brand", "Name_geocode", "Address_geocode",
    "Latitude_geocode", "Longitude_geocode",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--parts", default="parts")
    parser.add_argument("--workflow-result", default="success")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    job_id = spec.get("job_id", "")
    user = spec.get("user", "")
    result_sheet = spec.get("result_sheet", "") or f"Result_{job_id}"

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

    finished = time.strftime("%Y-%m-%d %H:%M:%S")
    status = "done" if args.workflow_result == "success" else "failed"

    full = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=s.OUTPUT_COLUMNS)
    full = full.reindex(columns=s.OUTPUT_COLUMNS).fillna("")
    before = len(full)
    keys = [c for c in DEDUP_KEYS if c in full.columns]
    full = full.drop_duplicates(subset=keys) if keys else full.drop_duplicates()

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
        print(f"WARN upload Drive gagal: {exc}")
        try:
            cloud.update_job(job_id, {"error": "upload Drive gagal: " + str(exc)[:300]})
        except Exception:  # noqa: BLE001
            pass

    cloud.update_job(job_id, {
        "status": status,
        "progress": f"100% ({len(full)} records)",
        "finished_at": finished,
        "output_file_id": file_id,
        "output_url": link,
        "result_sheet": result_sheet,
    })
    cloud.append_log(job_id, user, f"selesai: {before} -> {len(full)} records (dedup), status={status}")
    print(f"MERGED {before} -> {len(full)} records | {link}", flush=True)


if __name__ == "__main__":
    main()
