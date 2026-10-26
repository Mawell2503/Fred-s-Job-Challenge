"""The job record ("fiche mission") that Clara produces and the admin edits.

{
  "title": "Senior Accountant",                       <- Admin_INPUT#1 (profession)
  "summary": "...",                                   <- Clara's one-paragraph reading of the job
  "must_haves":   [criterion, ...],                   <- Admin_INPUT#2 (missing = eliminated)
  "nice_to_haves":[criterion, ...],                   <- Admin_INPUT#3 (counts, never blocks)
  "suggested_drop_sections": ["interests", ...]       <- Oscar keep/drop proposal for this role
}

criterion = {
  "id": "M1" | "N1" ...,
  "text": "At least 5 years of accounting experience",   (shown to people and to Jules)
  "type": "skill" | "language" | "years" | "degree" | "certification" | "other",
  "keywords": ["accounting", "comptabilite"],            (any one is enough; Remi's check)
  "min_years": 5                                         (only for type "years")
}
"""
from __future__ import annotations

import re

TYPES = ("skill", "language", "years", "degree", "certification", "other")
SECTION_TYPES = ("summary", "experience", "education", "skills", "languages", "certifications",
                 "training", "projects", "awards", "publications", "volunteering", "interests",
                 "references", "personal", "declaration", "unknown")


def _words(text: str) -> list[str]:
    stop = {"and", "or", "the", "of", "in", "with", "a", "an", "at", "least", "years", "year",
            "experience", "minimum", "strong", "good", "knowledge", "de", "en", "et", "ans"}
    return [w for w in re.findall(r"[A-Za-zÀ-ÿ#+.]{2,}", text) if w.lower() not in stop][:4]


def clean_criterion(c: dict, cid: str) -> dict:
    text = str(c.get("text", "")).strip()
    typ = str(c.get("type", "other")).strip().lower()
    if typ not in TYPES:
        typ = "other"
    kws = [str(k).strip() for k in (c.get("keywords") or []) if str(k).strip()]
    kws = list(dict.fromkeys(kws))[:10]
    if not kws and typ != "years":
        kws = _words(text)
    out = {"id": cid, "text": text or ", ".join(kws), "type": typ, "keywords": kws}
    if typ == "years":
        try:
            out["min_years"] = max(0.0, float(c.get("min_years")))
        except (TypeError, ValueError):
            m = re.search(r"(\d+(?:\.\d+)?)", text)
            out["min_years"] = float(m.group(1)) if m else 0.0
    return out


def clean_criteria(data: dict) -> dict:
    """Validate and normalise a job record (from Clara or from the admin's edits).
    Raises ValueError with a readable message when it is unusable."""
    if not isinstance(data, dict):
        raise ValueError("The job record must be an object.")
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("The job needs a title (what profession are you looking for?).")
    must = [clean_criterion(c, f"M{i}") for i, c in enumerate(data.get("must_haves") or [], 1)
            if isinstance(c, dict) and (c.get("text") or c.get("keywords"))]
    nice = [clean_criterion(c, f"N{i}") for i, c in enumerate(data.get("nice_to_haves") or [], 1)
            if isinstance(c, dict) and (c.get("text") or c.get("keywords"))]
    drop = [s for s in (data.get("suggested_drop_sections") or []) if s in SECTION_TYPES]
    return {"title": title[:120], "summary": str(data.get("summary", "")).strip()[:2000],
            "must_haves": must, "nice_to_haves": nice, "suggested_drop_sections": drop}


def all_criteria(job: dict) -> list[dict]:
    return [dict(c, kind="must") for c in job.get("must_haves", [])] + \
           [dict(c, kind="nice") for c in job.get("nice_to_haves", [])]
