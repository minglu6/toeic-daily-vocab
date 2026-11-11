#!/usr/bin/env python3
"""Synthesize commute-listening MP3 tracks from commute/lexicon.json.

Each word is spoken in five parts:
  1. English headword     (en-US-JennyNeural)
  2. Letter-by-letter spelling, with "hyphen" for "-"
  3. Compact Chinese gloss (zh-CN-XiaoxiaoNeural)
  4. Original TOEIC/business example in English
  5. Chinese translation of the example

Tracks are ~30 words. Segment MP3s are cached in commute/cache/ (gitignored).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commute_lib import (  # noqa: E402
    BITRATE,
    CACHE,
    COMMUTE,
    EN_RATE,
    EN_VOICE,
    FEED_PATH,
    LEXICON_PATH,
    MANIFEST_PATH,
    PAUSE_AFTER_EXAMPLE,
    PAUSE_AFTER_INTRO,
    PAUSE_AFTER_MEANING,
    PAUSE_AFTER_OUTRO,
    PAUSE_AFTER_SPELL,
    PAUSE_AFTER_WORD,
    PAUSE_AFTER_WORD_BLOCK,
    SAMPLE_RATE,
    SCRIPTS_DIR,
    SITE_BASE,
    TRACKS,
    WORDS_PER_TRACK,
    ZH_RATE,
    ZH_VOICE,
    track_intro,
    track_outro,
)


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


def text_key(voice: str, rate: str, text: str) -> str:
    h = hashlib.sha256(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()[:16]
    return h


def cache_path(voice: str, rate: str, text: str) -> Path:
    return CACHE / f"{text_key(voice, rate, text)}.mp3"


def silence_path(ms: int) -> Path:
    return CACHE / f"silence_{ms}.mp3"


def ensure_silence(ffmpeg: str, ms: int) -> Path:
    out = silence_path(ms)
    if out.exists() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    sec = f"{ms / 1000:.3f}"
    with tempfile.TemporaryDirectory(prefix="silence-") as tmp:
        dest = Path(tmp) / "s.mp3"
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={SAMPLE_RATE}:cl=mono",
                "-t",
                sec,
                "-ac",
                "1",
                "-ar",
                SAMPLE_RATE,
                "-b:a",
                BITRATE,
                "-codec:a",
                "libmp3lame",
                str(dest),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
            raise SystemExit(f"failed to make {ms}ms silence: {proc.stderr[-200:]}")
        out.write_bytes(dest.read_bytes())
    return out


async def synth_segment(
    sem: asyncio.Semaphore,
    edge: str,
    ffmpeg: str,
    voice: str,
    rate: str,
    text: str,
    force: bool,
    retries: int = 4,
) -> tuple[Path, str]:
    out = cache_path(voice, rate, text)
    if out.exists() and out.stat().st_size > 0 and not force:
        return out, "skip"
    out.parent.mkdir(parents=True, exist_ok=True)
    last_err = ""
    for attempt in range(retries):
        async with sem:
            with tempfile.TemporaryDirectory(prefix="commute-tts-") as tmp:
                raw = Path(tmp) / "raw.mp3"
                tiny = Path(tmp) / "tiny.mp3"
                proc = await asyncio.create_subprocess_exec(
                    edge,
                    "--voice",
                    voice,
                    f"--rate={rate}",
                    "--text",
                    text,
                    "--write-media",
                    str(raw),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _o, err = await proc.communicate()
                if proc.returncode != 0 or not raw.exists() or raw.stat().st_size == 0:
                    last_err = (err or b"").decode("utf-8", "ignore")[:240]
                else:
                    ff = await asyncio.create_subprocess_exec(
                        ffmpeg,
                        "-y",
                        "-i",
                        str(raw),
                        "-ac",
                        "1",
                        "-ar",
                        SAMPLE_RATE,
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
                    if src.exists() and src.stat().st_size > 0:
                        out.write_bytes(src.read_bytes())
                        return out, "ok"
                    last_err = (err2 or b"").decode("utf-8", "ignore")[:240]
        await asyncio.sleep(1.5 * (attempt + 1))
    return out, f"fail:{last_err}"


def chunk_entries(entries: list[dict], size: int) -> list[list[dict]]:
    return [entries[i : i + size] for i in range(0, len(entries), size)]


def cue_sheet(track_no: int, words: list[dict]) -> dict:
    intro = track_intro(track_no, len(words))
    outro = track_outro(track_no)
    cues: list[dict] = [
        {"kind": "intro", "voice": "zh", "text": intro, "pause_after_ms": PAUSE_AFTER_INTRO}
    ]
    for w in words:
        cues.extend(
            [
                {
                    "kind": "word",
                    "word": w["word"],
                    "voice": "en",
                    "text": w["speak_word"],
                    "pause_after_ms": PAUSE_AFTER_WORD,
                },
                {
                    "kind": "spelling",
                    "word": w["word"],
                    "voice": "en",
                    "text": w["spelling"],
                    "pause_after_ms": PAUSE_AFTER_SPELL,
                },
                {
                    "kind": "meaning",
                    "word": w["word"],
                    "voice": "zh",
                    "text": w["spoken_meaning"],
                    "pause_after_ms": PAUSE_AFTER_MEANING,
                },
                {
                    "kind": "example",
                    "word": w["word"],
                    "voice": "en",
                    "text": w["en"],
                    "pause_after_ms": PAUSE_AFTER_EXAMPLE,
                },
                {
                    "kind": "translation",
                    "word": w["word"],
                    "voice": "zh",
                    "text": w["zh"],
                    "pause_after_ms": PAUSE_AFTER_WORD_BLOCK,
                },
            ]
        )
    cues.append(
        {"kind": "outro", "voice": "zh", "text": outro, "pause_after_ms": PAUSE_AFTER_OUTRO}
    )
    return {
        "id": f"{track_no:03d}",
        "track": track_no,
        "title": f"第 {track_no} 轨",
        "word_count": len(words),
        "headwords": [w["word"] for w in words],
        "entries": [
            {
                "word": w["word"],
                "spelling": w["spelling"],
                "spoken_meaning": w["spoken_meaning"],
                "en": w["en"],
                "zh": w["zh"],
            }
            for w in words
        ],
        "cues": cues,
    }


def voice_of(tag: str) -> tuple[str, str]:
    if tag == "en":
        return EN_VOICE, EN_RATE
    return ZH_VOICE, ZH_RATE


def concat_mp3(ffmpeg: str, parts: list[Path], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="concat-") as tmp:
        lst = Path(tmp) / "list.txt"
        # concat demuxer needs absolute or escaped paths
        lines = []
        for p in parts:
            ap = p.resolve()
            lines.append("file " + "'" + str(ap).replace("'", r"'\''") + "'")
        lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raw = Path(tmp) / "out.mp3"
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-ac",
                "1",
                "-ar",
                SAMPLE_RATE,
                "-b:a",
                BITRATE,
                "-codec:a",
                "libmp3lame",
                str(raw),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode != 0 or not raw.exists() or raw.stat().st_size == 0:
            err = (proc.stderr or b"").decode("utf-8", "ignore")[-400:]
            raise SystemExit(f"ffmpeg concat failed for {dest.name}: {err}")
        dest.write_bytes(raw.read_bytes())


def mp3_duration_sec(path: Path) -> float:
    """Duration via ffmpeg probe (mutagen may be absent)."""
    proc = subprocess.run(
        [
            find_ffmpeg(),
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    err = (proc.stderr or b"").decode("utf-8", "ignore")
    # Duration: 00:10:12.34
    import re

    m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", err)
    if not m:
        return 0.0
    h, mi, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    frac = cs / (10 ** len(m.group(4)))
    return h * 3600 + mi * 60 + s + frac


def fmt_clock(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def write_feed(manifest: dict) -> None:
    items = []
    pub = manifest.get("built_at") or ""
    for t in manifest["tracks"]:
        url = f"{SITE_BASE}/commute/tracks/{t['id']}.mp3"
        title = f"托业通勤听力 {t['title']}（{t['word_count']} 词）"
        desc = (
            "原创托业通勤听力，非新东方原书录音。"
            f"本轨单词：{', '.join(t['headwords'])}。"
        )
        items.append(
            f"""    <item>
      <title>{xml_esc(title)}</title>
      <description>{xml_esc(desc)}</description>
      <guid isPermaLink="true">{url}</guid>
      <pubDate>{rfc2822()}</pubDate>
      <enclosure url="{url}" length="{t.get('bytes', 0)}" type="audio/mpeg"/>
      <itunes:title>{xml_esc(title)}</itunes:title>
      <itunes:duration>{int(round(t.get('duration_sec') or 0))}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>托业通勤听力（原创）</title>
    <link>{SITE_BASE}/commute/</link>
    <language>zh-cn</language>
    <description>原创托业/商务英语单词通勤听力。每词顺序：英文读音、字母拼写、中文释义、职场例句、例句汉译。本节目不是新东方或其他商业词书的原文或录音。</description>
    <atom:link href="{SITE_BASE}/commute/feed.xml" rel="self" type="application/rss+xml"/>
    <itunes:author>托业每日单词</itunes:author>
    <itunes:summary>原创托业通勤听力，非新东方原书录音。英文 Jenny、中文晓晓，适合路上不看屏听写。</itunes:summary>
    <itunes:category text="Education"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:language>zh</itunes:language>
{os.linesep.join(items)}
  </channel>
