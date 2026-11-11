#!/usr/bin/env python3
"""Validate commute/expansion-batch2.json (original batch-2 entries).

These headwords, glosses, and example sentences were written for this
project and are not copied from any commercial vocabulary book.

Used as:
  python3 scripts/build_commute_lexicon.py --extra commute/expansion-batch2.json
  python3 scripts/generate_commute_audio.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commute_lib import COMMUTE, LEXICON_PATH, norm_key  # noqa: E402

EXTRA = COMMUTE / "expansion-batch2.json"


def main() -> int:
    data = json.loads(EXTRA.read_text(encoding="utf-8"))
    entries = data["entries"]
    keys = [norm_key(e["word"]) for e in entries]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    missing = [e.get("word") for e in entries if not all(e.get(f) for f in ("word", "pos", "meaning", "en", "zh"))]
    print(f"{EXTRA.name}: {len(entries)} entries, dups={len(dups)}, incomplete={len(missing)}")
    if LEXICON_PATH.exists():
        lex = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
        have = {norm_key(e["word"]) for e in lex.get("entries", []) if e.get("batch", 1) == 1}
        overlap = [e["word"] for e in entries if norm_key(e["word"]) in have]
        print(f"overlap with batch-1 lexicon: {len(overlap)}")
    if dups or missing:
        print("dups:", dups[:10], "missing:", missing[:10], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
