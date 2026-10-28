"""Committee answers for LLM_BACKEND=fake: plain rules, NO AI, same answer every time.

Each fake member reads exactly the prompt a real model would get and applies its persona's bias
with simple arithmetic, so the debate, the re-prompts and the report can be exercised offline:

    Hélène  share of Jules's criteria met, with proof
    Victor  same base, + change of field / promotions, - "same role for years" / short stays
    Nina    starts at 8, -4 per practical blocker, -1.5 per risk
    debate  each moves part of the way towards the colleagues (Hélène 40 %, Nina 35 %, Victor 30 %),
            but Nina never goes above 4 with a blocker and Victor never drops a career changer below 6
"""
from __future__ import annotations

import re

from ..fake_llm import json_section, quote_of, section

_PULL = {"helene": 0.4, "victor": 0.3, "nina": 0.35}
_LINE_FOR_CHECK = [("salary", "Salary expectation"), ("start date", "Earliest start date"),
                   ("location", "Willing to relocate"), ("travel", "Willing to travel"),
                   ("right to work", "Right to work"), ("way of working", "Preferred way of working")]


def _persona(system: str) -> str:
    for key, name in (("HÉLÈNE", "helene"), ("VICTOR", "victor"), ("NINA", "nina")):
        if f"You are {key}" in system:
            return name
    raise ValueError("unknown committee member")


def _verdict(score: int) -> str:
    return "advance" if score >= 6 else "reject" if score <= 3 else "maybe"


def _clamp(x) -> int:
    return int(max(0, min(10, round(x))))


def _ratio(c: dict) -> float:
    crit = c["jules_assessment"]["criteria"]
    return sum(x["verdict"] == "met" for x in crit) / len(crit) if crit else 0.0


def _section_lines(cv: str, name: str) -> list[str]:
    m = re.search(rf"^\[{name}\]\n(.*?)(?=^\[[A-Z]|\Z)", cv, re.S | re.M)
    return [ln.strip() for ln in (m.group(1) if m else "").splitlines() if ln.strip()]


def _helene(c):
    crit = c["jules_assessment"]["criteria"]
    args = []
    for x in crit:
        if x["verdict"] == "met" and x["quotes"] and len(args) < 2 and x["quotes"][0] not in [a["quote"] for a in args]:
            args.append({"point": f"Proof for '{x['requirement']}'.", "quote": x["quotes"][0], "source": "cv"})
    if not args:
        first = next((ln for ln in c["cv"].splitlines() if ln.strip() and not ln.startswith("[")), "")
        args.append({"point": "Nothing here proves the skills the job needs.", "quote": quote_of(first), "source": "cv"})
    missing = [x["requirement"] for x in crit if x["verdict"] != "met"]
    return _clamp(1 + 8 * _ratio(c)), args, [f"No proof of: {m}." for m in missing[:2]]


def _victor(c):
    facts = c["career_timeline"]["facts"]
    s = 2 + 5 * _ratio(c)
    bonus = {"Changed field": 2.5, "moved up": 1.5, "Same role": -2, "Many short stays": -1.5,
             "Gap of": -0.5, "moved down": -1}
    for key, b in bonus.items():
        if any(key in f for f in facts):
            s += b
    lines = [ln for ln in _section_lines(c["cv"], "EXPERIENCE") if re.search(r"(19|20)\d{2}", ln)]
    lines = lines or [ln for ln in c["cv"].splitlines() if ln.strip() and not ln.startswith("[")][:1]
    path = next((f for f in facts if any(k in f for k in bonus)), facts[0] if facts else "A path with little to read.")
    args = [{"point": path, "quote": quote_of(lines[0]), "source": "cv"}]
    if len(lines) > 1:
        args.append({"point": "Where the path started.", "quote": quote_of(lines[-1]), "source": "cv"})
    concerns = [f for f in facts if any(k in f for k in ("Same role", "short stays", "Gap of"))][:2]
    return _clamp(s), args, concerns


def _nina(c):
    checks = c["practical_checks"]
    s = 8 - 4 * sum(x["status"] == "blocker" for x in checks) - 1.5 * sum(x["status"] == "risk" for x in checks)
    text = c["practical_answers"]
    lines = text.splitlines()

    def line_for(check):
        if check.startswith("language: "):
            lang = check.split(": ", 1)[1].lower()
            return next((ln for ln in lines if ln.lower().startswith(f"language: {lang}")), None) or \
                next((ln for ln in lines if ln.startswith("Language:")), None)
        for key, prefix in _LINE_FOR_CHECK:
            if check.startswith(key):
                return next((ln for ln in lines if ln.startswith(prefix)), None)
        return None

    order = sorted(checks, key=lambda x: {"blocker": 0, "risk": 1, "ok": 2, "unknown": 3}[x["status"]])
    args = []
    for x in order:
        ln = line_for(x["check"])
        if ln and ln not in [a["quote"] for a in args] and len(args) < 2:
            tone = {"blocker": "Blocker.", "risk": "Risk.", "ok": "Fine.", "unknown": "Unknown."}[x["status"]]
            args.append({"point": f"{tone} {x['detail']}", "quote": ln, "source": "practical"})
    if not args:
        args.append({"point": "Nothing practical to go on.", "quote": lines[0], "source": "practical"})
    concerns = [x["detail"] for x in checks if x["status"] in ("blocker", "risk")][:3]
    return _clamp(s), args, concerns


def position(system: str, prompt: str) -> dict:
    who = _persona(system)
    round_no = int(section(prompt, "round") or 0)
    cands = json_section(prompt, "candidates") or []
    judge = {"helene": _helene, "victor": _victor, "nina": _nina}[who]
    committee = json_section(prompt, "committee") or {}
    previous = json_section(prompt, "your_previous_position") or {}
    out = []
    for c in cands:
        score, args, concerns = judge(c)
        reply = ""
        if round_no > 0 and c["id"] in previous:
            mine = previous[c["id"]]["score"]
            others = [v["score"] for v in committee.get(c["id"], {}).values()]
            target = sum(others) / len(others) if others else mine
            score = _clamp(mine + (target - mine) * _PULL[who])
            if who == "nina" and any(x["status"] == "blocker" for x in c["practical_checks"]):
                score = min(score, 4)
            if who == "victor" and mine >= 6 and any("Changed field" in f for f in c["career_timeline"]["facts"]):
                score = max(score, 6)
            names = " and ".join(committee.get(c["id"], {}))
            reply = (f"{names}: you moved me from {mine:g} to {score}." if abs(score - mine) >= 1
                     else f"{names}: I hold my {mine:g}. {args[0]['point']}")
        out.append({"id": c["id"], "score": score, "verdict": _verdict(score), "arguments": args,
                    "concerns": concerns, "question": f"Walk me through: {args[0]['quote'][:80]}", "reply": reply})
    top5 = [x["id"] for x in sorted(out, key=lambda x: (-x["score"], x["id"]))][:5] if round_no == 0 else []
    return {"candidates": out, "top5": top5}


def gaston(prompt: str) -> dict:
    from .gaston import fake_report
    return fake_report(prompt)


__all__ = ["position", "gaston"]
