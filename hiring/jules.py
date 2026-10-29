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

import re
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


_ID = re.compile(r"\s*([MN]\d+)\b", re.I)


def _answers(raw: dict) -> dict:
    """criterion id -> answer. Tolerates ids like "m1" or "M1 (MUST-HAVE)"."""
    out = {}
    for a in raw.get("criteria") or []:
        m = _ID.match(str(a.get("id", ""))) if isinstance(a, dict) else None
        if m and m.group(1).upper() not in out:
            out[m.group(1).upper()] = a
    return out


def assess(job: dict, cand: dict, must_weight: float = 1.0) -> dict:
    """One LLM read of one CV, with every quote verified. Never raises.

    An answer that skips criteria gets ONE re-prompt naming them; whatever is still missing
    counts as "not mentioned" and is reported in `incomplete` (never silently)."""
    cv = cand["full_text"]
    crits = all_criteria(job)
    prompt = _prompt(job, cv)
    try:
        raw = complete_json(SYSTEM, prompt, SCHEMA)
        error = None
    except Exception as e:                     # one failed call must not stop the other 19
        raw, error = {"criteria": [], "overall": ""}, f"{type(e).__name__}: {e}"
    answers = _answers(raw)
    missing = [c["id"] for c in crits if c["id"] not in answers]
    reprompted = False
    if missing and not error:
        reprompted = True
        try:
            again = complete_json(SYSTEM, prompt + "\n\n" + tag(
                "validation_errors", f"Your previous answer did not cover: {', '.join(missing)}.") +
                "\nAnswer again for EVERY criterion id listed in <criteria>, using exactly those ids.", SCHEMA)
            fill = _answers(again)
            answers.update({k: v for k, v in fill.items() if k in missing})
            if not str(raw.get("overall", "")).strip():
                raw["overall"] = again.get("overall", "")
        except Exception:
            pass                               # keep the first answer; what is missing is reported below
        missing = [c["id"] for c in crits if c["id"] not in answers]

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
        "incomplete": missing if not error else [],
        "reprompted": reprompted,
    }


def _suspicious(r: dict, gate: list) -> bool:
    """Nothing met, though Remi's plain-code check already found must-haves on this CV."""
    return not r["error"] and r["score"] == 0 and any(g.get("status") == "PASS" for g in gate or [])


def run(job: dict, remi_out: dict, load, personal=None, top: int = 10, progress=None) -> dict:
    """`load(cid)` returns Oscar's candidate dict; `personal(cid)` returns the PII record."""
    shortlist = remi_out.get("shortlist", [])
    remi_score = {s["candidate_id"]: s.get("score", 0) for s in shortlist}
    remi_gate = {s["candidate_id"]: s.get("gate", []) for s in shortlist}
    must_weight = float(job.get("jules_must_weight", 1))
    workers = max(1, int(env_number("JULES_WORKERS", 3)))
    cands = [c for c in (load(s["candidate_id"]) for s in shortlist) if c]

    done, lock = [0], threading.Lock()

    def one(cand):
        r = assess(job, cand, must_weight)
        if _suspicious(r, remi_gate.get(cand["candidate_id"])):
            # a zero next to must-haves Remi already found is more likely a bad answer than a bad CV:
            # read it once more and keep the better result
            second = assess(job, cand, must_weight)
            r = max((r, second), key=lambda x: x["score"])
            r["reread"] = True
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

    warnings = [f"{r['candidate_id']}: {r['error']}" for r in results if r["error"]]
    warnings += [f"{r['candidate_id']}: Jules's answer skipped {', '.join(r['incomplete'])} even after a re-prompt; "
                 f"counted as not mentioned." for r in results if r["incomplete"]]
    warnings += [f"{r['candidate_id']}: Jules found none of the criteria, though Remi had found must-haves on this CV "
                 f"(read twice). Check this CV by hand." for r in results if _suspicious(r, remi_gate.get(r["candidate_id"]))]
    return {
        "agent": "JULES",
        "job_title": job.get("title"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scoring": f"1 point per criterion met (must-haves x{must_weight:g}); only quotes found "
                   f"word for word in the CV count.",
        "counts": {"read": len(results), "finalists": len(finalists),
                   "failed": sum(1 for r in results if r["error"]),
                   "reprompted": sum(1 for r in results if r["reprompted"]),
                   "reread": sum(1 for r in results if r.get("reread")),
                   "quotes_checked": sum(r["citations"]["checked"] for r in results),
                   "quotes_rejected": sum(r["citations"]["rejected"] for r in results)},
        "finalists": finalists,
        # every CV Jules read but did not keep, with its verdicts, so a surprising score can be checked
        "others": [{"candidate_id": r["candidate_id"], "rank": r["rank"], "score": r["score"],
                    "max_score": r["max_score"], "overall": r["overall"], "error": r["error"],
                    "criteria": [{"id": c["id"], "verdict": c["verdict"], "cv_quotes": c["cv_quotes"]}
                                 for c in r["criteria"]]} for r in results[top:]],
        "warnings": warnings,
    }


__all__ = ["run", "assess"]
