"""Stage 4: Segment - split normalized lines into labeled blocks and entries"""
from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter

from .models import Block, Line

# Dictionary
HEADING_SYNONYMS: dict[str, list[str]] = {
    "summary": ["summary", "professional summary", "career summary", "executive summary",
                "profile", "personal profile", "professional profile", "about me", "about",
                "objective", "career objective", "personal statement", "overview",
                "summary of qualifications", "profil", "resume professionnel"],
    "experience": ["experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "career history", "relevant experience",
                   "internships", "internship experience", "professional background",
                   "experience professionnelle", "experiences professionnelles", "parcours professionnel"],
    "education": ["education", "academic background", "academic qualifications", "qualifications",
                  "educational background", "education and training", "academic history", "studies",
                  "academic record", "formation", "education & qualifications"],
    "skills": ["skills", "technical skills", "key skills", "core competencies", "competencies",
               "skills and abilities", "areas of expertise", "expertise", "technologies", "tools",
               "soft skills", "hard skills", "it skills", "professional skills", "computer skills",
               "technical proficiencies", "competences", "skills summary"],
    "languages": ["languages", "language skills", "language proficiency", "langues"],
    "certifications": ["certifications", "certificates", "certification", "licenses", "licences",
                       "licenses and certifications", "accreditations", "professional certifications"],
    "training": ["training", "courses", "professional development", "training and courses",
                 "workshops", "trainings", "courses and training"],
    "projects": ["projects", "personal projects", "selected projects", "key projects",
                 "academic projects", "project experience", "projets"],
    "awards": ["awards", "honors", "honours", "achievements", "accomplishments",
               "awards and honors", "scholarships", "awards and achievements"],
    "publications": ["publications", "papers", "research", "conferences", "presentations"],
    "volunteering": ["volunteering", "volunteer experience", "volunteer work", "community involvement",
                     "extracurricular activities", "activities", "leadership", "involvement",
                     "extra curricular activities"],
    "interests": ["interests", "hobbies", "hobbies and interests", "personal interests", "leisure",
                  "leisure activities", "pastimes", "interests and hobbies", "centres d interet"],
    "references": ["references", "reference", "referees", "referee", "professional references",
                   "references available upon request"],
    "personal": ["personal details", "personal information", "personal data", "contact",
                 "contact details", "contact information", "contact me", "personal particulars",
                 "bio data", "biodata", "details", "personal"],
    "declaration": ["declaration", "declarations"],
}

# single words that signal a type when the rest of a short line is filler
KEYWORDS: dict[str, str] = {
    "experience": "experience", "employment": "experience", "internships": "experience",
    "education": "education", "qualifications": "education", "studies": "education",
    "skills": "skills", "competencies": "skills", "expertise": "skills", "tools": "skills",
    "technologies": "skills", "languages": "languages",
    "certifications": "certifications", "certificates": "certifications",
    "licenses": "certifications", "licences": "certifications",
    "training": "training", "courses": "training", "workshops": "training",
    "projects": "projects",
    "awards": "awards", "honors": "awards", "honours": "awards", "achievements": "awards",
    "accomplishments": "awards", "scholarships": "awards",
    "publications": "publications",
    "volunteering": "volunteering", "volunteer": "volunteering",
    "interests": "interests", "hobbies": "interests", "leisure": "interests",
    "references": "references", "referees": "references",
    "summary": "summary", "profile": "summary", "objective": "summary",
    "declaration": "declaration", "contact": "personal",
}
FILLER = {"and", "of", "the", "my", "professional", "work", "technical", "key", "core",
          "relevant", "academic", "personal", "additional", "other", "related", "selected",
          "industry", "career", "history", "background", "details", "information", "data",
          "extra", "curricular", "it", "computer", "soft", "hard", "summary", "me"}

