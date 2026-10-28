"""Gaston's report as Markdown (report.md). The same report dict also feeds the admin page.

Sections, in the order required by the brief:
  header · top 5 · per candidate · unresolved disagreements · recommendation + confidence ·
  interview questions · appendix (positions before and after the debate)
"""
from __future__ import annotations


def _cell(v) -> str:
    return str(v if v is not None else "–").replace("|", "\\|").replace("\n", " ")


def _table(head, rows) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def to_markdown(rep: dict) -> str:
    h = rep["header"]
    L = [f"# Hiring committee report: {h['assignment']}", ""]
    L += _table(["", ""], [
        ["Assignment", f"{h['assignment']} ({h['role_id']})"],
        ["Date", h["date"]],
        ["Applications received", h["applications"]],
        ["Readable CVs", h["readable"]],
        ["Passed the must-haves (Remi)", h["passed_must_haves"]],
        ["Read in full (Jules)", h["read_by_jules"]],
        ["Debated by the committee", h["debated"]],
        ["Committee", ", ".join(h["members"])],
        ["Debate", f"{h['rounds']} round(s); {h['stop_reason']}"],
    ])

    L += ["", "## Top 5", ""]
    L += _table(["#", "Candidate", "Score /10", "Members", "Agreement", "Comment"],
                [[t["rank"], f"{t['name']} ({t['id']})", f"{t['score']:g}",
                  ", ".join(f"{m} {s:g}" for m, s in t["scores"].items()), t["agreement"], t["comment"]]
                 for t in rep["top5"]])

    L += ["", "## Candidates", ""]
    for c in rep["candidates"]:
        L += [f"### {c['name']} ({c['id']})", "", "**Strengths**", ""]
        L += [f"{i}. {s['point']}  \n   > \"{s['quote']}\" ({'CV' if s['source'] == 'cv' else 'practical answers'})"
              for i, s in enumerate(c["strengths"], 1)]
        L += ["", "**Areas for improvement**", ""] + [f"- {x}" for x in c["improvements"]]
        L += ["", "**What's missing**", ""] + ([f"- {x}" for x in c["missing"]] or ["- Nothing noted."]) + [""]

    L += ["## Unresolved disagreements", ""]
    if rep["disagreements"]:
        for d in rep["disagreements"]:
            L += [f"- **{d['name']}** ({', '.join(f'{m} {s:g}' for m, s in d['scores'].items())}): {d['summary']}"]
    else:
        L += ["None: the committee settled every candidate near the top."]

    r = rep["recommendation"]
    L += ["", "## Recommendation", "", f"**Confidence: {r['confidence']}** ({'; '.join(r['reasons'])})", "", r["text"]]

    L += ["", "## Questions to ask in the interview", ""]
    for q in rep["interview_questions"]:
        L += [f"**{q['name']} ({q['id']})**", ""] + [f"- {x}" for x in q["questions"]] + [""]

    L += ["## Appendix: positions before and after the debate", ""]
    rows = []
    for a in rep["appendix"]:
        for x in a["rows"]:
            moved = x["after"] - x["before"]
            rows.append([f"{a['name']} (#{a['final_rank']})", x["member"], f"{x['before']:g} ({x['verdict_before']})",
                         f"{x['after']:g} ({x['verdict_after']})", f"{moved:+g}" if moved else "=", x["because"][:160]])
    L += _table(["Candidate", "Member", "Before", "After", "Moved", "Why"], rows)

    n = rep["notes"]
    L += ["", "---", "", f"*{h['ai_calls']} AI call(s), {h['reprompts']} re-prompt(s). "
          "Scores, ranking, confidence and disagreements are computed by plain code; Gaston only writes. "
          "Every quoted strength was checked word for word against the candidate's CV or practical answers.*"]
    if n["skipped"]:
        L += ["", "*Not launched: " + "; ".join(f"{m}: {w}" for m, w in n["skipped"].items()) + "*"]
    if n["filled_by_code"]:
        L += ["", "*Completed from the members' own verified arguments: " + ", ".join(n["filled_by_code"]) + ".*"]
    if n["unreliable"]:
        L += ["", "*Answered badly twice (invalid parts dropped): " +
              ", ".join(f"{u['member']} (round {u['round']})" for u in n["unreliable"]) + ".*"]
    return "\n".join(L) + "\n"


__all__ = ["to_markdown"]
