"""Jalankan satu chunk job scraping + streaming hasil ke tab + update Sheet.

Dipakai oleh GitHub Actions:
  python worker/run_job.py --spec spec.json --provinsi "Jawa Barat" \
      --chunk-index 3 --total-chunks 8 --chunk-label "Jawa Barat [4/8]" --start 240 --end 320

Semua penulisan ke Google Sheets dilakukan lewat thread latar (non-blocking, dengan retry)
agar proses scraping tidak menunggu jaringan.
"""
import argparse
import json
import os
import queue
import random
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poi_scraper as s  # noqa: E402
import cloud  # noqa: E402

STREAM_INTERVAL = 30
PROGRESS_INTERVAL = 30
CHECKPOINT_INTERVAL = 180
ZERO_FLUSH_INTERVAL = 300


class ControlStop(BaseException):
    """Sinyal berhenti kooperatif dari kolom `control` di Sheet.

    Sengaja turunan BaseException agar tidak tertelan `except Exception`
    di dalam scraper.
    """

    def __init__(self, mode):
        self.mode = mode


class Writer:
    """Thread penulis tunggal: menjaga urutan, retry, dan tidak membuang baris."""

    def __init__(self, max_attempts=5):
        self.q = queue.Queue()
        self.errors = []
        self.data_errors = []
        self.max_attempts = max_attempts
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            item = self.q.get()
            try:
                if item is None:
                    return
                fn, args = item
                for attempt in range(1, self.max_attempts + 1):
                    try:
                        fn(*args)
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt >= self.max_attempts:
                            message = f"{getattr(fn, '__name__', fn)}: {exc}"
                            self.errors.append(message)
                            if getattr(fn, "__name__", "") == "append_rows":
                                self.data_errors.append(message)
                            print(f"ERROR writer (menyerah setelah {attempt}x): {exc}", flush=True)
                            break
                        sleep = min(30.0, 2.0 ** attempt) + random.uniform(0, 1)
                        print(f"WARN writer retry {attempt}/{self.max_attempts}: {exc}", flush=True)
                        time.sleep(sleep)
            finally:
                self.q.task_done()

    def submit(self, fn, *args):
        self.q.put((fn, args))

    def drain(self, raise_on_error=False):
        self.q.join()
        if raise_on_error and self.data_errors:
            raise RuntimeError("Penulisan baris hasil ke Sheet gagal: " + self.data_errors[-1])

    def stop(self):
        self.q.put(None)
        self.thread.join(timeout=30)


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


def _norm_admin(value):
    """Normalisasi nama target agar cocok lintas versi data (prefix Kabupaten/Kota)."""
    text = str(value or "").strip().lower()
    text = re.sub(r"\badm\.", "administrasi", text)
    text = re.sub(r"\bkep\.", "kepulauan", text)
    parts = []
    for part in text.split(","):
        part = re.sub(r"^(kabupaten|kota)\s+", "", part.strip())
        part = re.sub(r"\s+", " ", part)
        if part:
            parts.append(part)
    return ", ".join(parts)


