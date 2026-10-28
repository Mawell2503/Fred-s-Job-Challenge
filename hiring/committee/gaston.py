"""GASTON - the clerk. Does not speak, does not vote: writes the final report.

    write_report(packet, state, meta, trace=None) -> report (dict)

Split of the work:
  plain code   header, top 5 + scores, agreement, unresolved disagreements, confidence level,
               what is missing, appendix (positions before / after)          -> the facts
  one AI call  comments, 3 substantiated strengths, 2 areas for improvement, disagreement
               summaries, recommendation wording, interview questions        -> the writing
  checks       every strength's quote is verified against the candidate's CV / practical answers;
               one re-prompt on problems; anything still wrong is filled from the members' own
               verified arguments, and the report says so (filled_by_code)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..citation_checker import quote_exists
from ..llm import DATA_NOTE, LLMError, complete_json, tag
from .members import DISPLAY

_SYSTEM = (Path(__file__).with_name("personas") / "gaston.md").read_text(encoding="utf-8").strip() + "\n\n" + DATA_NOTE

_STRENGTH = {"type": "object", "properties": {"point": {"type": "string"}, "quote": {"type": "string"},
                                              "source": {"type": "string", "enum": ["cv", "practical"]}},
             "required": ["point", "quote", "source"]}
SCHEMA = {
    "title": "gaston_report",
    "type": "object",
    "properties": {
        "top5": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "comment": {"type": "string"},
            "strengths": {"type": "array", "items": _STRENGTH},
            "improvements": {"type": "array", "items": {"type": "string"}},
            "missing": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "comment", "strengths", "improvements", "missing"]}},
        "disagreements": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "summary": {"type": "string"}}, "required": ["id", "summary"]}},
        "recommendation": {"type": "string"},
        "interview_questions": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "questions": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "questions"]}},
    },
    "required": ["top5", "disagreements", "recommendation", "interview_questions"],
}
STOP_WORDS = {"consensus": "the committee reached agreement", "deadlock": "positions stopped moving",
              "max_rounds": "the 3-round limit was reached"}


# ---------------- facts (plain code) ----------------
def agreement(spread: float) -> str:
    return "agreement" if spread <= 2 else "split"


def confidence(state: dict, packet) -> tuple[str, list[str]]:
    """High / Medium / Low for the recommendation, with the reasons (plain rules, see README)."""
    rk = state["ranking"]
    lead, top5 = rk[0], {r["id"] for r in rk[:5]}
    unresolved = {u["id"] for u in state["unresolved"]}
    lows, mediums, highs = [], [], []
    if lead["id"] in unresolved:
        lows.append(f"the committee still disagrees on the leading candidate {lead['id']}")
    if state["unreliable"]:
        lows.append("a member answered badly twice in the debate (" +
                    ", ".join(sorted({DISPLAY[u['member']] for u in state['unreliable']})) + ")")
    missing_members = [m for m in ("helene", "victor", "nina") if m not in state["members"]]
    if missing_members:
        (mediums if "nina" in state["skipped"] and missing_members == ["nina"] else lows).append(
            "not every member could give a position (" + ", ".join(DISPLAY[m] for m in missing_members) + ")")
    if "nina" in state["members"] and "No practical details." in packet.missing.get(lead["id"], []):
        lows.append(f"no practical details for {lead['id']}")
    if unresolved & top5 - {lead["id"]}:
        mediums.append("open disagreement on " + ", ".join(sorted(unresolved & top5 - {lead["id"]})))
    if lead["spread"] > 2:
        mediums.append(f"members' scores on {lead['id']} are {lead['spread']:g} points apart")
    if len(rk) > 1 and lead["score"] - rk[1]["score"] < 0.5:
        mediums.append(f"{lead['id']} and {rk[1]['id']} are almost tied")
    if set(lead["verdicts"].values()) == {"advance"}:
        highs.append(f"every member says 'advance' for {lead['id']}")
    if lows:
        return "Low", lows + mediums
    if mediums:
        return "Medium", mediums + highs
    return "High", highs + ["no open disagreement in the top 5"]


def _verified_args(state, cid, sources):
    """The members' own verified arguments for cid, best scorers first, distinct quotes."""
    out, seen = [], set()
    members = sorted(state["positions"], key=lambda m: -state["positions"][m].get(cid, {}).get("score", 0))
    for m in members:
        for a in state["positions"][m].get(cid, {}).get("arguments", []):
            if a["source"] in ("cv", "practical") and a["quote"] not in seen and quote_exists(a["quote"], sources.get(a["source"], "")):
                seen.add(a["quote"])
                out.append({**a, "by": DISPLAY[m]})
    return out


