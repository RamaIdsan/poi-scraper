"""Jalankan satu chunk job scraping + streaming hasil ke tab + update Sheet.

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

STREAM_INTERVAL = 20  # detik


def _safe_update(job_id, fields):
    if not job_id:
        return
    try:
        cloud.update_job(job_id, fields)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN update_job gagal: {exc}")


def _fmt_eta(seconds):
    try:
        seconds = int(max(0, seconds))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02}:{m:02}:{sec:02}"


def _run_url():
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--provinsi", default="")
    parser.add_argument("--chunk-index", type=int, default=None)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    job_id = spec.get("job_id", "")
    user = spec.get("user", "")
    profile = s.get_profile(spec.get("country", "indonesia"))
    result_sheet = spec.get("result_sheet", "") or f"Result_{job_id}"
    headers = list(s.OUTPUT_COLUMNS)

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

    try:
        cloud.ensure_header(result_sheet, headers)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN ensure_header gagal: {exc}")

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    _safe_update(job_id, {
        "status": "running",
        "started_at": started,
        "run_url": _run_url(),
    })
    cloud.append_log(job_id, user, f"chunk {args.chunk_index} mulai (provinsi='{args.provinsi or 'semua'}')")

    start_ts = time.time()
    last_emit = [0.0]
    streamed = [0]
    last_flush = [0.0]
    last_data = [[]]

    def on_progress(stats, current, total, scraped, admin=""):
        now = time.time()
        if now - last_emit[0] < 15:
            return
        last_emit[0] = now
        progress = round((current / total) * 100, 2) if total else 100.0
        elapsed = now - start_ts
        eta = (elapsed / current) * (total - current) if current > 0 else 0
        _safe_update(job_id, {
            "progress": f"{progress}%",
            "current_target": admin,
            "listings_found": stats.get("listings_found", 0),
            "records": scraped,
            "eta": _fmt_eta(eta),
        })
        print(f"PROGRESS {progress}% ({scraped} records) {current}/{total} | {admin}", flush=True)

    def on_data(data):
        last_data[0] = data
        now = time.time()
        if now - last_flush[0] < STREAM_INTERVAL:
            return
        new_rows = data[streamed[0]:]
        if not new_rows:
            return
        try:
            cloud.append_rows(result_sheet, [list(r) for r in new_rows])
            streamed[0] = len(data)
            last_flush[0] = now
            _safe_update(job_id, {"records": streamed[0]})
        except Exception as exc:  # noqa: BLE001
            print(f"WARN streaming gagal: {exc}")

    try:
        manifest = s.run_job(str(spec_file), progress_callback=on_progress, data_callback=on_data)
    except Exception as exc:  # noqa: BLE001
        _safe_update(job_id, {"status": "failed", "error": str(exc)})
        cloud.append_log(job_id, user, f"chunk {args.chunk_index} GAGAL: {exc}")
        print(f"ERROR {exc}", flush=True)
        raise

    # Flush sisa baris yang belum terkirim.
    remaining = last_data[0][streamed[0]:]
    if remaining:
        try:
            cloud.append_rows(result_sheet, [list(r) for r in remaining])
            streamed[0] = len(last_data[0])
            _safe_update(job_id, {"records": streamed[0]})
        except Exception as exc:  # noqa: BLE001
            print(f"WARN flush akhir gagal: {exc}")

    cloud.append_log(
        job_id, user,
        f"chunk {args.chunk_index} selesai: {manifest.get('records', 0)} records, {manifest.get('targets', 0)} target",
    )
    print("CHUNK_DONE " + json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
