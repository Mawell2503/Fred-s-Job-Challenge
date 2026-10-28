"""A stand-in for the LLM: NO AI, plain rules, same answer every time.

Used when LLM_BACKEND=fake, for tests and for running the whole pipeline offline (no cost,
no account). It reads the same prompt a real model would get and returns a well-formed
answer of the requested kind (the schema's "title"). Its judgement is deliberately simple;
it exists to exercise the plumbing, not to hire anyone.
"""
from __future__ import annotations

import json
import re

from .llm import LLMError

_STOP = set("""a an the and or of in on at to for with by from as is are be this that it its
least years year experience minimum strong good knowledge skills skill ability able
must have has candidate role job work working plus nice level de en et ans""".split())


def section(prompt: str, name: str) -> str | None:
    """Text inside <name>...</name>, or None."""
    m = re.search(rf"<{name}>\n?(.*?)\n?</{name}>", prompt, re.S)
    return m.group(1) if m else None


def json_section(prompt: str, name: str):
    body = section(prompt, name)
    return json.loads(body) if body else None


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-zÀ-ÿ+#.]{3,}", text or "") if w.lower().strip(".") not in _STOP]


def cv_lines(cv: str) -> list[str]:
    return [ln.strip() for ln in (cv or "").splitlines()
            if ln.strip() and not ln.strip().startswith("[")]


def quote_of(line: str) -> str:
    """A verbatim excerpt of one CV line (max 30 words, bullet marker removed)."""
    t = re.sub(r"^\s*-\s+", "", line).strip()
    return " ".join(t.split()[:30])


def find_line(cv: str, terms) -> str | None:
    from cv_reader.vocab import get_vocab
    vocab = get_vocab()
    for ln in cv_lines(cv):
        canon = vocab.canonicalize(ln)
        for t in terms:
            if len(t) >= 3 and vocab.contains(canon, t):
                return ln
    return None


# ---------------- Clara ----------------
def _clara(prompt: str) -> dict:
    def crit(line):
        text = line.strip(" -•\t")
        low = text.lower()
        m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|ans)", low)
        if m:
            return {"text": text, "type": "years", "keywords": words(text)[:3], "min_years": float(m.group(1))}
        typ = "language" if re.search(r"english|french|german|spanish|creole|anglais|francais|français", low) else \
            "degree" if re.search(r"degree|bachelor|master|bsc|msc|phd", low) else "skill"
        return {"text": text, "type": typ, "keywords": words(text)[:4], "min_years": None}

    must = [crit(x) for x in (section(prompt, "admin_must_haves") or "").splitlines() if x.strip()]
    nice = [crit(x) for x in (section(prompt, "admin_nice_to_haves") or "").splitlines() if x.strip()]
    ad = section(prompt, "job_description") or ""
    if not must and ad:
        must = [crit(s) for s in re.split(r"[\n.;]", ad) if re.search(r"required|must|minimum|essential", s, re.I)][:6]
    return {"summary": (ad.strip().split("\n")[0] if ad else section(prompt, "profession") or "")[:400],
            "must_haves": must, "nice_to_haves": nice,
            "suggested_drop_sections": ["interests", "references", "declaration"]}


# ---------------- Jules ----------------
_CRIT_LINE = re.compile(r"^- (?P<id>[MN]\d+) \((?P<kind>[^)]*)\): (?P<text>.*)$", re.M)


def _jules(prompt: str) -> dict:
    cv = section(prompt, "cv") or ""
    out = []
    for m in _CRIT_LINE.finditer(section(prompt, "criteria") or ""):
        line = find_line(cv, words(m.group("text")))
        if line:
            out.append({"id": m.group("id"), "verdict": "met", "quotes": [quote_of(line)],
                        "reason": "The CV mentions it directly."})
        else:
            out.append({"id": m.group("id"), "verdict": "not_mentioned", "quotes": [],
                        "reason": "The CV does not say."})
    met = sum(a["verdict"] == "met" for a in out)
    return {"criteria": out, "overall": f"Meets {met} of {len(out)} criteria on the evidence in the CV."}


# ---------------- Remi's optional gate ----------------
def _remi_gate(prompt: str) -> dict:
    cv = section(prompt, "cv") or ""
    answers = []
    for line in (section(prompt, "requirements") or "").splitlines():
        m = re.match(r"- (\w+): (.*)", line.strip())
        if not m:
            continue
        hit = find_line(cv, words(m.group(2)))
        answers.append({"id": m.group(1), "status": "PASS" if hit else "UNKNOWN",
                        "quote": quote_of(hit) if hit else ""})
    return {"answers": answers}


def respond(system: str, prompt: str, schema: dict) -> dict:
    kind = schema.get("title")
    if kind == "clara":
        return _clara(prompt)
    if kind == "jules":
        return _jules(prompt)
    if kind == "remi_gate":
        return _remi_gate(prompt)
    if kind == "committee_position":
        from .committee.fake import position
        return position(system, prompt)
    if kind == "gaston_report":
        from .committee.fake import gaston
        return gaston(prompt)
    raise LLMError(f"The fake backend does not know how to answer {kind!r}.")


__all__ = ["respond", "section", "json_section", "quote_of", "find_line", "cv_lines", "words"]