# ---------------- the AI call ----------------
def build_prompt(packet, state, meta, conf, errors=None) -> str:
    top = state["ranking"][:5]
    ids = [r["id"] for r in top]
    positions = {cid: {DISPLAY[m]: {"score_before": state["before"].get(m, {}).get(cid, {}).get("score"),
                                    "score_after": p[cid]["score"], "verdict": p[cid]["verdict"],
                                    "arguments": p[cid]["arguments"], "concerns": p[cid]["concerns"],
                                    "question": p[cid].get("question", "")}
                       for m, p in state["positions"].items() if cid in p} for cid in ids}
    parts = [
        tag("assignment", f"{meta.get('title', '')} ({meta.get('role_id', '')}), "
                          f"{meta.get('applications', '?')} applications"),
        tag("ranking", json.dumps([{"rank": i + 1, "id": r["id"], "committee_score": r["score"],
                                    "member_scores": {DISPLAY[m]: s for m, s in r["scores"].items()}}
                                   for i, r in enumerate(top)], ensure_ascii=False)),
        tag("positions", json.dumps(positions, indent=1, ensure_ascii=False)),
        tag("unresolved", json.dumps([{"id": u["id"], "scores": {DISPLAY[m]: s for m, s in u["scores"].items()}}
                                      for u in state["unresolved"]], ensure_ascii=False)),
        tag("missing", json.dumps({cid: packet.missing.get(cid, []) for cid in ids}, ensure_ascii=False)),
        tag("confidence", json.dumps({"level": conf[0], "reasons": conf[1]}, ensure_ascii=False)),
        tag("cvs", "\n\n".join(packet.sources[cid]["cv"] for cid in ids)),
        tag("practical", "\n\n".join(f"[{cid}]\n{packet.sources[cid]['practical']}" for cid in ids)),
    ]
    if errors:
        parts.append(tag("validation_errors", "\n".join(f"- {e}" for e in errors)))
        parts.append("Your previous report had the problems listed in <validation_errors>. Write it again in full, fixing them.")
    parts.append("Write the report for the five candidates in <ranking>, in that order.")
    return "\n\n".join(parts)


def check(answer: dict, packet, state) -> tuple[dict, list[str]]:
    ids = [r["id"] for r in state["ranking"][:5]]
    errors, by_id = [], {}
    for item in answer.get("top5") or []:
        cid = str(item.get("id", ""))
        if cid not in ids:
            errors.append(f"'{cid}' is not one of the five candidates in <ranking>.")
            continue
        src = packet.sources.get(cid, {})
        good = []
        for s in item.get("strengths") or []:
            q, where = str(s.get("quote", "")), s.get("source")
            if where in ("cv", "practical") and quote_exists(q, src.get(where, "")):
                good.append({"point": str(s.get("point", "")).strip(), "quote": q.strip(), "source": where})
            else:
                errors.append(f"{cid}: the strength quote \"{q[:80]}\" is not in the candidate's {where} text.")
        if len(good) < 3:
            errors.append(f"{cid}: needs exactly 3 strengths with verified quotes (has {len(good)}).")
        imp = [str(x).strip() for x in item.get("improvements") or [] if str(x).strip()]
        if len(imp) < 2:
            errors.append(f"{cid}: needs exactly 2 areas for improvement.")
        if not str(item.get("comment", "")).strip():
            errors.append(f"{cid}: the comment is empty.")
        by_id[cid] = {"comment": str(item.get("comment", "")).strip(), "strengths": good[:3], "improvements": imp[:2],
                      "missing": [str(x).strip() for x in item.get("missing") or [] if str(x).strip()]}
    for cid in ids:
        if cid not in by_id:
            errors.append(f"{cid} is missing from the report.")
    qs = {str(x.get("id")): [str(q).strip() for q in x.get("questions") or [] if str(q).strip()]
          for x in answer.get("interview_questions") or [] if isinstance(x, dict)}
    for cid in ids:
        if not qs.get(cid):
            errors.append(f"{cid}: give 2 or 3 interview questions.")
    known = {u["id"] for u in state["unresolved"]}
    dis = {str(d.get("id")): str(d.get("summary", "")).strip() for d in answer.get("disagreements") or []
           if isinstance(d, dict) and str(d.get("id")) in known}
    rec = str(answer.get("recommendation", "")).strip()
    if not rec:
        errors.append("The recommendation is empty.")
    return {"by_id": by_id, "questions": qs, "disagreements": dis, "recommendation": rec}, errors


