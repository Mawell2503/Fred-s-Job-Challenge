"""Stage 3: Normalize - clean up the mess extraction leaves behind"""
from __future__ import annotations

import re
import unicodedata

from .models import ExtractedDoc, Line

_ZERO_WIDTH = {ord(c): None for c in "\u200b\u200c\u200d\u2060\ufeff\u00ad"}   # zero-width chars + soft hyphen
_PUA = re.compile("[\ue000-\uf8ff]")                       # private-use icons (font glyphs)
_SYMBOL_BULLET = re.compile(
    "^\\s*[•‣◦⁃∙▪▫●○■□▶►▸"
    "➢➤✔✓·◆◇→➜➔❖-]\\s*")
_DASH_BULLET = re.compile(r"^\s*[-–—−]\s+(?=\S)")
_STAR_BULLET = re.compile(r"^\s*[*+]\s+(?=\S)")
_HYPHEN_END = re.compile(r"[A-Za-z]{2,}-$")


def _clean_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    t = t.replace("\t", "  ")
    t = "".join(" " if unicodedata.category(c) == "Cc" else c for c in t)
    if _SYMBOL_BULLET.match(t):
        t = _SYMBOL_BULLET.sub("- ", t, count=1)
    elif _DASH_BULLET.match(t):
        t = _DASH_BULLET.sub("- ", t, count=1)
    elif _STAR_BULLET.match(t):
        t = _STAR_BULLET.sub("- ", t, count=1)
    t = _PUA.sub(" ", t)
    t = re.sub(r" {3,}", "  ", t).strip()
    return t


def _collapse_letter_spacing(t: str) -> str:
    """'W O R K   E X P E R I E N C E' -> 'WORK EXPERIENCE' (letter-spaced headings)."""
    tokens = t.split()
    if len(tokens) >= 5 and all(len(x) == 1 and x.isalpha() for x in tokens):
        groups = re.split(r" {2,}", t)
        return " ".join("".join(g.split()) for g in groups)
    return t


def mark_repeated_edges(lines: list[Line]) -> None:
    """Flag header/footer lines that already appeared on an earlier page.
    The first occurrence is kept; later ones get repeated=True."""
    first_page: dict = {}
    for ln in lines:
        if ln.edge not in ("top", "bottom") or len(ln.text.strip()) < 2:
            continue
        key = (ln.edge, re.sub(r"\d+", "#", ln.text.lower().strip()))
        if key not in first_page:
            first_page[key] = ln.page
        elif ln.page > first_page[key]:
            ln.repeated = True


def normalize(doc: ExtractedDoc) -> list[Line]:
    lines: list[Line] = []
    for ln in doc.lines:
        t = _collapse_letter_spacing(_clean_text(ln.text))
        lines.append(ln.with_text(t))

    if doc.source_type in ("pdf", "txt"):
        for i in range(len(lines) - 1):
            cur, nxt = lines[i], lines[i + 1]
            if (_HYPHEN_END.search(cur.text) and nxt.text and nxt.page == cur.page
                    and nxt.text[0].islower()):
                head, _, rest = nxt.text.partition(" ")
                lines[i] = cur.with_text(cur.text[:-1] + head)
                lines[i + 1] = nxt.with_text(rest.strip())

    out: list[Line] = []
    for ln in lines:
        if not ln.text and (not out or not out[-1].text):
            continue
        out.append(ln)
    while out and not out[-1].text:
        out.pop()

    mark_repeated_edges(out)
    return out
