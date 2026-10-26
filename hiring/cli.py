"""Command line for the agents (the website calls these; you can too).

  python -m hiring clara --input clara_in.json --out job.json
        clara_in.json = {"title": ..., "ad_text": ..., "must_text": ..., "nice_text": ...}
  python -m hiring remi  --job job.json --runs data/runs [--ids C0001,C0002] --out remi.json
                         [--top 20] [--llm-gate] [--progress progress.json]
  python -m hiring jules --job job.json --runs data/runs --remi remi.json --out jules.json
                         [--top 5] [--progress progress.json]

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
    r.add_argument("--out", required=True)
    r.add_argument("--top", type=int, default=20)
    r.add_argument("--llm-gate", action="store_true")
    r.add_argument("--progress")

    j = sub.add_parser("jules", help="full LLM read of the shortlist + top-5")
    j.add_argument("--job", required=True)
    j.add_argument("--runs", required=True)
    j.add_argument("--remi", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--top", type=int, default=5)
    j.add_argument("--progress")

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
            ids = [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else list_candidates(a.runs)
            cands = [x for x in (load_candidate(a.runs, i) for i in ids) if x]
            out = run(job, cands, top=a.top, use_llm_gate=a.llm_gate, progress=_progress(a.progress, "remi"))
            out["counts"]["without_oscar_output"] = len(ids) - len(cands)
            _write(a.out, out)
            k = out["counts"]
            print(f"Remi: {k['candidates']} read, {k['eliminated']} eliminated, {k['shortlisted']} shortlisted.")

        else:
            from .jules import run
            job = clean_criteria(_read(a.job))
            out = run(job, _read(a.remi), lambda cid: load_candidate(a.runs, cid),
                      lambda cid: load_personal(a.runs, cid), top=a.top,
                      progress=_progress(a.progress, "jules"))
            _write(a.out, out)
            print(f"Jules: {out['counts']['read']} read, {out['counts']['finalists']} finalists, "
                  f"{out['counts']['quotes_rejected']} quote(s) rejected by the citation checker.")
            if out["counts"]["read"] and len(out["warnings"]) == out["counts"]["read"]:
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