ENTRY_TYPES = {"experience", "education", "projects", "certifications", "training",
               "volunteering", "awards", "publications", "unknown"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_head(text: str) -> str:
    t = _strip_accents(text).lower()
    t = t.replace("&", " and ").replace("+", " and ").replace("/", " and ")
    t = re.sub(r"[^a-z ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_EXACT: dict[str, str] = {}
_EXACT_NOSPACE: dict[str, str] = {}
for _typ, _syns in HEADING_SYNONYMS.items():
    for _s in _syns:
        _EXACT[norm_head(_s)] = _typ
        _EXACT_NOSPACE[norm_head(_s).replace(" ", "")] = _typ


def classify_heading(text: str, exact_only: bool = False) -> str | None:
    """Return the canonical type if `text` is a known section heading, else None."""
    raw = text.strip().rstrip(":").strip()
    if not raw or len(raw) > 60 or re.search(r"\d", raw) or raw.startswith("- "):
        return None
    n = norm_head(raw)
    if not n:
        return None
    if n in _EXACT:
        return _EXACT[n]
    if n.replace(" ", "") in _EXACT_NOSPACE and len(n.replace(" ", "")) >= 6:
        return _EXACT_NOSPACE[n.replace(" ", "")]
    if exact_only:
        return None
    words = n.split()
    if len(words) > 5:
        return None
    kws = [w for w in words if w in KEYWORDS]
    if kws and all(w in KEYWORDS or w in FILLER for w in words):
        return KEYWORDS[kws[0]]
    return None


# English and French month names (CVs come in both), with or without the abbreviation dot
_MONTH = (r"(?:jan(?:uary|vier|v)?|feb(?:ruary)?|f[eé]v(?:rier|r)?|mar(?:ch|s)?|apr(?:il)?|avr(?:il)?|"
          r"may|mai|jun(?:e)?|juin|jul(?:y)?|juil(?:let)?|aug(?:ust)?|ao[uû]t?|sep(?:t(?:ember|embre)?)?|"
          r"oct(?:ober|obre)?|nov(?:ember|embre)?|d[eé]c(?:ember|embre)?)\.?")
_YEAR = r"(?:19|20)\d{2}"
_DATE = rf"(?:{_MONTH}\s+{_YEAR}|\d{{1,2}}[/.]{_YEAR}|{_YEAR})"
# the end of a job that is still going on: "Present", "aujourd'hui", "à ce jour" ...
PRESENT_WORDS = (r"(?:present|current|now|ongoing|today|date|aujourd['’]hui|pr[eé]sent|actuel(?:lement)?|"
                 r"en\s+cours|[aà]\s+ce\s+jour)")
DATE_RANGE = re.compile(
    rf"\b{_DATE}\s*(?:-|–|—|to|until|[aà]|au|jusqu['’]?[aà])\s*(?:{_DATE}|{PRESENT_WORDS})(?![\w'])",
    re.I)
_ANY_DATE = re.compile(rf"\b{_DATE}\b", re.I)


def _is_bullet(text: str) -> bool:
    return bool(re.match(r"^\s*(?:-|\d+[.)])\s+", text))


def _is_dateish(text: str) -> bool:
    return bool(DATE_RANGE.search(text)) or (len(text.split()) <= 6 and bool(_ANY_DATE.search(text)))

# Entry Splitting


def _split_on_dates(chunk: list[Line]) -> list[list[Line]]:
    date_idx = [i for i, ln in enumerate(chunk)
                if not _is_bullet(ln.text) and DATE_RANGE.search(ln.text) and len(ln.text.split()) <= 14]
    if len(date_idx) <= 1:
        return [chunk]
    starts = [0]
    for k in range(1, len(date_idx)):
        d = date_idx[k]
        s = d
        j = d - 1
        steps = 0
        while (j > date_idx[k - 1] and steps < 2 and not _is_bullet(chunk[j].text)
               and len(chunk[j].text.split()) <= 10 and not chunk[j].text.rstrip().endswith(".")
               and not chunk[j].text[:1].islower()):
            s = j
            j -= 1
            steps += 1
        starts.append(max(s, starts[-1] + 1))
    starts.append(len(chunk))
    return [chunk[a:b] for a, b in zip(starts, starts[1:]) if chunk[a:b]]


def split_entries(lines: list[Line], btype: str) -> list[list[Line]]:
    chunks: list[list[Line]] = []
    cur: list[Line] = []
    for ln in lines:
        if ln.text.strip():
            cur.append(ln)
        elif cur:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    if btype not in ENTRY_TYPES:
        return chunks
    merged: list[list[Line]] = []
    for ch in chunks:
        first = ch[0].text.strip()
        if merged and (_is_bullet(first) or first[:1].islower()):
            merged[-1].extend(ch)
        else:
            merged.append(list(ch))
    out: list[list[Line]] = []
    for ch in merged:
        out.extend(_split_on_dates(ch))
    return out


# Heading detection
_MD_HEAD = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
_DECORATED = re.compile(r"^[=\-_*~#]{2,}\s*(.+?)\s*[=\-_*~#]{2,}$")
_UNDERLINE = re.compile(r"^[=\-_~*]{3,}$")
_INLINE = re.compile(r"^(?P<h>[^:]{2,40}):\s*(?P<rest>\S.*)$")


def _style(line: Line, body_size):
    letters = [c for c in line.text if c.isalpha()]
    caps = len(letters) >= 3 and all(c.isupper() for c in letters)
    big = bool(line.size and body_size and line.size >= body_size * 1.12)
    return caps, bool(line.bold), big


def _body_size(lines: list[Line]):
    sizes = [ln.size for ln in lines if ln.size and len(
        ln.text) >= 25 and not ln.text.isupper()]
    if len(sizes) < 3:
        sizes = [ln.size for ln in lines if ln.size and len(ln.text) >= 12]
    return statistics.median(sizes) if sizes else None


def _is_skill_name(text: str) -> bool:
    """True when the whole line is a known skill or tool ("SQL", "AWS", "C#"): a list item, never a
    heading, even when it is written in capitals like the CV's real headings."""
    try:
        from .vocab import get_vocab
        words = get_vocab().canonicalize(text).split()
    except Exception:
        return False
    return bool(words) and all(w.startswith("k_") for w in words)


def _next_nonempty(lines: list[Line], i: int):
    for j in range(i + 1, len(lines)):
        if lines[j].text.strip():
            return j
    return None


def segment(lines: list[Line]) -> tuple[list[Block], dict]:
    """Return (blocks, info). info has diagnostics such as n_known_headings."""
    L = lines
    n = len(L)
    body_size = _body_size(L)

    headings: dict[int, tuple[str, str, str | None]] = {}
    marker_idx: set[int] = set()

    for i, ln in enumerate(L):
        t = ln.text.strip()
        if not t or _is_bullet(t) or i in marker_idx:
            continue
        m = _MD_HEAD.match(t)
        if m:
            h = m.group(1)
            headings[i] = (classify_heading(h) or "unknown", h, None)
            continue
        m = _DECORATED.match(t)
        if m and not _UNDERLINE.match(t):
            h = m.group(1)
            headings[i] = (classify_heading(h) or "unknown", h, None)
            continue
        j = _next_nonempty(L, i)
        if (j is not None and j == i + 1 and _UNDERLINE.match(L[j].text.strip())
                and len(t) <= 50 and not _UNDERLINE.match(t)):
            headings[i] = (classify_heading(
                t) or "unknown", t.rstrip(":"), None)
            marker_idx.add(j)
            continue
        m = _INLINE.match(t)
        if m:
            typ = classify_heading(m.group("h"), exact_only=True)
            if typ:
                headings[i] = (typ, m.group("h").strip(),
                               m.group("rest").strip())
                continue
        if len(t.split()) <= 6:
            typ = classify_heading(t)
            if typ:
                headings[i] = (typ, t.rstrip(":").strip(), None)

    known = {i: h for i, h in headings.items() if h[0] != "unknown"}

    if known:
        first = min(known)
        for i in [i for i, h in headings.items() if h[0] == "unknown" and i < first]:
            L[i] = L[i].with_text(headings[i][1])
            del headings[i]
            marker_idx.discard(i + 1)

    sig = None
    sig_size = None
    if known:
        styles = [_style(L[i], body_size)
                  for i in known if i not in marker_idx]
        if styles:
            common = Counter(styles).most_common(1)[0][0]
            if any(common):
                sig = common
            sizes = [L[i].size for i in known if L[i].size]
            sig_size = statistics.median(sizes) if sizes else None

    first_known = min(known) if known else None
    for i, ln in enumerate(L):
        t = ln.text.strip()
        if (not t or i in headings or i in marker_idx or _is_bullet(t) or len(t) > 45
                or len(t.split()) > 6 or re.search(r"\d", t) or t.endswith((".", ",", ";"))):
            continue
        if first_known is None or i < first_known or _is_skill_name(t):
            continue
        nxt = _next_nonempty(L, i)
        if nxt is None or _is_dateish(L[nxt].text) or nxt in headings:
            continue
        prev_blank = i == 0 or not L[i - 1].text.strip()
        ok = False
        if ln.heading_style:
            ok = True
        elif sig is not None:
            style = _style(ln, body_size)
            if all(style[k] for k in range(3) if sig[k]):
                if sig_size and ln.size and abs(ln.size - sig_size) > max(0.8, 0.08 * sig_size):
                    ok = False
                elif ln.size is None and not prev_blank:
                    ok = False
                else:
                    ok = True
        if ok:
            headings[i] = ("unknown", t.rstrip(":").strip(), None)

    blocks: list[Block] = []
    order = sorted(headings)
    drop_idx = set(marker_idx)

    def body(a: int, b: int) -> list[Line]:
        return [L[k] for k in range(a, b) if k not in drop_idx]

    first_idx = order[0] if order else n
    head_lines = body(0, first_idx)
    if any(ln.text.strip() for ln in head_lines):
        btype = "header" if order else "unsorted"
        blocks.append(Block(btype, "", split_entries(head_lines, "header"), 0))

    for k, idx in enumerate(order):
        typ, heading, rest = headings[idx]
        end = order[k + 1] if k + 1 < len(order) else n
        content = body(idx + 1, end)
        if rest:
            content = [L[idx].with_text(rest)] + content
        blocks.append(Block(typ, heading, split_entries(content, typ), 0))

    for pos, b in enumerate(blocks):
        b.position = pos
    info = {"n_known_headings": len(
        known), "n_unknown_headings": len(headings) - len(known)}
    return blocks, info
