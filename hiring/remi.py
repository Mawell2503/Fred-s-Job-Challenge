"""REMI - compares every candidate with the job and shortlists the 20 closest.

Step 1  Must-have gate (second selection cycle). Each must-have gets one status:
          PASS     the CV clearly meets it
          FAIL     the CV clearly contradicts it   -> candidate ELIMINATED
          UNKNOWN  the CV doesn't say              -> kept, but penalised
        Checks are plain code on Oscar's structured record:
          years          total dated experience vs min_years (FAIL only when every job is
                         dated, so missing dates can never eliminate anyone)
          language       FAIL only if the CV lists its languages and this one isn't there
          degree         FAIL only if the CV has an Education section without it
          skill, certification, other   PASS if mentioned, otherwise UNKNOWN (never FAIL)
        Optional (--llm-gate): one LLM call per candidate re-reads the UNKNOWN skill/other
        must-haves; its answer only counts when its CV quote passes the citation_checker.

Step 2  Ranking (third selection cycle), survivors only:
          score = w_keywords * Keywords + w_meaning * Meaning + w_nice * Nice-to-haves
                  - unknown_penalty * (number of UNKNOWN must-haves)
          Keywords  share of the job's criteria whose keywords appear in the CV
                    (full weight in Skills/Experience, 60 % elsewhere)
          Meaning   embedding similarity between the CV and the job (best candidate = 1)
          Nice      share of nice-to-haves found
        Keep the top 20, with every candidate's breakdown saved so the ranking is explainable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from cv_reader.embed import backend_name, cosine, embed
from cv_reader.vocab import get_vocab

from .citation_checker import quote_exists
from .criteria import all_criteria

DEFAULT_WEIGHTS = {"keywords": 0.4, "meaning": 0.4, "nice": 0.2, "unknown_penalty": 0.1}
STRONG_SECTIONS = ("skills", "experience", "certifications", "education", "languages")
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"


# ---------------- evidence helpers ----------------
class CandidateView:
    """Canonicalized text of a candidate, per section, for fast keyword checks."""

    def __init__(self, cand: dict):
        self.vocab = get_vocab()
        self.cid = cand["candidate_id"]
        self.rec = cand["record"]
        self.text = cand["text"]
        self.full_text = cand["full_text"]
        self.canon_all = self.rec.get("canonical_text") or self.vocab.canonicalize(self.text)
        self.canon_sec = {t: self.vocab.canonicalize(s) for t, s in (self.rec.get("sections") or {}).items()}
        self.lines = [ln for ln in self.text.splitlines() if ln.strip() and not ln.startswith("[")]

    def find(self, keywords, sections=None):
        """First keyword found -> (keyword, CV line containing it); else None."""
        hay = [self.canon_sec.get(s, "") for s in sections] if sections else [self.canon_all]
        for kw in keywords:
            if any(self.vocab.contains(h, kw) for h in hay):
                return kw, self._line_for(kw)
        return None

    def _line_for(self, kw: str) -> str:
        for ln in self.lines:
            if self.vocab.contains(self.vocab.canonicalize(ln), kw):
                return ln.strip()[:200]
        return ""

    def has_section(self, *types) -> bool:
        return any(self.canon_sec.get(t, "").strip() for t in types)


def check(c: dict, v: CandidateView) -> dict:
    """Return {"status", "evidence"} for one criterion and one candidate."""
    kws, typ = c.get("keywords") or [], c["type"]

    if typ == "years":
        years = v.rec.get("total_years_experience")
        exp = v.rec.get("experience") or []
        need = c.get("min_years", 0)
        field = v.find(kws, ("experience", "summary", "header")) if kws else ("", "")
        if years is None:
            return {"status": UNKNOWN, "evidence": "No dated experience found in the CV."}
        if years >= need:
            if field is None:
                return {"status": UNKNOWN, "evidence": f"{years} years of dated experience, but the field "
                                                      f"({', '.join(kws)}) is not mentioned in it."}
            return {"status": PASS, "evidence": f"{years} years of dated experience. {field[1]}".strip()}
        if any(e.get("months") is None for e in exp):
            return {"status": UNKNOWN, "evidence": f"Only {years} years can be dated; some jobs have no dates."}
        return {"status": FAIL, "evidence": f"Only {years} years of experience (needs {need:g})."}

    hit = v.find(kws)
    if hit:
        return {"status": PASS, "evidence": hit[1] or f"mentions '{hit[0]}'"}
    if typ == "language" and v.rec.get("languages_listed"):
        return {"status": FAIL, "evidence": "Languages listed: " + ", ".join(v.rec["languages_listed"])}
    if typ == "degree" and v.has_section("education"):
        first = (v.rec.get("education") or [""])[0].splitlines()[0] if v.rec.get("education") else ""
        return {"status": FAIL, "evidence": f"Education listed without it. {first}".strip()}
    return {"status": UNKNOWN, "evidence": "Not mentioned in the CV."}


# ---------------- optional LLM gate ----------------
_GATE_SCHEMA = {
    "title": "remi_gate",
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "string"},
                       "status": {"type": "string", "enum": [PASS, FAIL, UNKNOWN]},
                       "quote": {"type": "string"}},
        "required": ["id", "status", "quote"]}}},
    "required": ["answers"],
}
_GATE_SYSTEM = """You check whether a CV meets specific job requirements.
For each requirement answer PASS (the CV clearly shows it), FAIL (the CV clearly contradicts it)
or UNKNOWN (the CV does not say). Silence is UNKNOWN, never FAIL.
For PASS and FAIL, "quote" must be copied word for word from the CV (5-30 words, no "...").
For UNKNOWN, quote is an empty string.
"""


def llm_resolve(v: CandidateView, unknowns: list[dict], gate: dict) -> None:
    from .llm import DATA_NOTE, complete_json, tag
    reqs = "\n".join(f"- {c['id']}: {c['text']}" for c in unknowns)
    out = complete_json(_GATE_SYSTEM + DATA_NOTE, tag("requirements", reqs) + "\n\n" + tag("cv", v.full_text),
                        _GATE_SCHEMA)
    for a in out.get("answers", []):
        g = gate.get(a.get("id"))
        if not g or a.get("status") not in (PASS, FAIL):
            continue
        if quote_exists(a.get("quote", ""), v.full_text):     # unverified answers are ignored
            g.update(status=a["status"], evidence=a["quote"], by="llm")


# ---------------- main ----------------
def job_text(job: dict) -> str:
    parts = [job.get("title", ""), job.get("summary", "")]
    for c in all_criteria(job):
        parts.append(c["text"] + ". " + ", ".join(c.get("keywords", [])))
    return "\n".join(p for p in parts if p)


def keyword_score(job: dict, v: CandidateView) -> float:
    groups = [c.get("keywords") or [] for c in all_criteria(job)]
    title = job.get("title", "")
    if title:
        groups.append([title])
    groups = [g for g in groups if g]
    if not groups:
        return 0.0
    total = 0.0
    for g in groups:
        if v.find(g, STRONG_SECTIONS):
            total += 1.0
        elif v.find(g):
            total += 0.6
    return total / len(groups)


def run(job: dict, candidates: list[dict], top: int = 20, use_llm_gate: bool = False,
        weights: dict | None = None, progress=None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(job.get("remi_weights") or {}), **(weights or {})}
    must, nice = job.get("must_haves", []), job.get("nice_to_haves", [])
    backend = backend_name()
    job_vec = embed(job_text(job), backend)["vector"]
    warnings, eliminated, survivors = [], [], []

    for i, cand in enumerate(candidates, 1):
        v = CandidateView(cand)
        gate = {c["id"]: {"id": c["id"], "text": c["text"], **check(c, v)} for c in must}
        unknown_soft = [c for c in must if gate[c["id"]]["status"] == UNKNOWN and c["type"] in ("skill", "other", "certification")]
        if use_llm_gate and unknown_soft and not any(g["status"] == FAIL for g in gate.values()):
            try:
                llm_resolve(v, unknown_soft, gate)
            except Exception as e:
                warnings.append(f"{v.cid}: LLM gate skipped ({e})")
        fails = [g for g in gate.values() if g["status"] == FAIL]
        if fails:
            eliminated.append({"candidate_id": v.cid, "failed": fails})
        else:
            nice_res = [{"id": c["id"], "text": c["text"], **check(c, v)} for c in nice]
            emb = cand.get("embedding")
            vec = emb["vector"] if emb and emb.get("backend") == backend else embed(cand["text"], backend)["vector"]
            survivors.append({
                "candidate_id": v.cid,
                "headline": v.rec.get("headline", ""),
                "total_years_experience": v.rec.get("total_years_experience"),
                "gate": list(gate.values()),
                "nice": nice_res,
                "_kw": keyword_score(job, v),
                "_cos": cosine(vec, job_vec),
            })
        if progress:
            progress(i, len(candidates))

    # Meaning: similarity relative to the closest candidate (best = 1.0), so it sits on the
    # same 0-1 scale as the other parts whatever embedding backend is used
    hi = max([s["_cos"] for s in survivors] or [0])
    for s in survivors:
        meaning = max(0.0, s["_cos"]) / hi if hi > 0 else 0.0
        nice_share = (sum(n["status"] == PASS for n in s["nice"]) / len(s["nice"])) if s["nice"] else 0.0
        n_unknown = sum(g["status"] == UNKNOWN for g in s["gate"])
        score = (w["keywords"] * s["_kw"] + w["meaning"] * meaning + w["nice"] * nice_share
                 - w["unknown_penalty"] * n_unknown)
        s["breakdown"] = {"keywords": round(s.pop("_kw"), 3), "meaning": round(meaning, 3),
                          "meaning_raw_cosine": round(s.pop("_cos"), 4),
                          "nice_to_haves": round(nice_share, 3), "unknown_must_haves": n_unknown}
        s["score"] = round(score, 4)

    survivors.sort(key=lambda s: (-s["score"], s["candidate_id"]))
    for rank, s in enumerate(survivors, 1):
        s["rank"] = rank
    if len(survivors) < top:
        warnings.append(f"Only {len(survivors)} candidate(s) passed the must-haves (asked for {top}).")

    return {
        "agent": "REMI",
        "job_title": job.get("title"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "weights": w,
        "embedding_backend": backend,
        "llm_gate": use_llm_gate,
        "counts": {"candidates": len(candidates), "eliminated": len(eliminated),
                   "passed_gate": len(survivors), "shortlisted": min(top, len(survivors))},
        "shortlist": survivors[:top],
        "not_shortlisted": [{"candidate_id": s["candidate_id"], "rank": s["rank"], "score": s["score"]}
                            for s in survivors[top:]],
        "eliminated": eliminated,
        "warnings": warnings,
    }


__all__ = ["run", "check", "CandidateView", "DEFAULT_WEIGHTS"]