</rss>
"""
    FEED_PATH.write_text(xml, encoding="utf-8")


def xml_esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def rfc2822() -> str:
    return time.strftime("%a, %d %b %Y %H:%M:%S +0800", time.localtime())


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


async def synthesize_all(
    jobs: list[tuple[str, str, str]],
    force: bool,
    concurrency: int,
) -> int:
    edge = find_edge_tts()
    ffmpeg = find_ffmpeg()
    sem = asyncio.Semaphore(concurrency)
    print(f"tts jobs={len(jobs)} concurrency={concurrency} force={force}")
    results = await asyncio.gather(
        *[synth_segment(sem, edge, ffmpeg, v, r, t, force) for v, r, t in jobs]
    )
    ok = skip = fail = 0
    failures = []
    for (path, status), job in zip(results, jobs):
        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        else:
            fail += 1
            failures.append((job[2][:60], status))
    print(f"tts done ok={ok} skip={skip} fail={fail}")
    for text, status in failures[:25]:
        print(f"  FAIL {text!r}: {status}")
    return fail


def unique_jobs(cues_all: list[dict]) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    jobs: list[tuple[str, str, str]] = []
    for cue in cues_all:
        voice, rate = voice_of(cue["voice"])
        key = text_key(voice, rate, cue["text"])
        if key in seen:
            continue
        seen.add(key)
        jobs.append((voice, rate, cue["text"]))
    return jobs


def parse_track_spec(spec: str, n_tracks: int) -> set[int]:
    chosen: set[int] = set()
    if not spec:
        return set(range(1, n_tracks + 1))
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            for i in range(lo, hi + 1):
                if 1 <= i <= n_tracks:
                    chosen.add(i)
        else:
            i = int(part)
            if 1 <= i <= n_tracks:
                chosen.add(i)
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lexicon", default=str(LEXICON_PATH))
    ap.add_argument("--force", action="store_true", help="regenerate TTS segments")
    ap.add_argument("--force-concat", action="store_true", help="rebuild track mp3s even if present")
    ap.add_argument("--tracks", help="track numbers, e.g. 1 or 1-3,7")
    ap.add_argument("--limit-words", type=int, help="use only the first N lexicon entries (testing)")
    ap.add_argument("--concurrency", type=int, default=int(os.environ.get("TOEIC_TTS_CONCURRENCY", "6")))
    ap.add_argument("--skip-tts", action="store_true", help="only concat / write manifest")
    args = ap.parse_args()

    lex = json.loads(Path(args.lexicon).read_text(encoding="utf-8"))
    entries = lex["entries"]
    if args.limit_words:
        entries = entries[: args.limit_words]
    groups = chunk_entries(entries, WORDS_PER_TRACK)
    chosen = parse_track_spec(args.tracks or "", len(groups))
    if not chosen:
        print("no tracks selected", file=sys.stderr)
        return 1

    ffmpeg = find_ffmpeg()
    CACHE.mkdir(parents=True, exist_ok=True)
    TRACKS.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    sheets = []
    for i, words in enumerate(groups, start=1):
        sheet = cue_sheet(i, words)
        sheets.append(sheet)
        (SCRIPTS_DIR / f"{sheet['id']}.json").write_text(
            json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if not args.skip_tts:
        cues_needed: list[dict] = []
        for sheet in sheets:
            if sheet["track"] in chosen:
                cues_needed.extend(sheet["cues"])
        fails = asyncio.run(synthesize_all(unique_jobs(cues_needed), args.force, args.concurrency))
        if fails:
            print(f"{fails} TTS segments failed", file=sys.stderr)
            return 1

    # ensure silences
    pauses = {
        PAUSE_AFTER_INTRO,
        PAUSE_AFTER_WORD,
        PAUSE_AFTER_SPELL,
        PAUSE_AFTER_MEANING,
        PAUSE_AFTER_EXAMPLE,
        PAUSE_AFTER_WORD_BLOCK,
        PAUSE_AFTER_OUTRO,
    }
    for ms in pauses:
        ensure_silence(ffmpeg, ms)

    track_infos = []
    for sheet in sheets:
        tid = sheet["id"]
        dest = TRACKS / f"{tid}.mp3"
        if sheet["track"] in chosen and (args.force_concat or not dest.exists() or dest.stat().st_size == 0):
            parts: list[Path] = []
            for cue in sheet["cues"]:
                voice, rate = voice_of(cue["voice"])
                seg = cache_path(voice, rate, cue["text"])
                if not seg.exists() or seg.stat().st_size == 0:
                    raise SystemExit(f"missing segment for track {tid}: {cue['kind']} {cue['text'][:50]!r}")
                parts.append(seg)
                parts.append(silence_path(int(cue["pause_after_ms"])))
            print(f"concat {dest.name} parts={len(parts)} words={sheet['word_count']}")
            concat_mp3(ffmpeg, parts, dest)
        duration = mp3_duration_sec(dest) if dest.exists() else 0.0
        info = {
            "id": tid,
            "title": sheet["title"],
            "file": f"tracks/{tid}.mp3",
            "word_count": sheet["word_count"],
            "duration_sec": round(duration, 2),
            "duration": fmt_clock(duration),
            "bytes": dest.stat().st_size if dest.exists() else 0,
            "headwords": sheet["headwords"],
        }
        track_infos.append(info)
        print(f"  {tid} {info['duration']} {info['bytes']} bytes {info['word_count']} words")

    manifest = {
        "title": "托业通勤听力（原创）",
        "disclaimer": "本听力为原创托业/商务英语通勤材料，不是新东方或其他商业词书的原文或录音。",
        "page": f"{SITE_BASE}/commute/",
        "feed": f"{SITE_BASE}/commute/feed.xml",
        "voice": {"en": EN_VOICE, "zh": ZH_VOICE, "en_rate": EN_RATE, "zh_rate": ZH_RATE},
        "bitrate": BITRATE,
        "words_per_track": WORDS_PER_TRACK,
        "word_count": len(entries),
        "track_count": len(track_infos),
        "built_at": iso_now(),
        "tracks": track_infos,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_feed(manifest)
    print(f"wrote {MANIFEST_PATH} and {FEED_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
