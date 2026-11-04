#!/usr/bin/env python3
"""Generate Quiet Paper click-to-play MP3s for TOEIC day pages.

Headwords → audio/<slug>.mp3
Example sentences → audio/ex/<sha256-16>.mp3  (en-US-JennyNeural)

Hash = SHA-256 hex[:16] of UTF-8(casefold(collapse_ws(strip(text)))).
Day pages are patched so each <en> gets data-audio="ex/<hash>.mp3".

Usage examples:
  # All unique headwords + example sentences across days/*.html
  python3 scripts/generate-audio.py

  # One day: words + examples, and patch that day's <en> tags
  python3 scripts/generate-audio.py --day 2026-11-04

  # Examples only (still patches HTML)
  python3 scripts/generate-audio.py --examples-only
  python3 scripts/generate-audio.py --day 2026-11-04 --examples-only

  # Words only
  python3 scripts/generate-audio.py --words-only --day 2026-11-04

  # Explicit word list (hyphenated OK)
  python3 scripts/generate-audio.py --words "change-of-control,pay-band,raci"

  # Force regenerate
  python3 scripts/generate-audio.py --day 2026-11-04 --force

Requires:
  - edge-tts on PATH or at /workspace/.venv-tts/bin/edge-tts
  - ffmpeg on PATH

Voice default: en-US-JennyNeural (same as listening-practice female / narration family).

Morning routine tip: after writing days/YYYY-MM-DD.html, run:
  python3 scripts/generate-audio.py --day YYYY-MM-DD
then commit audio/*.mp3, audio/ex/*.mp3, and the patched day page.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAYS = ROOT / "days"
AUDIO = ROOT / "audio"
AUDIO_EX = AUDIO / "ex"
VOICE = os.environ.get("TOEIC_TTS_VOICE", "en-US-JennyNeural")
BITRATE = os.environ.get("TOEIC_TTS_BITRATE", "32k")
CONCURRENCY = int(os.environ.get("TOEIC_TTS_CONCURRENCY", "8"))

WORD_RE = re.compile(r'<span class="word">([^<]+)</span>')
# Capture optional existing attrs so re-patch is idempotent
EN_RE = re.compile(r"<en(\s[^>]*)?>(.*?)</en>", re.S)


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


def speak_text_word(word: str) -> str:
    return word.strip().replace("-", " ")


def normalize_sentence(text: str) -> str:
    """Shared hash key: trim, collapse whitespace, casefold."""
    return re.sub(r"\s+", " ", text.strip()).casefold()


def speak_text_sentence(text: str) -> str:
    """TTS text: trim + collapse whitespace; keep original case."""
    return re.sub(r"\s+", " ", text.strip())


def sentence_hash(text: str) -> str:
    return hashlib.sha256(normalize_sentence(text).encode("utf-8")).hexdigest()[:16]


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


def sentences_from_html(html: str) -> list[str]:
    return [m.group(2) for m in EN_RE.finditer(html)]


def sentences_from_day(day: str) -> list[str]:
    path = DAYS / f"{day}.html"
    if not path.exists():
        raise SystemExit(f"missing day page: {path}")
    return sentences_from_html(path.read_text(encoding="utf-8"))


def sentences_from_all_days() -> list[str]:
    found: list[str] = []
    for path in sorted(DAYS.glob("*.html")):
        found.extend(sentences_from_html(path.read_text(encoding="utf-8")))
    return found


def day_paths(day: str | None) -> list[Path]:
    if day:
        path = DAYS / f"{day}.html"
        if not path.exists():
            raise SystemExit(f"missing day page: {path}")
        return [path]
    return sorted(DAYS.glob("*.html"))


def patch_en_data_audio(html: str) -> tuple[str, int]:
    """Ensure each <en> has data-audio=\"ex/<hash>.mp3\". Returns (html, changes)."""

    def repl(m: re.Match[str]) -> str:
        attrs = m.group(1) or ""
        inner = m.group(2)
        h = sentence_hash(inner)
        rel = f"ex/{h}.mp3"
        # Strip any existing data-audio=... then set ours
        attrs2 = re.sub(r'\s*data-audio="[^"]*"', "", attrs)
        attrs2 = attrs2.strip()
        if attrs2:
            return f'<en {attrs2} data-audio="{rel}">{inner}</en>'
        return f'<en data-audio="{rel}">{inner}</en>'

    new_html, n = EN_RE.subn(repl, html)
    # Count actual attribute writes (subn counts all matches)
    return new_html, n


def patch_day_pages(day: str | None) -> int:
    total = 0
    for path in day_paths(day):
        raw = path.read_text(encoding="utf-8")
        new, n = patch_en_data_audio(raw)
        if new != raw:
            path.write_text(new, encoding="utf-8")
        total += n
        print(f"patch {path.name}: {n} <en> tags")
    return total