def _compact_stats(stats):
    return {k: v for k, v in (stats or {}).items()
            if k not in ("zero_targets", "failed_targets_list")}


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
    parser.add_argument("--chunk-label", default="", help="Label unik chunk (untuk resume & tampilan).")
    parser.add_argument("--start", default="", help="Indeks awal irisan target di provinsi ini.")
    parser.add_argument("--end", default="", help="Indeks akhir (eksklusif) irisan target.")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    job_id = spec.get("job_id", "")
    user = spec.get("user", "")
    profile = s.get_profile(spec.get("country", "indonesia"))
    result_sheet = spec.get("result_sheet", "") or f"Result_{job_id}"
    headers = list(s.OUTPUT_COLUMNS)

    chunk_idx = args.chunk_index if args.chunk_index is not None else 0
    total_chunks = max(1, int(args.total_chunks or 1))
    chunk_label = args.chunk_label or args.provinsi or "semua"
    chunk_pos = f"{chunk_idx + 1}/{total_chunks}"

    if args.provinsi == "__SKIP__":
        cloud.append_log(job_id, user, "resume: seluruh chunk sudah selesai, chunk dilewati")
        print("CHUNK_DONE " + json.dumps({"skipped": True}, ensure_ascii=False), flush=True)
        return

    if args.provinsi:
        spec.setdefault("scope", {})
        spec["scope"][profile["levels"][0]] = [args.provinsi]
    start_arg = str(args.start or "").strip()
    end_arg = str(args.end or "").strip()
    if start_arg:
        spec["target_start"] = int(start_arg)
    if end_arg:
        spec["target_end"] = int(end_arg)
    spec["chunk_index"] = chunk_idx
    spec["total_chunks"] = total_chunks
    spec["output_dir"] = "output"

    planned_hint = 0
    if start_arg or end_arg:
        try:
            planned_hint = int(end_arg or 0) - int(start_arg or 0)
        except ValueError:
            planned_hint = 0

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

    def _chunk_update(fields):
        if job_id:
            try:
                cloud.upsert_chunk(job_id, chunk_idx, fields)
            except Exception as exc:  # noqa: BLE001
                print(f"WARN upsert_chunk gagal: {exc}")

    _async(cloud.upsert_chunk, job_id, chunk_idx, {
        "label": chunk_label, "status": "running", "planned": planned_hint, "done": 0,
    })
    _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} mulai (wilayah='{chunk_label}')")

    start_ts = time.time()
    last_emit = [0.0]
    last_ckpt = [0.0]
    last_zero_flush = [0.0]
    streamed = [0]
    skipped = [0]
    last_flush = [0.0]
    last_data = [[]]
    last_stats = [{}]

    target_column = cloud._col_letter(list(s.OUTPUT_COLUMNS).index("Query_Target"))

    def fetch_done_targets():
        """Target yang barisnya sudah masuk tab hasil (ternormalisasi)."""
        values = cloud.fetch_column_values(result_sheet, target_column)
        done = {_norm_admin(v) for v in values if str(v).strip()}
        done.discard(_norm_admin("Query_Target"))
        return done

    done_targets = None
    try:
        done_targets = fetch_done_targets()
    except Exception as exc:  # noqa: BLE001
        _safe_update(job_id, {
            "status": "failed",
            "error": f"Gagal membaca checkpoint tab hasil: {exc}",
        })
        writer.drain()
        _chunk_update({
            "status": "failed",
            "planned": planned_hint,
            "done": 0,
            "stats_json": json.dumps({"error": str(exc)}, ensure_ascii=False),
        })
        _async(cloud.append_log, job_id, user,
               f"chunk {chunk_pos} gagal membaca checkpoint tab hasil: {exc}")
        writer.drain()
        writer.stop()
        raise
    if done_targets is None:
        done_targets = set()

    def _filter(targets):
        if not done_targets:
            return targets
        idxs = [i for i, t in enumerate(targets) if _norm_admin(t["admin"]) in done_targets]
        if not idxs:
            return targets
        last = max(idxs)
        kept = [t for i, t in enumerate(targets) if i == last or _norm_admin(t["admin"]) not in done_targets]
        skipped[0] = len(targets) - len(kept)
        if skipped[0] > 0:
            message = (
                f"chunk {chunk_pos}: resume — lewati {skipped[0]} target selesai, "
                f"ulang target terakhir yang dikerjakan, sisa {len(kept)} target"
            )
            _async(cloud.append_log, job_id, user, message)
            print("RESUME " + message, flush=True)
        return kept

    def on_progress(stats, current, total, scraped, admin=""):
        last_stats[0] = stats
        now = time.time()
        if now - last_emit[0] < PROGRESS_INTERVAL:
            return
        last_emit[0] = now
        try:
            ctrl = str(cloud.get_job_field(job_id, "control") or "").strip().lower()
        except Exception:
            ctrl = ""
        if ctrl in ("pause", "stop"):
            raise ControlStop(ctrl)
        if now - last_ckpt[0] >= CHECKPOINT_INTERVAL:
            last_ckpt[0] = now
            _async(cloud.upsert_chunk, job_id, chunk_idx, {
                "status": "running",
                "planned": planned_hint,
                "done": skipped[0] + current,
                "stats_json": json.dumps(_compact_stats(stats), ensure_ascii=False),
            })
        if stats.get("zero_targets") and now - last_zero_flush[0] >= ZERO_FLUSH_INTERVAL:
            last_zero_flush[0] = now
            _async(_append_zero_targets, job_id, list(stats["zero_targets"]))
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
            "filter_stats_json": json.dumps(_compact_stats(stats), ensure_ascii=False),
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

    def flush_remaining():
        remaining = last_data[0][streamed[0]:]
        if not remaining:
            return
        rows = [list(r) for r in remaining]
        streamed[0] = len(last_data[0])
        _async(cloud.append_rows, result_sheet, rows)

    manifest = None
    try:
        manifest = s.run_job(str(spec_file), progress_callback=on_progress, data_callback=on_data,
                             target_filter=_filter if done_targets else None)
        flush_remaining()
        _async(_append_zero_targets, job_id, last_stats[0].get("zero_targets", []))
        writer.drain(raise_on_error=True)
    except ControlStop as stop:
        checkpoint = {"chunk_index": chunk_idx, "chunk_label": chunk_label, "reason": stop.mode}
        new_status = "paused" if stop.mode == "pause" else "cancelled"
        flush_remaining()
        _async(_append_zero_targets, job_id, last_stats[0].get("zero_targets", []))
        _async(cloud.update_job, job_id, {
            "status": new_status,
            "checkpoint_json": json.dumps(checkpoint, ensure_ascii=False),
        })
        _async(cloud.upsert_chunk, job_id, chunk_idx, {
            "status": new_status,
            "planned": planned_hint,
            "done": skipped[0] + last_stats[0].get("rows_completed", 0),
            "failed_json": json.dumps(last_stats[0].get("failed_targets_list", []), ensure_ascii=False),
            "stats_json": json.dumps(_compact_stats(last_stats[0]), ensure_ascii=False),
        })
        _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} dihentikan ({stop.mode}) pada target terakhir")
        writer.drain()
        writer.stop()
        print("CHUNK_STOP " + json.dumps(checkpoint, ensure_ascii=False), flush=True)
        return
    except Exception as exc:  # noqa: BLE001
        flush_remaining()
        _async(_append_zero_targets, job_id, last_stats[0].get("zero_targets", []))
        _async(cloud.update_job, job_id, {"status": "failed", "error": str(exc)})
        _async(cloud.upsert_chunk, job_id, chunk_idx, {
            "status": "failed",
            "planned": planned_hint,
            "done": skipped[0] + last_stats[0].get("rows_completed", 0),
            "failed_json": json.dumps(last_stats[0].get("failed_targets_list", []), ensure_ascii=False),
            "stats_json": json.dumps(_compact_stats(last_stats[0]), ensure_ascii=False),
        })
        _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} GAGAL: {exc}")
        writer.drain()
        writer.stop()
        print(f"ERROR {exc}", flush=True)
        raise
    except BaseException as exc:  # noqa: BLE001
        # Jaring pengaman: jangan biarkan status job nyangkut 'running'.
        flush_remaining()
        _async(cloud.update_job, job_id, {"status": "failed", "error": f"Abort tak terduga: {exc}"})
        _async(cloud.append_log, job_id, user, f"chunk {chunk_pos} abort tak terduga: {exc}")
        writer.drain()
        writer.stop()
        raise

    failed_list = list(last_stats[0].get("failed_targets_list", []))[:500]
    final_status = "partial" if failed_list else "done"
    _chunk_update({
        "status": final_status,
        "planned": manifest.get("targets_planned", manifest.get("targets", 0)),
        "done": manifest.get("targets_planned", manifest.get("targets", 0)),
        "failed_json": json.dumps(failed_list, ensure_ascii=False),
        "stats_json": json.dumps(_compact_stats(last_stats[0]), ensure_ascii=False),
    })
    if failed_list:
        _async(
            cloud.append_log, job_id, user,
            f"chunk {chunk_pos} selesai dengan {len(failed_list)} target gagal — "
            "target tersebut akan dicoba lagi saat Resume",
        )
    else:
        _async(
            cloud.append_log, job_id, user,
            f"chunk {chunk_pos} selesai: {manifest.get('records', 0)} records, "
            f"{manifest.get('targets', 0)} target",
        )
    writer.drain()
    writer.stop()
    print("CHUNK_DONE " + json.dumps(manifest, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
