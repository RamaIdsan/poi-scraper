"""Hitung chunk (per provinsi) dari spec, cetak matrix untuk GitHub Actions.

Contoh: python worker/plan_chunks.py --spec spec.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import poi_scraper as s  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, help="Path spec JSON.")
    parser.add_argument("--threshold", type=int, default=40, help="Ambang target sebelum dipecah.")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
    profile = s.get_profile(spec.get("country", "indonesia"))
    chunks, counts = s.plan_chunks(spec, profile, threshold=args.threshold)

    if len(chunks) == 1:
        labels = [""]
    else:
        top_level = profile["levels"][0]
        labels = [c["scope"][top_level][0] for c in chunks]

    n = len(labels)
    matrix = {
        "chunk": labels,
        "idx": list(range(n)),
        "total": [n] * n,
    }

    print(json.dumps({"matrix": matrix, "chunks": n, "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
