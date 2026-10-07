"""Jalankan satu chunk job scraping + streaming hasil ke tab + update Sheet.

Dipakai oleh GitHub Actions:
  python worker/run_job.py --spec spec.json --provinsi "Abra" --chunk-index 0 --total-chunks 81

Semua penulisan ke Google Sheets dilakukan lewat thread latar (non-blocking) agar
proses scraping tidak menunggu jaringan.
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poi_scraper as s  # noqa: E402
import cloud  # noqa: E402

STREAM_INTERVAL = 20  # detik


class ControlStop(BaseException):
    """Sinyal berhenti kooperatif dari kolom `control` di Sheet.

    Sengaja turunan BaseException agar tidak tertelan `except Exception`
    di dalam scraper.
    """

    def __init__(self, mode):
        self.mode = mode


class Writer:
    """Thread penulis tunggal: menjaga urutan & tidak memblokir scraping."""

    def __init__(self):
        self.q = queue.Queue()
        self.errors = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            item = self.q.get()
            try:
                if item is None:
                    return
                fn, args = item
                try:
                    fn(*args)
                except Exception as exc:  # noqa: BLE001
                    self.errors.append(str(exc))
                    print(f"WARN writer: {exc}", flush=True)
            finally:
                self.q.task_done()

    def submit(self, fn, *args):
        self.q.put((fn, args))

    def drain(self):
        self.q.join()

    def stop(self):
        self.q.put(None)
        self.thread.join(timeout=20)


def _safe_update(job_id, fields):
    """Update sinkron (dipakai untuk status penting sebelum loop)."""
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


def _run_id():
    return os.environ.get("GITHUB_RUN_ID", "")


def _update_chunk_json(job_id, idx, label, status, records, stats=None):
    """Catat status tiap chunk di kolom chunks_json (dijalankan di thread writer)."""
    try:
        raw = cloud.get_job_field(job_id, "chunks_json") or ""
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    entry = {"label": label, "status": status, "records": records}
    if stats:
        # Hanya angka ringkas; daftar zero_targets tidak dimasukkan agar kolom
        # chunks_json tidak melebihi batas 50.000 karakter.
        entry["stats"] = {k: v for k, v in stats.items() if k != "zero_targets"}
    data[str(idx)] = entry
    cloud.update_job(job_id, {"chunks_json": json.dumps(data, ensure_ascii=False)})


def _append_zero_targets(job_id, zero_list):
    """Akumulasi target 0 hasil ke kolom zero_targets_json (maks 500)."""
    if not zero_list:
        return
    try:
        raw = cloud.get_job_field(job_id, "zero_targets_json") or ""
        existing = json.loads(raw) if raw.strip().startswith("[") else []
        if not isinstance(existing, list):
            existing = []
    except Exception:
        existing = []
    seen = set(existing)
    for z in zero_list:
        z = str(z or "").strip()
        if z and z not in seen:
            existing.append(z)
            seen.add(z)
    existing = existing[-500:]
    cloud.update_job(job_id, {"zero_targets_json": json.dumps(existing, ensure_ascii=False)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--provinsi", default="")
    parser.add_argument("--chunk-index", type=int, default=None)
    parser.add_argument("--total-chunks", type=int, default=1)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    job_id = spec.get("job_id", "")
    user = spec.get("user", "")
    profile = s.get_profile(spec.get("country", "indonesia"))
    result_sheet = spec.get("result_sheet", "") or f"Result_{job_id}"
    headers = list(s.OUTPUT_COLUMNS)

    chunk_idx = args.chunk_index if args.chunk_index is not None else 0
    total_chunks = max(1, int(args.total_chunks or 1))
    chunk_label = args.provinsi or "semua"
    chunk_pos = f"{chunk_idx + 1}/{total_chunks}"

    if args.provinsi == "__SKIP__":
        cloud.append_log(job_id, user, "resume: seluruh chunk sudah selesai, chunk dilewati")
        print("CHUNK_DONE " + json.dumps({"skipped": True}, ensure_ascii=False), flush=True)
        return

    if args.provinsi:
        spec.setdefault("scope", {})
        spec["scope"][profile["levels"][0]] = [args.provinsi]
    spec["chunk_index"] = chunk_idx
    spec["total_chunks"] = total_chunks
    spec["output_dir"] = "output"

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_file = out_dir / f"spec_chunk_{chunk_idx}.json"
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
        "run_id": _run_id(),
        "current_chunk": chunk_pos,
        "total_chunks": total_chunks,
    })

    writer = Writer()

    def _async(fn, *fn_args):
        writer.submit(fn, *fn_args)

    _async(_update_chunk_json, job_id, chunk_idx, chunk_label, "running", 0, None)
    _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} mulai (wilayah='{chunk_label}')")

    start_ts = time.time()
    last_emit = [0.0]
    streamed = [0]
    last_flush = [0.0]
    last_data = [[]]
    last_stats = [{}]

    def on_progress(stats, current, total, scraped, admin=""):
        last_stats[0] = stats
        now = time.time()
        if now - last_emit[0] < 15:
            return
        last_emit[0] = now
        try:
            ctrl = str(cloud.get_job_field(job_id, "control") or "").strip().lower()
        except Exception:
            ctrl = ""
        if ctrl in ("pause", "stop"):
            raise ControlStop(ctrl)
        local = (current / total) if total else 1.0
        overall = round(((chunk_idx + local) / total_chunks) * 100, 2)
        elapsed = now - start_ts
        eta = (elapsed / current) * (total - current) if current > 0 else 0
        _async(cloud.update_job, job_id, {
            "progress": f"chunk {chunk_pos} · {round(local * 100, 1)}%",
            "overall_progress": overall,
            "current_chunk": chunk_pos,
            "total_chunks": total_chunks,
            "current_target": admin,
            "listings_found": stats.get("listings_found", 0),
            "records": scraped,
            "eta": _fmt_eta(eta),
            "filter_stats_json": json.dumps(stats, ensure_ascii=False),
        })
        print(f"PROGRESS overall={overall}% chunk={chunk_pos} local={round(local*100,1)}% ({scraped} records) {current}/{total} | {admin}", flush=True)

    def on_data(data):
        last_data[0] = data
        now = time.time()
        if now - last_flush[0] < STREAM_INTERVAL:
            return
        new_rows = data[streamed[0]:]
        if not new_rows:
            return
        rows = [list(r) for r in new_rows]
        streamed[0] = len(data)
        last_flush[0] = now
        _async(cloud.append_rows, result_sheet, rows)
        _async(cloud.update_job, job_id, {"records": streamed[0]})

    def flush_remaining():
        remaining = last_data[0][streamed[0]:]
        if not remaining:
            return
        rows = [list(r) for r in remaining]
        streamed[0] = len(last_data[0])
        _async(cloud.append_rows, result_sheet, rows)
        _async(cloud.update_job, job_id, {"records": streamed[0]})

    try:
        manifest = s.run_job(str(spec_file), progress_callback=on_progress, data_callback=on_data)
    except ControlStop as stop:
        checkpoint = {"chunk_index": chunk_idx, "chunk_label": chunk_label, "reason": stop.mode}
        new_status = "paused" if stop.mode == "pause" else "cancelled"
        flush_remaining()
        _async(_append_zero_targets, job_id, last_stats[0].get("zero_targets", []))
        _async(cloud.update_job, job_id, {
            "status": new_status,
            "checkpoint_json": json.dumps(checkpoint, ensure_ascii=False),
        })
        _async(_update_chunk_json, job_id, chunk_idx, chunk_label, new_status, streamed[0], last_stats[0])
        _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} dihentikan ({stop.mode}) pada target terakhir")
        writer.drain()
        writer.stop()
        print("CHUNK_STOP " + json.dumps(checkpoint, ensure_ascii=False), flush=True)
        return
    except Exception as exc:  # noqa: BLE001
        flush_remaining()
        _async(_append_zero_targets, job_id, last_stats[0].get("zero_targets", []))
        _async(cloud.update_job, job_id, {"status": "failed", "error": str(exc)})
        _async(_update_chunk_json, job_id, chunk_idx, chunk_label, "failed", streamed[0], last_stats[0])
        _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} GAGAL: {exc}")
        writer.drain()
        writer.stop()
        print(f"ERROR {exc}", flush=True)
        raise

    flush_remaining()
    _async(_append_zero_targets, job_id, last_stats[0].get("zero_targets", []))
    _async(_update_chunk_json, job_id, chunk_idx, chunk_label, "done", streamed[0], last_stats[0])
    _async(
        cloud.append_log, job_id, user,
        f"chunk {chunk_pos} selesai: {manifest.get('records', 0)} records, {manifest.get('targets', 0)} target",
    )
    writer.drain()
    writer.stop()
    print("CHUNK_DONE " + json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