def fill(written, packet, state, conf) -> list[str]:
    """Complete anything still missing from the members' own words. Returns what was filled."""
    filled = []
    for r in state["ranking"][:5]:
        cid = r["id"]
        w = written["by_id"].setdefault(cid, {"comment": "", "strengths": [], "improvements": [], "missing": []})
        if len(w["strengths"]) < 3:
            have = {s["quote"] for s in w["strengths"]}
            pool = [{"point": f"{a['by']}: {a['point']}", "quote": a["quote"], "source": a["source"]}
                    for a in _verified_args(state, cid, packet.sources[cid])]
            # then Jules's verified evidence on the criteria met
            pool += [{"point": f"Meets '{c['requirement']}' (Jules).", "quote": q, "source": "cv"}
                     for c in packet.core[cid]["jules_assessment"]["criteria"] if c["verdict"] == "met"
                     for q in c["quotes"] if quote_exists(q, packet.sources[cid]["cv"])]
            for a in pool:
                if len(w["strengths"]) >= 3:
                    break
                if a["quote"] not in have:
                    w["strengths"].append(a)
                    have.add(a["quote"])
            filled.append(f"{cid} strengths")
        if len(w["improvements"]) < 2:
            pool = [c for m in state["positions"] for c in state["positions"][m].get(cid, {}).get("concerns", [])]
            pool += packet.missing.get(cid, [])
            for c in pool:
                if len(w["improvements"]) >= 2:
                    break
                if c not in w["improvements"]:
                    w["improvements"].append(c)
            filled.append(f"{cid} areas for improvement")
        if not w["comment"]:
            w["comment"] = f"Committee score {r['score']:g}/10 ({agreement(r['spread'])})."
            filled.append(f"{cid} comment")
        if not written["questions"].get(cid):
            written["questions"][cid] = [q for q in (state["positions"][m].get(cid, {}).get("question")
                                                     for m in state["positions"]) if q][:3]
            filled.append(f"{cid} interview questions")
    for u in state["unresolved"]:
        if u["id"] not in written["disagreements"]:
            written["disagreements"][u["id"]] = "Scores at the end of the debate: " + ", ".join(
                f"{DISPLAY[m]} {s:g}" for m, s in u["scores"].items()) + "."
            filled.append(f"{u['id']} disagreement summary")
    if not written["recommendation"]:
        lead = state["ranking"][0]
        written["recommendation"] = (f"Interview {lead['id']} first (committee score {lead['score']:g}/10). "
                                     f"Confidence: {conf[0]} ({'; '.join(conf[1])}).")
        filled.append("recommendation")
    return filled


# ---------------- the report ----------------
def write_report(packet, state, meta: dict, trace=None) -> dict:
    conf = confidence(state, packet)
    written, errors, used_ai = None, None, True
    for attempt in (1, 2):
        prompt = build_prompt(packet, state, meta, conf, errors)
        try:
            answer = complete_json(_SYSTEM, prompt, SCHEMA, temperature=0.3)
            state["calls"] += 1
        except LLMError as e:
            state["calls"] += 1
            state["warnings"].append(f"Gaston could not be reached: {e}")
            if trace:
                trace.say(f"Gaston could not be reached ({str(e)[:120]}); the report is assembled from the "
                          f"members' own words.", None, "gaston", "error")
            used_ai = False
            break
        written, errs = check(answer, packet, state)
        if trace:
            trace.log("answer", member="gaston", attempt=attempt, prompt=prompt, answer=answer, errors=errs)
        if not errs:
            break
        if attempt == 1:
            state["reprompts"] += 1
            errors = errs
            if trace:
                trace.say(f"Orchestrator re-prompts Gaston: {len(errs)} problem(s), e.g. {errs[0][:140]}",
                          None, "orchestrator", "reprompt")
    if written is None:
        written = {"by_id": {}, "questions": {}, "disagreements": {}, "recommendation": ""}
    filled = fill(written, packet, state, conf)
    if trace and filled:
        trace.say("Completed from the members' own verified arguments: " + ", ".join(filled) + ".", None, "gaston", "warning")

    rk = state["ranking"]
    name = lambda cid: packet.names.get(cid, cid)        # noqa: E731
    top = rk[:5]
    report = {
        "header": {
            "assignment": meta.get("title", ""), "role_id": meta.get("role_id", ""),
            "date": datetime.now(timezone.utc).date().isoformat(),
            "applications": meta.get("applications"), "readable": meta.get("readable"),
            "passed_must_haves": meta.get("passed_gate"), "read_by_jules": meta.get("read_by_jules"),
            "debated": len(state["candidates"]),
            "members": [DISPLAY[m] for m in state["members"]],
            "rounds": len(state["rounds"]) - 1, "stop_reason": STOP_WORDS.get(state["stop_reason"], state["stop_reason"]),
            "ai_calls": state["calls"], "reprompts": state["reprompts"],
        },
        "top5": [{"rank": i + 1, "id": r["id"], "name": name(r["id"]), "score": r["score"],
                  "scores": {DISPLAY[m]: s for m, s in r["scores"].items()}, "agreement": agreement(r["spread"]),
                  "comment": written["by_id"][r["id"]]["comment"]} for i, r in enumerate(top)],
        "candidates": [{"id": r["id"], "name": name(r["id"]),
                        "strengths": written["by_id"][r["id"]]["strengths"][:3],
                        "improvements": written["by_id"][r["id"]]["improvements"][:2],
                        "missing": list(dict.fromkeys(packet.missing.get(r["id"], []) + written["by_id"][r["id"]]["missing"]))}
                       for r in top],
        "disagreements": [{"id": u["id"], "name": name(u["id"]) if u["id"] in {r["id"] for r in top} else u["id"],
                           "scores": {DISPLAY[m]: s for m, s in u["scores"].items()},
                           "summary": written["disagreements"].get(u["id"], "")} for u in state["unresolved"]],
        "recommendation": {"text": written["recommendation"], "confidence": conf[0], "reasons": conf[1]},
        "interview_questions": [{"id": r["id"], "name": name(r["id"]), "questions": written["questions"].get(r["id"], [])[:3]}
                                for r in top],
        "appendix": [{"id": cid, "name": name(cid) if cid in {r["id"] for r in top} else cid,
                      "final_rank": next(i + 1 for i, r in enumerate(rk) if r["id"] == cid),
                      "rows": [{"member": DISPLAY[m],
                                "before": state["before"][m][cid]["score"], "verdict_before": state["before"][m][cid]["verdict"],
                                "after": state["after"][m][cid]["score"], "verdict_after": state["after"][m][cid]["verdict"],
                                "because": next((c["because"] for rr in reversed(state["rounds"]) for c in rr["changes"]
                                                 if c["member"] == m and c["id"] == cid), "")}
                               for m in state["members"] if cid in state["before"].get(m, {}) and cid in state["after"].get(m, {})]}
                     for cid in state["candidates"]],
        "notes": {"skipped": {DISPLAY[m]: why for m, why in state["skipped"].items()},
                  "unreliable": [{"member": DISPLAY[u["member"]], "round": u["round"]} for u in state["unreliable"]],
                  "filled_by_code": filled, "written_by_ai": used_ai, "warnings": state["warnings"]},
    }
    return report


