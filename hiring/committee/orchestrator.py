"""THE ORCHESTRATOR - runs the committee. Plain code: it never asks the AI what to do.

    run(packet, trace=None, progress=None, max_rounds=3) -> state (dict)

1. Choose who sits: Hélène and Victor always; Nina only if there is something practical to judge
   (role requirements or the candidates' practical answers).
2. Round 0 - positions BEFORE: Hélène, then Victor, then Nina, each ALONE, on all candidates.
3. Rounds 1..3 - debate, only on CONTESTED candidates, mostly with the members in conflict:
     contested = scores 3+ apart, or "advance" next to "reject", or close to the top-5 cut-off
                 (ranks 4-7) with scores 2+ apart
     launched  = for each contested candidate, the highest and the lowest scorer; and Victor
                 whenever Hélène gave 3 or less to someone another member gave 6 or more (his rescue);
                 a member scoring in between is called once, the first round the candidate is contested
4. Stop when nothing is contested (consensus), when a round changes no score by 1 point or more
   (deadlock), or after round 3.
5. Every answer is validated (validate.py). Nonsense -> ONE re-prompt with the exact errors; if it
   is still wrong, the invalid parts are dropped and the member is marked unreliable for that round.
6. Final top 5 (plain formula): committee score = average of the members' final scores;
   ties -> Jules's score, then Remi's.
7. Everything is traced: who was launched and why, every prompt, every raw answer, every check.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone

from ..llm import LLMError
from . import members as M
from .validate import validate_position

CONTESTED_SPREAD = 3
CUTLINE_SPREAD = 2
CHANGE_THRESHOLD = 1.0


class Trace:
    """Full log (trace.jsonl) + short human feed (feed.json, read live by the admin page)."""

    def __init__(self, path=None, feed_path=None):
        self.path, self.feed_path = path, feed_path
        self.feed: list[dict] = []
        self.events = 0
        if path:
            open(path, "w", encoding="utf-8").close()

    def log(self, kind: str, **data):
        self.events += 1
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "kind": kind, **data}, ensure_ascii=False) + "\n")

    def say(self, text: str, round_no=None, member=None, kind="info"):
        self.feed.append({"round": round_no, "member": member, "kind": kind, "text": text})
        self.log("feed", round=round_no, member=member, text=text)
        if self.feed_path:
            from pathlib import Path
            p = Path(self.feed_path)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(self.feed, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)


def _name(m):
    return M.DISPLAY[m]


def contested(positions: dict, ids) -> list[str]:
    """Candidates the committee does not agree on (see module doc)."""
    ranking = rank(positions, ids, {})
    pos_of = {r["id"]: i + 1 for i, r in enumerate(ranking)}
    out = []
    for cid in ids:
        sc = [positions[m][cid]["score"] for m in positions if cid in positions[m]]
        vs = {positions[m][cid]["verdict"] for m in positions if cid in positions[m]}
        if len(sc) < 2:
            continue
        spread = max(sc) - min(sc)
        if spread >= CONTESTED_SPREAD or {"advance", "reject"} <= vs or \
                (4 <= pos_of.get(cid, 99) <= 7 and spread >= CUTLINE_SPREAD):
            out.append(cid)
    return out


def assignments(positions: dict, cids, heard=None) -> dict:
    """member -> [candidate ids] to debate this round, with the reason for each launch.

    `heard` = the (member, cid) pairs already called in an earlier debate round. A member whose score
    sits between the highest and the lowest is called once, the first round the candidate is contested,
    so the middle view is argued at least once; after that, only the two ends are called."""
    heard = heard if heard is not None else set()
    plan: dict[str, dict[str, str]] = {}
    for cid in cids:
        sc = {m: positions[m][cid]["score"] for m in positions if cid in positions[m]}
        hi, lo = max(sc.values()), min(sc.values())
        first_time = not any(c == cid for _, c in heard)
        for m, s in sc.items():
            if s == hi:
                plan.setdefault(m, {})[cid] = f"highest score on {cid} ({s:g})"
            elif s == lo:
                plan.setdefault(m, {})[cid] = f"lowest score on {cid} ({s:g})"
            elif first_time:
                plan.setdefault(m, {})[cid] = f"in the middle on {cid} ({s:g}): heard once"
        h = sc.get("helene")
        if "victor" in sc and h is not None and h <= 3 and any(s >= 6 for m, s in sc.items() if m != "helene"):
            plan.setdefault("victor", {})[cid] = f"rescue: Hélène gave {cid} {h:g}"
            plan.setdefault("helene", {}).setdefault(cid, f"Victor challenges her {h:g} on {cid}")
    return plan


def rank(positions: dict, ids, scores: dict, tie_break_direct: bool = False) -> list[dict]:
    """Average of the members' scores, best first. Ties: Jules's score, then Remi's; with `tie_break_direct`
    (a role setting, whole-pool runs only) people who applied for this role come first among equal averages."""
    rows = []
    for cid in ids:
        sc = {m: positions[m][cid]["score"] for m in positions if cid in positions[m]}
        mean = round(sum(sc.values()) / len(sc), 2) if sc else None
        rows.append({"id": cid, "score": mean, "scores": sc,
                     "verdicts": {m: positions[m][cid]["verdict"] for m in positions if cid in positions[m]},
                     "spread": round(max(sc.values()) - min(sc.values()), 1) if len(sc) > 1 else 0.0,
                     "jules": scores.get(cid, {}).get("jules", 0.0), "remi": scores.get(cid, {}).get("remi", 0.0),
                     "direct": scores.get(cid, {}).get("direct", 0)})
    rows.sort(key=lambda r: (-(r["score"] if r["score"] is not None else -1),
                             -(r["direct"] if tie_break_direct else 0), -r["jules"], -r["remi"], r["id"]))
    return rows


def _debate_view(positions, me, cids):
    """What `me` sees of the colleagues' current positions on `cids`."""
    view = {}
    for cid in cids:
        view[cid] = {_name(m): {"score": p[cid]["score"], "verdict": p[cid]["verdict"],
                                "arguments": p[cid]["arguments"], "concerns": p[cid]["concerns"],
                                **({"reply": p[cid]["reply"]} if p[cid].get("reply") else {})}
                     for m, p in positions.items() if m != me and cid in p}
    return view