async def synth_mp3(
    sem: asyncio.Semaphore,
    edge: str,
    ffmpeg: str,
    text: str,
    out: Path,
    force: bool,
) -> tuple[str, str]:
    label = out.name
    if out.exists() and out.stat().st_size > 0 and not force:
        return label, "skip"
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
                return label, f"tts-fail:{(err or b'').decode('utf-8', 'ignore')[:200]}"
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
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(src.read_bytes())
            return label, "ok"


async def synth_one_word(
    sem: asyncio.Semaphore,
    edge: str,
    ffmpeg: str,
    word: str,
    force: bool,
) -> tuple[str, str]:
    slug = slug_of(word)
    out = AUDIO / f"{slug}.mp3"
    return await synth_mp3(sem, edge, ffmpeg, speak_text_word(word), out, force)


async def synth_one_sentence(
    sem: asyncio.Semaphore,
    edge: str,
    ffmpeg: str,
    sentence: str,
    force: bool,
) -> tuple[str, str]:
    h = sentence_hash(sentence)
    out = AUDIO_EX / f"{h}.mp3"
    return await synth_mp3(sem, edge, ffmpeg, speak_text_sentence(sentence), out, force)


def summarize(results: list[tuple[str, str]], kind: str) -> int:
    ok = skip = fail = 0
    failures: list[tuple[str, str]] = []
    for slug, status in results:
        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        else:
            fail += 1
            failures.append((slug, status))
    print(f"{kind}: done ok={ok} skip={skip} fail={fail}")
    for slug, status in failures[:30]:
        print(f"  FAIL {slug}: {status}")
    return fail


async def run_words(words: list[str], force: bool) -> int:
    seen: set[str] = set()
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
    print(f"words voice={VOICE} bitrate={BITRATE} concurrency={CONCURRENCY} n={len(unique)}")
    results = await asyncio.gather(
        *[synth_one_word(sem, edge, ffmpeg, w, force) for w in unique]
    )
    return summarize(list(results), "words")


async def run_sentences(sentences: list[str], force: bool) -> int:
    seen: set[str] = set()
    unique: list[str] = []
    for s in sentences:
        key = normalize_sentence(s)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(speak_text_sentence(s))

    edge = find_edge_tts()
    ffmpeg = find_ffmpeg()
    AUDIO_EX.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    print(f"examples voice={VOICE} bitrate={BITRATE} concurrency={CONCURRENCY} n={len(unique)}")
    results = await asyncio.gather(
        *[synth_one_sentence(sem, edge, ffmpeg, s, force) for s in unique]
    )
    return summarize(list(results), "examples")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--day", help="YYYY-MM-DD day page under days/")
    ap.add_argument("--words", help="Comma-separated headwords (implies words-only)")
    ap.add_argument("--force", action="store_true", help="Regenerate even if mp3 exists")
    ap.add_argument(
        "--examples-only",
        "--sentences-only",
        action="store_true",
        dest="examples_only",
        help="Only generate example-sentence MP3s and patch <en> tags",
    )
    ap.add_argument(
        "--words-only",
        action="store_true",
        help="Only generate headword MP3s (no examples, no HTML patch)",
    )
    ap.add_argument(
        "--sentences",
        "--examples",
        action="store_true",
        dest="sentences",
        help="Include example sentences (default when not --words-only)",
    )
    ap.add_argument(
        "--patch-only",
        action="store_true",
        help="Only patch data-audio on <en> tags; do not call TTS",
    )
    args = ap.parse_args()

    do_words = True
    do_examples = True
    if args.words:
        do_words = True
        do_examples = False
    elif args.examples_only:
        do_words = False
        do_examples = True
    elif args.words_only:
        do_words = True
        do_examples = False
    elif args.patch_only:
        do_words = False
        do_examples = False

    # --sentences is affirmative but default already includes examples
    if args.sentences:
        do_examples = True

    fails = 0

    if args.patch_only:
        patch_day_pages(args.day)
        return 0

    if do_words:
        if args.words:
            words = [w.strip() for w in args.words.split(",") if w.strip()]
        elif args.day:
            words = words_from_day(args.day)
        else:
            words = words_from_all_days()
        if not words:
            print("no words found", file=sys.stderr)
            if not do_examples:
                return 1
        else:
            fails += asyncio.run(run_words(words, args.force))

    if do_examples:
        if args.day:
            sentences = sentences_from_day(args.day)
        else:
            sentences = sentences_from_all_days()
        if not sentences:
            print("no example sentences found", file=sys.stderr)
            fails += 1
        else:
            fails += asyncio.run(run_sentences(sentences, args.force))
        patch_day_pages(args.day)

    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
