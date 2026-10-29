"""Oscar's structured candidate record (one JSON per CV).

Built from the blocks Oscar KEPT (so the admin's keep/drop choices apply). Holds what
Remi needs to check criteria without an LLM:

    headline, sections (text per section), experience entries with parsed dates,
    total_years_experience, education, skills listed, standardised terms, languages,
    availability, and the canonicalized text used for keyword matching.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from .models import Block
from .segment import DATE_RANGE, PRESENT_WORDS
from .vocab import get_vocab

# English and French month names, checked by their start (accents removed): "févr." -> fev -> 2
_MONTH_STARTS = [("juil", 7), ("juin", 6), ("jan", 1), ("feb", 2), ("fev", 2), ("mar", 3), ("apr", 4), ("avr", 4),
                 ("may", 5), ("mai", 5), ("jun", 6), ("jul", 7), ("aug", 8), ("aou", 8), ("sep", 9), ("oct", 10),
                 ("nov", 11), ("dec", 12)]
_PRESENT = re.compile(PRESENT_WORDS, re.I)
_ONE_DATE = re.compile(
    r"(?P<mon>[^\W\d_]{3,9})\.?\s+(?P<y1>(?:19|20)\d{2})|(?P<mm>\d{1,2})[/.](?P<y2>(?:19|20)\d{2})|(?P<y3>(?:19|20)\d{2})",
    re.I)


def _month(word: str) -> int | None:
    w = "".join(c for c in unicodedata.normalize("NFKD", word.lower()) if not unicodedata.combining(c))
    return next((n for start, n in _MONTH_STARTS if w.startswith(start)), None)
_AVAIL = re.compile(r"availab|notice period|start date|can start|immediate(ly)?\b|disponib|preavis", re.I)
_SPLIT_ITEMS = re.compile(r"\s*(?:[,;|•·]|\s/\s|\s-\s|^-\s)\s*")


def _to_month_index(token: str, today: date) -> int | None:
    """'Mar 2019' / '03/2019' / '2019' / 'Present' -> months since year 0 (start of period)."""
    if _PRESENT.fullmatch(token.strip()):
        return today.year * 12 + today.month - 1
    m = _ONE_DATE.search(token)
    if not m:
        return None
    if m.group("y1"):
        mon = _month(m.group("mon"))
        return int(m.group("y1")) * 12 + ((mon or 1) - 1)
    if m.group("y2"):
        mm = int(m.group("mm"))
        return int(m.group("y2")) * 12 + (min(max(mm, 1), 12) - 1)
    return int(m.group("y3")) * 12


def parse_range(text: str, today: date | None = None):
    """Return (start_idx, end_idx, matched_text) for the first date range in `text`, else None.
    A year-only end ('2016 - 2019') counts up to the END of that year."""
    today = today or date.today()
    m = DATE_RANGE.search(text)
    if not m:
        return None
    raw = m.group(0)
    parts = re.split(r"\s*(?:-|–|—|\bto\b|\buntil\b|\bjusqu['’]?\s*[aà]\b|\bau\b|\s[aà]\s)\s*", raw, maxsplit=1, flags=re.I)
    if len(parts) != 2:
        return None
    a = _to_month_index(parts[0], today)
    b = _to_month_index(parts[1], today)
    if a is None or b is None:
        return None
    if re.fullmatch(r"\s*(?:19|20)\d{2}\s*", parts[1]):
        b += 11                                    # "… - 2019" means through Dec 2019
    if b < a:
        return None
    return a, b, raw


def _merged_months(ranges: list[tuple[int, int]]) -> int:
    """Total months covered, counting overlapping jobs once."""
    total, cur = 0, None
    for a, b in sorted(ranges):
        if cur is None or a > cur[1] + 1:
            if cur:
                total += cur[1] - cur[0] + 1
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        total += cur[1] - cur[0] + 1
    return total


def _fmt(idx: int) -> str:
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _entry_text(entry) -> str:
    return "\n".join(ln.text for ln in entry).strip()


def _items(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        body = re.sub(r"^\s*-\s+", "", line)
        if ":" in body and len(body.split(":", 1)[0]) <= 30:     # "Languages: French, English"
            body = body.split(":", 1)[1]
        for part in _SPLIT_ITEMS.split(body):
            p = part.strip(" .")
            if 1 < len(p) <= 60 and p not in out:
                out.append(p)
    return out


def build_record(blocks: list[Block], candidate_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    vocab = get_vocab()
    by_type: dict[str, list[Block]] = {}
    for b in blocks:
        by_type.setdefault(b.type, []).append(b)

    def entries(*types):
        return [e for t in types for b in by_type.get(t, []) for e in b.entries if e]

    def lines(*types):
        return [ln.text for t in types for b in by_type.get(t, []) for ln in b.lines if ln.text.strip()]

    # experience entries with dates
    experience, ranges = [], []
    for e in entries("experience"):
        text = _entry_text(e)
        head = next((ln.text for ln in e if DATE_RANGE.search(ln.text)), "")
        r = parse_range(head, today) if head else None
        title = re.sub(r"\s{2,}", " | ", DATE_RANGE.sub("", e[0].text)).strip(" |,-–") if e else ""
        item = {"title": title, "text": text, "start": None, "end": None, "months": None}
        if r:
            a, b, _ = r
            item.update(start=_fmt(a), end=_fmt(b), months=b - a + 1)
            ranges.append((a, b))
        experience.append(item)
    total_years = round(_merged_months(ranges) / 12, 1) if ranges else None

    # skills: what they listed + every known skill mentioned anywhere in the kept text
    full_text = "\n".join(ln.text for b in blocks for ln in b.lines)
    canon = vocab.canonicalize(full_text)
    skill_lines = lines("skills")
    skills_listed = _items(skill_lines)
    lang_lines = lines("languages")
    lang_terms = vocab.terms_in("\n".join(lang_lines))

    header_lines = lines("header", "unsorted")
    record = {
        "candidate_id": candidate_id,
        "headline": header_lines[0] if header_lines else "",
        "sections_present": [b.type for b in blocks],
        "sections": {t: "\n\n".join(_entry_text(e) for b in bl for e in b.entries if e)
                     for t, bl in by_type.items()},
        "experience": experience,
        "total_years_experience": total_years,
        "education": [_entry_text(e) for e in entries("education")],
        "certifications": [_entry_text(e) for e in entries("certifications", "training")],
        "skills_listed": skills_listed,
        # every standardised term found anywhere (skills, tools, languages, degrees ...)
        "standard_terms": sorted(vocab.name(t) for t in vocab.terms_in(canon, already_canonical=True)),
        "languages_listed": _items(lang_lines),
        "languages": sorted(vocab.name(t) for t in lang_terms),
        "availability": [t for t in (ln.text for b in blocks for ln in b.lines) if _AVAIL.search(t)],
        "canonical_text": canon,
        "computed_on": today.isoformat(),
    }
    return record


__all__ = ["build_record", "parse_range"]
