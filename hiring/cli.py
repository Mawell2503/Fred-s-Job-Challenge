"""Command line for the agents (the website calls these; you can too).

  python -m hiring clara --input clara_in.json --out job.json
        clara_in.json = {"title": ..., "ad_text": ..., "must_text": ..., "nice_text": ...}
  python -m hiring remi  --job job.json --runs data/runs [--ids C0001,C0002 | --ids-file ids.txt] --out remi.json
                         [--top 20] [--llm-gate] [--progress progress.json]
  python -m hiring jules --job job.json --runs data/runs --remi remi.json --out jules.json
                         [--top 10] [--progress progress.json]
  python -m hiring committee --job job.json --runs data/runs --jules jules.json --input committee_in.json
                         --out committee.json [--report report.md] [--trace trace.jsonl] [--feed feed.json]
                         [--progress progress.json]
        committee_in.json = {"role": {"id", "title"}, "logistics": {...}, "practical": {cid: {...}},
                             "linkedin": {cid: true}, "names": {cid: name}, "funnel": {"applications", "readable", "scope", ...},
                             "application": {cid: {"kind": "this" | "open" | "other", "roles": [titles]}},
                             "tie_break_direct": false}

Exit codes: 0 ok, 2 bad input, 3 the LLM could not be used.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .criteria import clean_criteria
from .llm import LLMError
from .store import list_candidates, load_candidate, load_personal


def _read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _write(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)                       # readers never see a half-written file


def _progress(path, stage):
    if not path:
        return None
    return lambda done, total: _write(path, {"stage": stage, "done": done, "total": total})


def _committee(a) -> int:
    from datetime import datetime, timezone

    from .committee import orchestrator
    from .committee.gaston import write_report
    from .committee.packet import build
    from .committee.report import to_markdown

    job = clean_criteria(_read(a.job))
    cin = _read(a.input)
    packet = build(job, _read(a.jules), lambda cid: load_candidate(a.runs, cid), cin)
    trace = orchestrator.Trace(a.trace, a.feed)

    def progress(done, total, label):
        if a.progress:
            _write(a.progress, {"stage": "committee", "done": done, "total": total + 1, "label": label})

    state = orchestrator.run(packet, trace=trace, progress=progress, max_rounds=a.rounds)
    if a.progress:
        _write(a.progress, {"stage": "committee", "done": 1, "total": 1, "label": "Gaston writes the report"})
    trace.say("Gaston writes the report.", None, "gaston", "launch")
    funnel = cin.get("funnel") or {}
    role = cin.get("role") or {}
    meta = {"title": role.get("title") or job.get("title"), "role_id": role.get("id", ""), **funnel}
    report = write_report(packet, state, meta, trace=trace)
    trace.say(f"Report ready. Recommendation confidence: {report['recommendation']['confidence']}.", None, "gaston", "result")
    state["feed"] = trace.feed
    out = {"agent": "COMMITTEE", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **{k: state[k] for k in ("top5", "ranking", "stop_reason", "calls", "reprompts", "members", "skipped",
                                    "unresolved", "rounds", "before", "after", "positions", "history", "unreliable",
                                    "warnings", "feed", "seconds")},
           "report": report}
    _write(a.out, out)
    if a.report:
        p = Path(a.report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(to_markdown(report), encoding="utf-8")
    print(f"Committee: top 5 {', '.join(state['top5'])}; {len(state['rounds']) - 1} debate round(s) "
          f"({state['stop_reason']}); {state['calls']} AI call(s), {state['reprompts']} re-prompt(s); "
          f"confidence {report['recommendation']['confidence']}.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hiring", description="Clara, Remi and Jules.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clara", help="propose criteria from a job ad / admin lists")
    c.add_argument("--input", required=True)
    c.add_argument("--out", required=True)

    r = sub.add_parser("remi", help="must-have gate + top-20 ranking")
    r.add_argument("--job", required=True)
    r.add_argument("--runs", required=True)
    r.add_argument("--ids", help="comma-separated candidate ids (default: all under --runs)")
    r.add_argument("--ids-file", help="file with one candidate id per line (large pools: no command-line limit)")
    r.add_argument("--out", required=True)
    r.add_argument("--top", type=int, default=20)
    r.add_argument("--llm-gate", action="store_true")
    r.add_argument("--progress")

    j = sub.add_parser("jules", help="full LLM read of the shortlist + top-10")
    j.add_argument("--job", required=True)
    j.add_argument("--runs", required=True)
    j.add_argument("--remi", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--top", type=int, default=10)
    j.add_argument("--progress")

    k = sub.add_parser("committee", help="Helene, Victor, Nina debate Jules's top 10; Gaston writes the report")
    k.add_argument("--job", required=True)
    k.add_argument("--runs", required=True)
    k.add_argument("--jules", required=True)
    k.add_argument("--input", required=True)
    k.add_argument("--out", required=True)
    k.add_argument("--report")
    k.add_argument("--trace")
    k.add_argument("--feed")
    k.add_argument("--progress")
    k.add_argument("--rounds", type=int, default=3)

    a = ap.parse_args(argv)
    try:
        if a.cmd == "clara":
            from .clara import propose
            inp = _read(a.input)
            job = propose(inp.get("title", ""), inp.get("ad_text", ""),
                          inp.get("must_text", ""), inp.get("nice_text", ""))
            _write(a.out, job)
            print(f"Clara: {len(job['must_haves'])} must-haves, {len(job['nice_to_haves'])} nice-to-haves.")

        elif a.cmd == "remi":
            from .remi import run
            job = clean_criteria(_read(a.job))
            if a.ids_file:
                ids = [x.strip() for x in Path(a.ids_file).read_text(encoding="utf-8").splitlines() if x.strip()]
            elif a.ids:
                ids = [x.strip() for x in a.ids.split(",") if x.strip()]
            else:
                ids = list_candidates(a.runs)
            cands = [x for x in (load_candidate(a.runs, i) for i in ids) if x]
            out = run(job, cands, top=a.top, use_llm_gate=a.llm_gate, progress=_progress(a.progress, "remi"))
            out["counts"]["without_oscar_output"] = len(ids) - len(cands)
            _write(a.out, out)
            k = out["counts"]
            print(f"Remi: {k['candidates']} read, {k['eliminated']} eliminated, {k['shortlisted']} shortlisted.")

        elif a.cmd == "committee":
            return _committee(a)

        else:
            from .jules import run
            job = clean_criteria(_read(a.job))
            out = run(job, _read(a.remi), lambda cid: load_candidate(a.runs, cid),
                      lambda cid: load_personal(a.runs, cid), top=a.top,
                      progress=_progress(a.progress, "jules"))
            _write(a.out, out)
            print(f"Jules: {out['counts']['read']} read, {out['counts']['finalists']} finalists, "
                  f"{out['counts']['quotes_rejected']} quote(s) rejected by the citation checker.")
            if out["counts"]["read"] and out["counts"]["failed"] == out["counts"]["read"]:
                print("Every LLM call failed:\n  " + "\n  ".join(out["warnings"][:3]), file=sys.stderr)
                return 3
    except LLMError as e:
        print(f"LLM ERROR: {e}", file=sys.stderr)
        return 3
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"INPUT ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
