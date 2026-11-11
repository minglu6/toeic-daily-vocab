#!/usr/bin/env python3
"""Build commute/lexicon.json from daily word-card pages (original entries only).

Sources, in order:
  1. days/*.html in this repo (complete cards with meaning + example)
  2. Optional extra JSON files passed with --extra (for later expansion batches)

Dedup is by hyphen/space-normalized key. Existing entries win over extras.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commute_lib import (  # noqa: E402
    DAYS,
    LEXICON_PATH,
    WORDS_PER_TRACK,
    norm_key,
    parse_day_cards,
    slug_of,
    spoken_meaning,
    spelling_text,
    speak_word,
)


def entries_from_days() -> list[dict]:
    out: list[dict] = []
    for path in sorted(DAYS.glob("*.html")):
        day = path.stem
        for item in parse_day_cards(path.read_text(encoding="utf-8")):
            if not item["word"] or not item["meaning"] or not item["en"] or not item["zh"]:
                print(f"skip incomplete {day} {item.get('word')!r}", file=sys.stderr)
                continue
            item["source"] = f"days/{path.name}"
            item["day"] = day
            item["batch"] = 1
            out.append(item)
    return out


def entries_from_json(path: Path, batch: int) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("entries") or data.get("words") or data.get("items") or []
    out: list[dict] = []
    for raw in data:
        w = (raw.get("word") or raw.get("w") or "").strip()
        meaning = (raw.get("meaning") or "").strip()
        en = (raw.get("en") or raw.get("ex_en") or "").strip()
        zh = (raw.get("zh") or raw.get("ex_zh") or "").strip()
        if not (w and meaning and en and zh):
            print(f"skip incomplete extra {w!r} in {path}", file=sys.stderr)
            continue
        out.append(
            {
                "word": w,
                "ipa": (raw.get("ipa") or "").strip(),
                "pos": (raw.get("pos") or "").strip(),
                "meaning": meaning,
                "en": en,
                "zh": zh,
                "source": f"extra:{path.name}",
                "day": raw.get("day") or "",
                "batch": int(raw.get("batch") or batch),
            }
        )
    return out


def enrich(item: dict, index: int) -> dict:
    word = item["word"]
    return {
        "index": index,
        "word": word,
        "slug": slug_of(word),
        "ipa": item.get("ipa") or "",
        "pos": item.get("pos") or "",
        "meaning": item["meaning"],
        "spoken_meaning": spoken_meaning(item.get("pos") or "", item["meaning"]),
        "spelling": spelling_text(word),
        "speak_word": speak_word(word),
        "en": item["en"],
        "zh": item["zh"],
        "source": item.get("source") or "",
        "day": item.get("day") or "",
        "batch": item.get("batch") or 1,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extra", action="append", default=[], help="extra JSON lexicon files")
    ap.add_argument("--out", default=str(LEXICON_PATH))
    args = ap.parse_args()

    raw: list[dict] = entries_from_days()
    for extra in args.extra:
        raw.extend(entries_from_json(Path(extra), batch=2))

    seen: set[str] = set()
    unique: list[dict] = []
    skipped = 0
    for item in raw:
        key = norm_key(item["word"])
        if not key or key in seen:
            skipped += 1
            continue
        seen.add(key)
        unique.append(item)

    entries = [enrich(it, i + 1) for i, it in enumerate(unique)]
    payload = {
        "title": "托业通勤听力词库（原创）",
        "disclaimer": "本词库与录音均为原创托业/商务英语学习材料，不是新东方或其他商业词书的原文或录音。",
        "word_count": len(entries),
        "words_per_track": WORDS_PER_TRACK,
        "track_count": (len(entries) + WORDS_PER_TRACK - 1) // WORDS_PER_TRACK,
        "entries": entries,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} entries={len(entries)} skipped_dup={skipped} tracks={payload['track_count']}")
    # sanity print
    for it in entries[:3] + [e for e in entries if "-" in e["word"]][:2]:
        print(f"  ex {it['word']}: {it['spelling']} | {it['spoken_meaning']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
