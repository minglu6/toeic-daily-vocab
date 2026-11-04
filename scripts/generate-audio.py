#!/usr/bin/env python3
"""Generate Quiet Paper click-to-play MP3s for TOEIC day pages.

Usage examples:
  # All unique .word spans across days/*.html (idempotent; skips existing)
  python3 scripts/generate-audio.py

  # Only words from one day page
  python3 scripts/generate-audio.py --day 2026-11-04

  # Explicit word list (hyphenated OK)
  python3 scripts/generate-audio.py --words "change-of-control,pay-band,raci"

  # Force regenerate
  python3 scripts/generate-audio.py --day 2026-11-04 --force

Requires:
  - edge-tts on PATH or at /workspace/.venv-tts/bin/edge-tts
  - ffmpeg on PATH

Output:
  audio/<slug>.mp3   slug = lowercase headword (hyphens kept)
  Speak text = hyphens replaced with spaces

Morning routine tip: after writing days/YYYY-MM-DD.html, run:
  python3 scripts/generate-audio.py --day YYYY-MM-DD
then commit audio/*.mp3 with the day page.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAYS = ROOT / "days"
AUDIO = ROOT / "audio"
VOICE = os.environ.get("TOEIC_TTS_VOICE", "en-US-JennyNeural")
BITRATE = os.environ.get("TOEIC_TTS_BITRATE", "32k")
CONCURRENCY = int(os.environ.get("TOEIC_TTS_CONCURRENCY", "8"))

WORD_RE = re.compile(r'<span class="word">([^<]+)</span>')


def find_edge_tts() -> str:
    env = os.environ.get("EDGE_TTS_BIN")
    if env and Path(env).exists():
        return env
    which = shutil.which("edge-tts")
    if which:
        return which
    candidate = Path("/workspace/.venv-tts/bin/edge-tts")
    if candidate.exists():
        return str(candidate)
    raise SystemExit("edge-tts not found; install edge-tts or set EDGE_TTS_BIN")


def find_ffmpeg() -> str:
    which = shutil.which("ffmpeg")
    if which:
        return which
    if Path("/usr/bin/ffmpeg").exists():
        return "/usr/bin/ffmpeg"
    raise SystemExit("ffmpeg not found")


def slug_of(word: str) -> str:
    return word.strip().lower()


def speak_text(word: str) -> str:
    return word.strip().replace("-", " ")


def words_from_day(day: str) -> list[str]:
    path = DAYS / f"{day}.html"
    if not path.exists():
        raise SystemExit(f"missing day page: {path}")
    return WORD_RE.findall(path.read_text(encoding="utf-8"))


def words_from_all_days() -> list[str]:
    found: list[str] = []
    for path in sorted(DAYS.glob("*.html")):
        found.extend(WORD_RE.findall(path.read_text(encoding="utf-8")))
    return found


async def synth_one(
    sem: asyncio.Semaphore,
    edge: str,
    ffmpeg: str,
    word: str,
    force: bool,
) -> tuple[str, str]:
    slug = slug_of(word)
    out = AUDIO / f"{slug}.mp3"
    if out.exists() and out.stat().st_size > 0 and not force:
        return slug, "skip"
    text = speak_text(word)
    async with sem:
        with tempfile.TemporaryDirectory(prefix="toeic-tts-") as tmp:
            raw = Path(tmp) / "raw.mp3"
            tiny = Path(tmp) / "tiny.mp3"
            proc = await asyncio.create_subprocess_exec(
                edge,
                "--voice",
                VOICE,
                "--text",
                text,
                "--write-media",
                str(raw),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _out, err = await proc.communicate()
            if proc.returncode != 0 or not raw.exists() or raw.stat().st_size == 0:
                return slug, f"tts-fail:{(err or b'').decode('utf-8', 'ignore')[:200]}"
            # Re-encode mono low-bitrate for Pages size
            ff = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-y",
                "-i",
                str(raw),
                "-ac",
                "1",
                "-ar",
                "24000",
                "-b:a",
                BITRATE,
                "-codec:a",
                "libmp3lame",
                str(tiny),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _o2, err2 = await ff.communicate()
            src = tiny if ff.returncode == 0 and tiny.exists() and tiny.stat().st_size > 0 else raw
            AUDIO.mkdir(parents=True, exist_ok=True)
            out.write_bytes(src.read_bytes())
            return slug, "ok"


async def run(words: list[str], force: bool) -> int:
    # unique preserve order
    seen = set()
    unique: list[str] = []
    for w in words:
        s = slug_of(w)
        if not s or s in seen:
            continue
        seen.add(s)
        unique.append(s)

    edge = find_edge_tts()
    ffmpeg = find_ffmpeg()
    AUDIO.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    print(f"voice={VOICE} bitrate={BITRATE} concurrency={CONCURRENCY} words={len(unique)}")
    results = await asyncio.gather(
        *[synth_one(sem, edge, ffmpeg, w, force) for w in unique]
    )
    ok = skip = fail = 0
    failures = []
    for slug, status in results:
        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        else:
            fail += 1
            failures.append((slug, status))
    print(f"done ok={ok} skip={skip} fail={fail}")
    for slug, status in failures[:30]:
        print(f"  FAIL {slug}: {status}")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", help="YYYY-MM-DD day page under days/")
    ap.add_argument("--words", help="Comma-separated headwords")
    ap.add_argument("--force", action="store_true", help="Regenerate even if mp3 exists")
    args = ap.parse_args()

    if args.words:
        words = [w.strip() for w in args.words.split(",") if w.strip()]
    elif args.day:
        words = words_from_day(args.day)
    else:
        words = words_from_all_days()

    if not words:
        print("no words found", file=sys.stderr)
        return 1
    return asyncio.run(run(words, args.force))


if __name__ == "__main__":
    raise SystemExit(main())
