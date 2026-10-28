"""JULES - reads the 20 shortlisted CVs in full and picks the 10 finalists.

For each of Remi's 20:
  1. The LLM reads the FULL CV (personal details and referees removed, no other section dropped) against
     every Clara criterion, one by one: met / not_met / not_mentioned, with word-for-word
     CV quotes as proof and a one-line reason.
  2. citation_checker verifies every quote BEFORE scoring. A "met" with no verified quote
     becomes "unverified" and earns 0 points, so an invented quote can never win.
  3. Points: 1 per criterion met (must-haves can be weighted with jules_must_weight).
Then: rank the 20 by verified points (Remi's score breaks ties), keep the top 10 (the
committee then picks the final 5), and give
each finalist a detailed score + the list of what's missing, with the requirement text and
CV quotes side by side. Names are re-attached from Oscar's personal-details file only here.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .citation_checker import quote_exists
from .criteria import all_criteria
from .llm import DATA_NOTE, complete_json, env_number, tag

MET, NOT_MET, NOT_MENTIONED, UNVERIFIED = "met", "not_met", "not_mentioned", "unverified"

SCHEMA = {
    "title": "jules",
    "type": "object",
    "properties": {
        "criteria": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "verdict": {"type": "string", "enum": [MET, NOT_MET, NOT_MENTIONED]},
                "quotes": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["id", "verdict", "quotes", "reason"]}},
        "overall": {"type": "string"},
    },
    "required": ["criteria", "overall"],
}

SYSTEM = """You are JULES, a careful senior recruiter. You assess ONE candidate's CV against a
fixed list of job criteria, one criterion at a time. The CV may be in English or French; the
candidate's name has been replaced by [CANDIDATE].

For every criterion id, return:
- verdict: "met" only if the CV clearly demonstrates it; "not_met" only if a passage of the
  CV shows the candidate does not meet it (e.g. 3 years when 5 are required) - quote that
  passage; "not_mentioned" if the CV is silent or does not settle it (e.g. a language is
  listed but the required level is not stated). Do not give credit for vague claims.
  A skill, tool or language that the CV lists (e.g. in a Skills or Languages section) counts
  as "met" when the requirement only asks for that skill itself; when the requirement asks
  for a level (fluent, advanced, expert, N years), a plain listing is "not_mentioned".
- quotes: 1 to 3 excerpts copied EXACTLY, character for character, from the CV that support
  your verdict (5 to 30 words each, never shortened with "..." and never translated).
  Use an empty list for "not_mentioned".
- reason: one sentence linking the quote to the requirement.
Then "overall": two or three sentences on the candidate's fit for this job.
Judge only what the CV says. Every quote is checked automatically against the CV text;
a quote that is not found word for word earns no credit.
""" + DATA_NOTE


def _prompt(job: dict, cv_text: str) -> str:
    crit = "\n".join(f"- {c['id']} ({'MUST-HAVE' if c['kind'] == 'must' else 'nice-to-have'}): {c['text']}"
                     for c in all_criteria(job))
    return "\n\n".join([tag("job", f"{job.get('title', '')}\n{job.get('summary', '')}".strip()),
                        tag("criteria", crit), tag("cv", cv_text)])


def assess(job: dict, cand: dict, must_weight: float = 1.0) -> dict:
    """One LLM read of one CV, with every quote verified. Never raises."""
    cv = cand["full_text"]
    crits = all_criteria(job)
    try:
        raw = complete_json(SYSTEM, _prompt(job, cv), SCHEMA)
        error = None
    except Exception as e:                     # one failed call must not stop the other 19
        raw, error = {"criteria": [], "overall": ""}, f"{type(e).__name__}: {e}"
    answers = {a.get("id"): a for a in raw.get("criteria", []) if isinstance(a, dict)}

    rows, points, max_points = [], 0.0, 0.0
    for c in crits:
        weight = must_weight if c["kind"] == "must" else 1.0
        max_points += weight
        a = answers.get(c["id"], {})
        verdict = a.get("verdict") if a.get("verdict") in (MET, NOT_MET, NOT_MENTIONED) else NOT_MENTIONED
        quotes = [q for q in (a.get("quotes") or []) if isinstance(q, str) and q.strip()]
        verified = [q for q in quotes if quote_exists(q, cv)]
        rejected = [q for q in quotes if q not in verified]
        if verdict == MET and not verified:
            verdict = UNVERIFIED            # claimed but no proof found in the CV
        elif verdict == NOT_MET and not verified:
            verdict = NOT_MENTIONED         # a gap needs proof too; otherwise it is silence
        earned = weight if verdict == MET else 0.0
        points += earned
        rows.append({"id": c["id"], "kind": c["kind"], "requirement": c["text"], "verdict": verdict,
                     "points": earned, "cv_quotes": verified, "rejected_quotes": rejected,
                     "reason": str(a.get("reason", "")).strip()})
    return {
        "candidate_id": cand["candidate_id"],
        "score": points,
        "max_score": max_points,
        "criteria": rows,
        "list_missing": [{"id": r["id"], "kind": r["kind"], "requirement": r["requirement"],
                          "verdict": r["verdict"], "cv_quotes": r["cv_quotes"]}
                         for r in rows if r["verdict"] != MET],
        "overall": str(raw.get("overall", "")).strip(),
        "citations": {"checked": sum(len(r["cv_quotes"]) + len(r["rejected_quotes"]) for r in rows),
                      "rejected": sum(len(r["rejected_quotes"]) for r in rows)},
        "error": error,
    }


def run(job: dict, remi_out: dict, load, personal=None, top: int = 10, progress=None) -> dict:
    """`load(cid)` returns Oscar's candidate dict; `personal(cid)` returns the PII record."""
    shortlist = remi_out.get("shortlist", [])
    remi_score = {s["candidate_id"]: s.get("score", 0) for s in shortlist}
    must_weight = float(job.get("jules_must_weight", 1))
    workers = max(1, int(env_number("JULES_WORKERS", 3)))
    cands = [c for c in (load(s["candidate_id"]) for s in shortlist) if c]

    done, lock = [0], threading.Lock()

    def one(cand):
        r = assess(job, cand, must_weight)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], len(cands))
        return r

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, cands))

    results.sort(key=lambda r: (-r["score"], -remi_score.get(r["candidate_id"], 0), r["candidate_id"]))
    for rank, r in enumerate(results, 1):
        r["rank"] = rank
        r["remi_score"] = remi_score.get(r["candidate_id"])
    finalists = results[:top]
    for r in finalists:
        p = personal(r["candidate_id"]) if personal else None
        if p:
            r["personal"] = {"name": p.get("name"), "emails": p.get("emails", []),
                             "phones": p.get("phones", []), "urls": p.get("urls", {})}

    errors = [f"{r['candidate_id']}: {r['error']}" for r in results if r["error"]]
    return {
        "agent": "JULES",
        "job_title": job.get("title"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scoring": f"1 point per criterion met (must-haves x{must_weight:g}); only quotes found "
                   f"word for word in the CV count.",
        "counts": {"read": len(results), "finalists": len(finalists),
                   "quotes_checked": sum(r["citations"]["checked"] for r in results),
                   "quotes_rejected": sum(r["citations"]["rejected"] for r in results)},
        "finalists": finalists,
        "others": [{"candidate_id": r["candidate_id"], "rank": r["rank"], "score": r["score"],
                    "max_score": r["max_score"]} for r in results[top:]],
        "warnings": errors,
    }


__all__ = ["run", "assess"]