def run(packet, trace: Trace | None = None, progress=None, max_rounds: int = 3) -> dict:
    trace = trace or Trace()
    ids = list(packet.ids)
    started = time.time()
    state = {"members": [], "skipped": {}, "rounds": [], "calls": 0, "reprompts": 0,
             "unreliable": [], "warnings": [], "stop_reason": None}
    if not ids:
        raise ValueError("The committee has no candidates (Jules produced no finalists).")

    # ---- 1. who sits
    sitting = ["helene", "victor"]
    if packet.has_practical_data:
        sitting.append("nina")
    else:
        state["skipped"]["nina"] = "No practical requirements on the role and no practical answers from candidates."
        trace.say("Nina is not launched: nothing practical to judge (no role requirements, no candidate answers).",
                  0, "orchestrator", "decision")
    trace.log("decision", what="members", sitting=sitting, skipped=state["skipped"])
    planned = len(sitting) * (1 + max_rounds)
    done = 0

    def tick(label):
        nonlocal done
        done += 1
        if progress:
            progress(min(done, planned), planned, label)

    def call(m, cids, round_no, debate=None, previous=None):
        """One validated answer from member m, with at most one re-prompt. Returns clean or None."""
        errors = None
        for attempt in (1, 2):
            try:
                answer, prompt = M.ask(m, packet, cids, round_no, debate=debate, previous=previous, errors=errors)
                state["calls"] += 1
            except LLMError as e:
                state["calls"] += 1
                trace.log("llm_error", member=m, round=round_no, attempt=attempt, error=str(e))
                trace.say(f"{_name(m)} could not be reached ({str(e)[:120]}).", round_no, m, "error")
                state["warnings"].append(f"Round {round_no}: {_name(m)} failed: {e}")
                return None
            clean, errs = validate_position(answer, cids, m, packet, round_no)
            trace.log("answer", member=m, round=round_no, attempt=attempt, candidates=cids, prompt=prompt,
                      answer=answer, errors=errs)
            if not errs:
                return clean
            if attempt == 1:
                state["reprompts"] += 1
                errors = errs
                trace.say(f"Orchestrator re-prompts {_name(m)}: {len(errs)} problem(s), e.g. {errs[0][:140]}",
                          round_no, "orchestrator", "reprompt")
            else:
                state["unreliable"].append({"member": m, "round": round_no, "errors": errs})
                trace.say(f"{_name(m)} answered badly twice; invalid parts dropped ({len(errs)} problem(s)).",
                          round_no, "orchestrator", "warning")
                return clean
        return None

    # ---- 2. round 0: positions before
    positions: dict[str, dict] = {}
    heard: set = set()                         # (member, cid) already called in a debate round
    history: dict[str, dict[str, list]] = {}   # member -> cid -> what they said, round by round

    def remember(m, cid, round_no, pos, reason, kept_arguments=False):
        """One entry of the member's record on a candidate: only validated content (verified quotes)."""
        history.setdefault(m, {}).setdefault(cid, []).append({
            "round": round_no, "reason": reason, "score": pos["score"], "verdict": pos["verdict"],
            "arguments": copy.deepcopy(pos["arguments"]), "concerns": list(pos.get("concerns") or []),
            "question": pos.get("question", ""), "reply": pos.get("reply", ""),
            **({"kept_previous_arguments": True} if kept_arguments else {})})
    top5s = {}
    r0 = {"round": 0, "launched": [], "changes": []}
    for m in sitting:
        trace.say(f"{_name(m)} reads the {len(ids)} candidates on their own.", 0, m, "launch")
        tick(f"Round 0 · {_name(m)}")
        clean = call(m, ids, 0)
        r0["launched"].append({"member": m, "candidates": ids, "reason": "round 0: everyone gives a position"})
        if clean is None or not clean["positions"]:
            continue
        positions[m] = clean["positions"]
        for cid, p in clean["positions"].items():
            remember(m, cid, 0, p, "round 0: everyone gives a position")
        top5s[m] = clean["top5"]
        best = max(clean["positions"].items(), key=lambda kv: kv[1]["score"])
        trace.say(f"{_name(m)} scored {len(clean['positions'])} candidates; favourite {best[0]} "
                  f"({best[1]['score']:g}/10).", 0, m, "position")
    if not positions:
        raise LLMError("No committee member could give a position (every call failed).")
    # a candidate nobody could score (every answer about it invalid twice) has no average: it cannot be
    # ranked, so it is left out of the debate and the ranking, and the report says so
    unscored = [c for c in ids if not any(c in p for p in positions.values())]
    if unscored:
        ids = [c for c in ids if c not in unscored]
        state["warnings"].append("No member gave a valid score for " + ", ".join(unscored) +
                                 ": left out of the ranking. Check these CVs by hand.")
        trace.say(f"No valid score for {', '.join(unscored)}: left out of the ranking.", 0, "orchestrator", "warning")
        if not ids:
            raise LLMError("No committee member gave a valid score for any candidate.")
    state["members"] = list(positions)
    before = copy.deepcopy(positions)
    state["rounds"].append(r0)

    # ---- 3. debate rounds
    state["stop_reason"] = "max_rounds"
    for r in range(1, max_rounds + 1):
        hot = contested(positions, ids)
        if not hot:
            state["stop_reason"] = "consensus"
            trace.say("No candidate is contested any more: the committee agrees. Debate closed.", r, "orchestrator", "stop")
            break
        plan = assignments(positions, hot, heard)
        trace.say(f"Round {r}: {len(hot)} contested candidate(s): {', '.join(hot)}.", r, "orchestrator", "decision")
        rr = {"round": r, "contested": hot, "launched": [], "changes": []}
        changed = False
        for m in M.MEMBERS:
            if m not in plan or m not in positions:
                continue
            cids = [c for c in ids if c in plan[m] and c in positions[m]]
            if not cids:
                continue
            reasons = {c: plan[m][c] for c in cids}
            rr["launched"].append({"member": m, "candidates": cids, "reason": reasons})
            heard.update((m, c) for c in cids)
            trace.log("decision", what="launch", round=r, member=m, reasons=reasons)
            trace.say(f"{_name(m)} is asked to defend or revise: " + "; ".join(reasons.values()) + ".", r, m, "launch")
            tick(f"Round {r} · {_name(m)}")
            clean = call(m, cids, r, debate=_debate_view(positions, m, cids),
                         previous={c: positions[m][c] for c in cids})
            if clean is None:
                continue
            for cid, newp in clean["positions"].items():
                old = positions[m][cid]
                kept = not newp["arguments"]
                if kept:                             # dropped as invalid: keep the old reasoning
                    newp["arguments"] = old["arguments"]
                remember(m, cid, r, newp, reasons.get(cid, ""), kept)
                delta = newp["score"] - old["score"]
                if abs(delta) >= CHANGE_THRESHOLD:
                    changed = True
                why = newp.get("reply") or (newp["arguments"][0]["point"] if newp["arguments"] else "")
                rr["changes"].append({"member": m, "id": cid, "from": old["score"], "to": newp["score"],
                                      "verdict_from": old["verdict"], "verdict_to": newp["verdict"], "because": why})
                verb = "holds" if abs(delta) < CHANGE_THRESHOLD else ("moves up" if delta > 0 else "moves down")
                trace.say(f"{_name(m)} on {cid}: {old['score']:g} → {newp['score']:g} ({verb}). {why[:220]}",
                          r, m, "change" if verb != "holds" else "hold")
                positions[m][cid] = newp
        state["rounds"].append(rr)
        if not changed:
            state["stop_reason"] = "deadlock"
            trace.say("Nobody moved by a full point this round: the remaining disagreements stay open.",
                      r, "orchestrator", "stop")
            break
    else:
        trace.say(f"Round limit reached ({max_rounds}). The debate is closed.", max_rounds, "orchestrator", "stop")

    # ---- 4. final ranking + what is still open
    ranking = rank(positions, ids, packet.scores, packet.tie_break_direct)
    top5 = [r["id"] for r in ranking[:5]]
    still = contested(positions, ids)
    unresolved = [r for r in ranking if r["id"] in still and ranking.index(r) < 7]
    state.update({
        "candidates": ids,
        "before": {m: {c: {"score": p["score"], "verdict": p["verdict"]} for c, p in pos.items()}
                   for m, pos in before.items()},
        "after": {m: {c: {"score": p["score"], "verdict": p["verdict"]} for c, p in pos.items()}
                  for m, pos in positions.items()},
        "positions": positions,
        "history": history,
        "round0_top5": top5s,
        "ranking": ranking,
        "top5": top5,
        "unresolved": [{"id": r["id"], "scores": r["scores"], "verdicts": r["verdicts"], "spread": r["spread"]}
                       for r in unresolved],
        "seconds": round(time.time() - started, 1),
    })
    trace.say("Final top 5 (average of the members' scores): " +
              ", ".join(f"{r['id']} {r['score']:g}" for r in ranking[:5]) + ".", None, "orchestrator", "result")
    state["feed"] = trace.feed
    trace.log("result", top5=top5, stop_reason=state["stop_reason"], calls=state["calls"], reprompts=state["reprompts"])
    return state


__all__ = ["run", "Trace", "contested", "assignments", "rank"]
