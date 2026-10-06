"""Jalankan satu chunk job scraping + update Sheet + upload CSV ke Drive.

Dipakai oleh GitHub Actions:
  python worker/run_job.py --spec spec.json --provinsi "Abra" --chunk-index 0
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import scraper_indonesia as s  # noqa: E402
import cloud  # noqa: E402


def _safe_update(job_id, fields):
    try:
        cloud.update_job(job_id, fields)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN update_job gagal: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--provinsi", default="")
    parser.add_argument("--chunk-index", type=int, default=None)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    job_id = spec.get("job_id", "")
    profile = s.get_profile(spec.get("country", "indonesia"))

    if args.provinsi:
        spec.setdefault("scope", {})
        spec["scope"][profile["levels"][0]] = [args.provinsi]
    if args.chunk_index is not None:
        spec["chunk_index"] = args.chunk_index
    spec["output_dir"] = "output"

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_file = out_dir / f"spec_chunk_{args.chunk_index if args.chunk_index is not None else 0}.json"
    spec_file.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    _safe_update(job_id, {"status": "running", "started_at": started})

    last_emit = [0.0]

    def on_progress(stats, current, total, scraped):
        now = time.time()
        if now - last_emit[0] < 30:
            return
        last_emit[0] = now
        progress = round((current / total) * 100, 2) if total else 100.0
        _safe_update(job_id, {"progress": f"{progress}% ({scraped} records) | {current}/{total}"})
        print(f"PROGRESS {progress}% ({scraped} records) {current}/{total}", flush=True)

    try:
        manifest = s.run_job(str(spec_file), progress_callback=on_progress)
    except Exception as exc:  # noqa: BLE001
        _safe_update(job_id, {"status": "failed", "error": str(exc)})
        print(f"ERROR {exc}", flush=True)
        raise

    # Chunk tidak diupload ke Drive; cukup sebagai artifact. Job "merge" yang
    # menggabungkan semua chunk dan mengunggah satu file final ke Drive.
    print("CHUNK_DONE " + json.dumps(manifest, ensure_ascii=False), flush=True)
    # status final ditentukan oleh job merge (agar link final = hasil gabung)


if __name__ == "__main__":
    main()
