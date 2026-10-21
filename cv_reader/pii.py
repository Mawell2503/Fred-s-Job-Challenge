"""
Personal-information split

Everything that identifies the candidate (name, email, phone, links, date of
birth, nationality, marital status, address, national ID ...) is moved OUT of
the CV text into a separate record.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, OrderedDict

from .models import Block, Line

NAME_PLACEHOLDER = "[CANDIDATE]"

# labelled personal fields
LABELS: dict[str, list[str]] = {
    "date_of_birth": ["date of birth", "d.o.b.", "d.o.b", "dob", "birth date", "birthdate",
                      "born", "date de naissance"],
    "place_of_birth": ["place of birth", "birthplace", "lieu de naissance"],
    "age": ["age"],
    "nationality": ["nationality", "citizenship", "nationalite"],
    "marital_status": ["marital status", "civil status", "status civil", "etat civil"],
    "gender": ["gender", "sex", "sexe"],
    "address": ["home address", "postal address", "permanent address", "current address",
                "address", "residence", "adresse"],
    "religion": ["religion"],
    "national_id": ["national identity card", "national id", "nic no", "nic", "id number",
                    "id no", "identity card", "passport number", "passport no", "passport"],
    "name": ["full name", "candidate name", "name", "nom"],
}
_LABEL_TO_KEY = {lab: key for key, labs in LABELS.items() for lab in labs}
_LABEL_ALTS = "|".join(re.escape(l)
                       for l in sorted(_LABEL_TO_KEY, key=len, reverse=True))
_PAIR = re.compile(
    rf"\b(?P<label>{_LABEL_ALTS})\s*[:：]\s*(?P<value>[^|;•]+?)"
    rf"(?=\s{{2,}}|\s*[|;•]|$|\s+(?:{_LABEL_ALTS})\s*[:：])", re.I)
_TABLE_ROW = re.compile(
    rf"^(?P<label>{_LABEL_ALTS})\s{{2,}}(?P<value>\S.*)$", re.I)

# contact patterns
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_URL = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>\"')\]]+|"
    r"\b(?:[a-z]{2,3}\.)?(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com|"
    r"twitter\.com|x\.com|medium\.com|stackoverflow\.com|kaggle\.com)/[^\s<>\"')\]]+")
_PHONE = re.compile(r"(?<![\w@/.$€£#])(\+?\(?\d[\d\s().\-]{5,}\d)(?![\w@/%])")
_NIC = re.compile(r"\b[A-Z]\d{12}[A-Z0-9]\b")
_CONTACT_PREFIX = re.compile(
    r"(?i)(?:e-?mail|mail|tel(?:ephone)?|phone|mobile|mob|cell|whatsapp|fax|linkedin|github|"
    r"website|web|url|portfolio|skype|contact)(?:\s*(?:no|number|nr))?\.?\s*[:\-–|]?\s*$")
_PHONE_LABEL_BEFORE = re.compile(
    r"(?i)(?:tel(?:ephone)?|phone|mobile|mob|cell|whatsapp|fax|call)(?:\s*(?:no|number|nr))?\.?\s*[:\-–|]?\s*$")
_YEAR = r"(?:19|20)\d{2}"
_STREET = re.compile(
    r"(?i)\b\d{1,4}[A-Za-z]?,?\s+[A-Za-z' .\-]{3,40}\b(?:road|rd|street|st|avenue|ave|lane|ln|drive|dr|"
    r"boulevard|blvd|rue|route|chemin|close|crescent|way|court|ct|square|place)\b\.?")

# name detection
_NAME_STOP = {
    "curriculum", "vitae", "resume", "cv", "engineer", "developer", "manager", "analyst",
    "student", "professional", "profile", "summary", "contact", "email", "phone", "address",
    "objective", "experience", "education", "skills", "intern", "consultant", "specialist",
    "designer", "accountant", "officer", "assistant", "director", "administrator",
    "technician", "teacher", "nurse", "architect", "senior", "junior", "lead", "executive",
    "coordinator", "supervisor", "software", "data", "scientist", "marketing", "sales",
    "finance", "financial", "business", "project", "page", "of", "and", "the", "for",
    "ltd", "inc", "llc", "university", "college", "school", "limited", "bank", "group",
    "personal", "details", "information", "application", "applicant", "candidate",
}
_PARTICLES = {"de", "da", "di", "del", "van", "von", "der", "den", "la", "le", "al", "bin",
              "ibn", "dos", "das", "du", "el", "ben"}
_NAME_TOKEN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.\-]*$")


def _looks_like_name(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 40 or re.search(r"[\d@:/|,()\[\]]", t):
        return False
    toks = t.split()
    if not 2 <= len(toks) <= 4:
        return False
    if {x.lower().strip(".") for x in toks} & _NAME_STOP:
        return False
    for tok in toks:
        if not _NAME_TOKEN.match(tok):
            return False
        if tok.lower() in _PARTICLES:
            continue
        if not tok[0].isupper():
            return False
    return sum(1 for x in toks if x.lower() not in _PARTICLES) >= 2


# helpers
def _label_key(text: str):
    return _LABEL_TO_KEY.get(text.strip().rstrip(":").strip().lower())


def _clean_url(u: str) -> str:
    return u.rstrip(".,;:)")


def _valid_phone(s: str, before: str, btype: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if not 7 <= len(digits) <= 15:
        return False
    if re.fullmatch(rf"{_YEAR}\s*[-–/.]\s*{_YEAR}", s.strip()):
        return False
    if re.fullmatch(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}", s.strip()):
        return False
    groups = [g for g in re.split(r"\D+", s) if g]
    if groups and all(re.fullmatch(_YEAR, g) for g in groups):
        return False
    labelled = bool(_PHONE_LABEL_BEFORE.search(before))
    if btype in ("header", "personal", "unsorted") or labelled:
        return True
    return s.strip().startswith(("+", "(")) or (len(digits) >= 9 and bool(re.search(r"[\s.\-]", s)))


def _spans(text: str, btype: str):
    """Find contact-like spans. Returns [(start, end, category, value)] without overlaps."""
    found = []
    for m in _EMAIL.finditer(text):
        found.append((m.start(), m.end(), "email", m.group(0).rstrip(".")))
    for m in _URL.finditer(text):
        found.append((m.start(), m.end(), "url", _clean_url(m.group(0))))
    for m in _NIC.finditer(text):
        found.append((m.start(), m.end(), "national_id", m.group(0)))
    for m in _PHONE.finditer(text):
        if _valid_phone(m.group(1), text[:m.start(1)], btype):
            found.append((m.start(1), m.end(1), "phone", m.group(1).strip()))
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    out, last_end = [], -1
    for s in found:
        if s[0] >= last_end:
            out.append(s)
            last_end = s[1]
    return out


def mask_text(text: str) -> str:
    """Replace contact details with tags (used so logs never contain raw PII)."""
    t = _EMAIL.sub("[email]", text)
    t = _URL.sub("[url]", t)
    t = _NIC.sub("[id]", t)
    t = _PHONE.sub(lambda m: "[phone]" if _valid_phone(
        m.group(1), "", "personal") else m.group(0), t)
    return t


def _tidy(text: str, had_bullet: bool) -> str:
    t = re.sub(r"(?:\s*[|;•·]\s*){2,}", " | ", text)
    t = re.sub(r"^[\s|,;:/•·\-–]+|[\s|,;:/•·\-–]+$", "", t)
    t = re.sub(r" {3,}", "  ", t).strip()
    if t and had_bullet and not t.startswith("- "):
        t = "- " + t
    return t


class _Store:
    def __init__(self):
        self.name = None
        self.emails = OrderedDict()
        self.phones = OrderedDict()
        self.urls = {"linkedin": OrderedDict(), "github": OrderedDict(),
                     "other": OrderedDict()}
        self.personal: dict[str, list[str]] = {}
        self.address_lines = OrderedDict()
        self.national_ids = OrderedDict()
        self.removed = Counter()

    def add_url(self, u: str):
        low = u.lower()
        key = "linkedin" if "linkedin.com" in low else "github" if "github.com" in low else "other"
        self.urls[key][u] = None

    def add_labeled(self, key: str, value: str):
        value = value.strip().strip(".")
        if not value:
            return
        if key == "name":
            if self.name is None:
                self.name = value
        elif key == "address":
            self.address_lines[value] = None
        elif key == "national_id":
            self.national_ids[value] = None
        else:
            self.personal.setdefault(key, [])
            if value not in self.personal[key]:
                self.personal[key].append(value)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "emails": list(self.emails),
            "phones": list(self.phones),
            "urls": {k: list(v) for k, v in self.urls.items()},
            "personal": {k: "; ".join(v) for k, v in self.personal.items()},
            "address_lines": list(self.address_lines),
            "national_ids": list(self.national_ids),
        }


def _record_spans(store: _Store, spans) -> None:
    for _, _, cat, val in spans:
        if cat == "email":
            store.emails[val.lower()] = None
        elif cat == "phone":
            store.phones[re.sub(r"\s+", " ", val)] = None
        elif cat == "url":
            store.add_url(val)
        elif cat == "national_id":
            store.national_ids[val] = None


def _process_entry(entry: list[Line], btype: str, store: _Store) -> None:
    i = 0
    while i < len(entry):
        ln = entry[i]
        text = ln.text
        if not text.strip():
            i += 1
            continue
        had_bullet = text.startswith("- ")
        body = text[2:] if had_bullet else text
        changed = False
        key = _label_key(body)
        if key and i + 1 < len(entry) and entry[i + 1].text.strip() and not _label_key(entry[i + 1].text):
            take = 3 if key == "address" else 1
            vals = []
            j = i + 1
            while j < len(entry) and len(vals) < take and entry[j].text.strip() \
                    and len(entry[j].text) <= 70 and not _label_key(entry[j].text):
                vals.append(entry[j].text.strip())
                entry[j].text = ""
                j += 1
            store.add_labeled(key, ", ".join(vals) if key ==
                              "address" else vals[0])
            store.removed[key] += 1
            ln.text = ""
            i = j
            continue
        m = _TABLE_ROW.match(body)
        if m and _label_key(m.group("label")):
            store.add_labeled(_LABEL_TO_KEY[m.group(
                "label").lower()], m.group("value"))
            store.removed[_LABEL_TO_KEY[m.group("label").lower()]] += 1
            ln.text = ""
            i += 1
            continue

        pairs = []
        for m in _PAIR.finditer(body):
            before = body[:m.start()]
            if before.strip() == "" or before.rstrip().endswith(("|", ";", "•")) or before.endswith("  "):
                pairs.append(m)
        if pairs:
            for m in reversed(pairs):
                k = _LABEL_TO_KEY[m.group("label").lower()]
                store.add_labeled(k, m.group("value"))
                store.removed[k] += 1
                body = body[:m.start()] + " " + body[m.end():]
            changed = True

        spans = _spans(body, btype)
        if spans:
            _record_spans(store, spans)
            for s, e, cat, _ in reversed(spans):
                pm = _CONTACT_PREFIX.search(body[:s])
                start = pm.start() if pm else s
                body = body[:start] + " " + body[e:]
                store.removed[cat if cat != "url" else "link"] += 1
            changed = True

        if changed:
            ln.text = _tidy(body, had_bullet)
        i += 1


def split_pii(blocks: list[Block], aux_text: list[str]) -> tuple[dict, Counter]:
    """Remove PII from `blocks` in place. Returns (pii_record, removed_counts)."""
    store = _Store()
    scan = [b for b in blocks if b.type != "references"]

    head = scan[0] if scan and scan[0].type in ("header", "unsorted") else None
    name_line = None
    if head is not None:
        cands = []
        for idx, ln in enumerate(l for l in head.lines if l.text.strip()):
            if idx >= 10:
                break
            if _looks_like_name(ln.text):
                cands.append((-(ln.size or 0), idx, ln))
        if cands:
            cands.sort(key=lambda c: (c[0], c[1]))
            name_line = cands[0][2]
            store.name = name_line.text.strip()

    if name_line is None:
        sizes = [
            ln.size for b in scan for ln in b.lines if ln.size and ln.text.strip()]
        body = statistics.median(sizes) if sizes else 0
        fb, seen = [], 0
        for b in scan:
            for ln in b.lines:
                if not ln.text.strip():
                    continue
                seen += 1
                if seen <= 60 and body and ln.size and ln.size >= 1.15 * body and _looks_like_name(ln.text):
                    fb.append((-ln.size, seen, ln))
        if fb:
            fb.sort(key=lambda c: (c[0], c[1]))
            name_line = fb[0][2]
            store.name = name_line.text.strip()

    for b in scan:
        for entry in b.entries:
            _process_entry(entry, b.type, store)

    for a in aux_text:
        a = a.strip()
        if a.lower().startswith("mailto:"):
            store.emails[a[7:].split("?")[0].lower()] = None
        elif a.lower().startswith("tel:"):
            store.phones[a[4:]] = None
        elif a.lower().startswith(("http://", "https://")):
            store.add_url(a)

    if name_line is not None:
        name_line.text = ""
        store.removed["name"] += 1
    if store.name:
        pat = re.compile(r"\b" + r"\s+".join(re.escape(t)
                         for t in store.name.split()) + r"\b", re.I)
        for b in scan:
            for ln in b.lines:
                if ln.text and pat.search(ln.text):
                    ln.text = pat.sub(NAME_PLACEHOLDER, ln.text)
                    store.removed["name_mention"] += 1

    if head is not None:
        for ln in head.lines:
            if ln.text and _STREET.search(ln.text):
                store.address_lines[ln.text.strip()] = None
                store.removed["address"] += 1
                ln.text = ""

    for b in blocks:
        b.entries = [[ln for ln in e if ln.text.strip()] for e in b.entries]
        b.entries = [e for e in b.entries if e]
    return store.to_dict(), store.removed
