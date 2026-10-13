"""Hitung chunk dari spec, cetak matrix untuk GitHub Actions.

Chunk = per provinsi, dipecah lagi per jumlah target (default 80) agar tiap job
jauh dari batas waktu GitHub Actions. Setiap entry membawa provinsi + irisan target
(`start`/`end`) + label unik untuk resume.

Contoh: python worker/plan_chunks.py --spec spec.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poi_scraper as s  # noqa: E402


def build_entry(idx, chunk, total_chunks, profile, single):
    top_level = profile["levels"][0]
    scope = chunk.get("scope") or {}
    prov = (scope.get(top_level) or [""])[0]
    if single:
        # Chunk tunggal: pakai spec apa adanya (jangan menimpa scope).
        return {"idx": idx, "chunk": "semua", "total": total_chunks,
                "prov": "", "start": None, "end": None}
    label = chunk.get("chunk_label") or prov
    return {"idx": idx, "chunk": label, "total": total_chunks, "prov": prov,
            "start": chunk.get("target_start"), "end": chunk.get("target_end")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, help="Path spec JSON.")
    parser.add_argument("--threshold", type=int, default=40, help="Ambang target sebelum dipecah.")
    parser.add_argument("--chunk-size", type=int, default=80, help="Maksimum target per chunk.")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    profile = s.get_profile(spec.get("country", "indonesia"))
    chunks, counts = s.plan_chunks(spec, profile, threshold=args.threshold, chunk_size=args.chunk_size)

    single = len(chunks) == 1
    all_entries = [
        build_entry(i, c, len(chunks), profile, single)
        for i, c in enumerate(chunks)
    ]

    done = set(spec.get("resume_done_chunks") or [])
    entries = [
        e for e in all_entries
        if e["chunk"] not in done and not (e["prov"] and e["prov"] in done)
    ]
    if not entries:
        entries = [{"idx": 0, "chunk": "__SKIP__", "total": len(chunks),
                    "prov": "__SKIP__", "start": None, "end": None}]

    print(json.dumps({
        "idxs": [e["idx"] for e in entries],
        "entries": entries,
        "all_entries": all_entries,
        "chunks": len(chunks),
        "todo": len(entries),
        "counts": counts,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