# ---------------- LLM_BACKEND=fake ----------------
def fake_report(prompt: str) -> dict:
    """Gaston without AI: assembles the report from the members' own words."""
    from ..fake_llm import json_section
    ranking = json_section(prompt, "ranking") or []
    positions = json_section(prompt, "positions") or {}
    unresolved = json_section(prompt, "unresolved") or []
    missing = json_section(prompt, "missing") or {}
    conf = json_section(prompt, "confidence") or {"level": "Medium", "reasons": []}
    from ..fake_llm import section
    practical = {}
    for block in (section(prompt, "practical") or "").split("\n\n"):
        head, _, body = block.partition("\n")
        practical[head.strip("[]")] = [ln for ln in body.splitlines() if ln.strip()]
    top5, questions = [], []
    for r in ranking:
        cid, pos = r["id"], positions.get(r["id"], {})
        args, seen = [], set()
        for who, p in sorted(pos.items(), key=lambda kv: -kv[1]["score_after"]):
            for a in p["arguments"]:
                if a["quote"] not in seen and a["source"] in ("cv", "practical"):
                    seen.add(a["quote"])
                    args.append({"point": f"{who}: {a['point']}", "quote": a["quote"], "source": a["source"]})
        for ln in practical.get(cid, []):                 # then the practical answers, verbatim
            if len(args) < 3 and ln not in seen and not ln.startswith("No practical"):
                seen.add(ln)
                args.append({"point": "Practical answer.", "quote": ln, "source": "practical"})
        concerns = [c for p in pos.values() for c in p["concerns"]] + missing.get(cid, [])
        scores = ", ".join(f"{w} {p['score_after']:g}" for w, p in pos.items())
        top5.append({"id": cid, "comment": f"Ranked {r['rank']} with {r['committee_score']:g}/10 ({scores}).",
                     "strengths": args[:3], "improvements": list(dict.fromkeys(concerns))[:2], "missing": []})
        questions.append({"id": cid, "questions": [p["question"] for p in pos.values() if p.get("question")][:3]})
    lead = ranking[0]["id"] if ranking else "-"
    return {"top5": top5,
            "disagreements": [{"id": u["id"], "summary": "Still apart: " + ", ".join(f"{k} {v:g}" for k, v in u["scores"].items()) + "."}
                              for u in unresolved],
            "recommendation": f"Interview {lead} first. Confidence {conf['level']}: {'; '.join(conf['reasons'])}.",
            "interview_questions": questions}


__all__ = ["write_report", "confidence", "check", "fill", "fake_report", "SCHEMA"]
