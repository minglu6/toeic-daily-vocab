#!/usr/bin/env python3
"""Shared helpers for TOEIC commute-listening tracks."""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAYS = ROOT / "days"
COMMUTE = ROOT / "commute"
CACHE = COMMUTE / "cache"
TRACKS = COMMUTE / "tracks"
SCRIPTS_DIR = COMMUTE / "scripts"
LEXICON_PATH = COMMUTE / "lexicon.json"
MANIFEST_PATH = COMMUTE / "tracks.json"
FEED_PATH = COMMUTE / "feed.xml"

EN_VOICE = "en-US-JennyNeural"
ZH_VOICE = "zh-CN-XiaoxiaoNeural"
EN_RATE = "-12%"
ZH_RATE = "-8%"
BITRATE = "32k"
SAMPLE_RATE = "24000"
WORDS_PER_TRACK = 30
SITE_BASE = "https://minglu6.github.io/toeic-daily-vocab"

# Pause lengths in milliseconds
PAUSE_AFTER_INTRO = 800
PAUSE_AFTER_WORD = 650
PAUSE_AFTER_SPELL = 550
PAUSE_AFTER_MEANING = 700
PAUSE_AFTER_EXAMPLE = 550
PAUSE_AFTER_WORD_BLOCK = 1500
PAUSE_AFTER_OUTRO = 400

POS_MAP = {
    "n": "名词",
    "n.": "名词",
    "n. pl.": "名词",
    "n.pl.": "名词",
    "v": "动词",
    "v.": "动词",
    "vt.": "动词",
    "vi.": "动词",
    "adj": "形容词",
    "adj.": "形容词",
    "adv": "副词",
    "adv.": "副词",
    "prep": "介词",
    "prep.": "介词",
    "conj": "连词",
    "conj.": "连词",
    "phr": "短语",
    "phr.": "短语",
    "phrase": "短语",
    "abbr": "缩写",
    "abbr.": "缩写",
    "pref": "前缀",
    "pref.": "前缀",
    "num": "数词",
    "num.": "数词",
}


def slug_of(word: str) -> str:
    return re.sub(r"\s+", "-", word.strip().lower())


def norm_key(word: str) -> str:
    """Dedup key: casefold, treat hyphen/space/underscore as the same."""
    s = word.strip().casefold()
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def pos_to_zh(pos: str) -> str:
    raw = (pos or "").strip()
    if not raw:
        return ""
    parts = re.split(r"\s*/\s*", raw)
    mapped: list[str] = []
    seen: set[str] = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        key = p if p.endswith(".") or p in POS_MAP else (p + "." if not p.endswith(".") else p)
        zh = POS_MAP.get(p) or POS_MAP.get(key) or POS_MAP.get(p.rstrip("."))
        if not zh:
            # last chance: leading token
            m = re.match(r"(n\.?\s*pl\.?|n|v|adj|adv|prep|conj|phr|abbr|pref|num)\.?", p, re.I)
            if m:
                tok = m.group(1).lower()
                zh = POS_MAP.get(tok) or POS_MAP.get(tok + ".")
        if zh and zh not in seen:
            seen.add(zh)
            mapped.append(zh)
    if not mapped:
        return ""
    if len(mapped) == 1:
        return mapped[0]
    return "或".join(mapped)


def spoken_meaning(pos: str, meaning: str) -> str:
    """Compact oral Chinese gloss, suitable for commuting."""
    text = (meaning or "").strip()
    text = re.sub(r"\s+", "", text)
    senses = [s.strip("。；;，, ") for s in re.split(r"[；;]", text) if s.strip()]
    picked: list[str] = []
    for s in senses:
        # Skip parenthetical English-heavy tails that are too long
        if len(s) > 28 and picked:
            break
        nxt = picked + [s]
        joined = "，".join(nxt)
        if picked and len(joined) > 28 and len(picked) >= 2:
            break
        if picked and len(joined) > 36:
            break
        picked.append(s)
        if len(picked) >= 3:
            break
        if len("，".join(picked)) >= 18 and len(picked) >= 2:
            break
    body = "，".join(picked) if picked else text
    pos_cn = pos_to_zh(pos)
    if pos_cn:
        return f"{pos_cn}，{body}"
    return body


_SPECIAL = {
    "-": "hyphen",
    "–": "hyphen",
    "—": "hyphen",
    "'": "apostrophe",
    "’": "apostrophe",
    "/": "slash",
    "&": "and",
    ".": "dot",
    " ": "space",
}


def spelling_text(word: str) -> str:
    """Spell a headword letter by letter in English, naming hyphens clearly."""
    parts: list[str] = []
    for ch in word.strip():
        if ch in _SPECIAL:
            # skip spaces between already-hyphenated tokens? keep "space" for multi-word
            parts.append(_SPECIAL[ch])
        elif ch.isalpha():
            parts.append(ch.upper())
        elif ch.isdigit():
            parts.append(ch)
        else:
            # ignore leftover punctuation
            continue
    return ", ".join(parts)


def speak_word(word: str) -> str:
    """Natural pronunciation: hyphens become short pauses (spaces)."""
    return re.sub(r"[-–—]+", " ", word.strip())


def track_intro(track_no: int, n_words: int) -> str:
    return f"托业通勤听力，第{track_no}轨，共{n_words}个单词。"


def track_outro(track_no: int) -> str:
    return f"第{track_no}轨结束。"


CARD_RE = re.compile(r"<article class=\"card\">(.*?)</article>", re.S)
WORD_RE = re.compile(r'<span class="word">([^<]+)</span>')
IPA_RE = re.compile(r'<span class="ipa">([^<]*)</span>')
POS_RE = re.compile(r'<span class="pos">([^<]*)</span>')
MEANING_RE = re.compile(r'<div class="meaning">([^<]*)</div>')
EN_RE = re.compile(r"<en(?:\s[^>]*)?>(.*?)</en>", re.S)
ZH_RE = re.compile(r"<zh(?:\s[^>]*)?>(.*?)</zh>", re.S)


def parse_day_cards(page: str) -> list[dict]:
    items: list[dict] = []
    for card in CARD_RE.findall(page):
        w = WORD_RE.search(card)
        if not w:
            continue
        ipa = IPA_RE.search(card)
        pos = POS_RE.search(card)
        meaning = MEANING_RE.search(card)
        en = EN_RE.search(card)
        zh = ZH_RE.search(card)
        def clean(s: str) -> str:
            s = html.unescape(s or "")
            s = re.sub(r"\s+", " ", s).strip()
            return s

        items.append(
            {
                "word": clean(w.group(1)),
                "ipa": clean(ipa.group(1) if ipa else ""),
                "pos": clean(pos.group(1) if pos else ""),
                "meaning": clean(meaning.group(1) if meaning else ""),
                "en": clean(en.group(1) if en else ""),
                "zh": clean(zh.group(1) if zh else ""),
            }
        )
    return items
