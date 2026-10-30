"""LIVE - what the committee page shows while the committee sits. Plain code, no AI.

    live = Live(path, packet)      # writes the candidates' facts once (names, Jules/Remi, Nina's checks,
                                   # Victor's timeline, what is missing)
    live.update(phase=..., speaking=..., positions=...)   # merged in, file rewritten atomically

live.json (read by GET /api/admin/roles/:id/results while the committee runs):
  phase       "round0" | "debate" | "report" | "done"
  round, max_rounds, members, skipped
  speaking    the member being asked right now (None between calls), on = the candidate ids asked about
  contested   ids debated this round; launched = {member: {cid: reason}}
  positions   {member: {cid: {score, verdict, arguments, concerns, question, reply}}}  (current)
  round_start {member: {cid: score}} at the start of this round (the "before" mark on the scores)
  ranking     rank() rows; calls, reprompts, unreliable, stop_reason
  candidates  {cid: {name, jules_rank, jules, remi, checks, timeline, missing, linkedin, has_practical,
                     application ('Applied for this position', 'Open application', ... on whole-pool runs)}}
  confidence  once Gaston is done

Names are for the admin page only: they never go into a prompt.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def candidates_info(packet) -> dict:
    from .packet import application_label as label
    out = {}
    for i, cid in enumerate(packet.ids):
        lens = packet.lens or {}
        nina = lens.get("nina", {}).get(cid, {})
        victor = lens.get("victor", {}).get(cid, {})
        helene = lens.get("helene", {}).get(cid, {})
        sc = packet.scores.get(cid, {})
        out[cid] = {
            "name": packet.names.get(cid, cid),
            "jules_rank": i + 1,
            "jules": sc.get("jules_raw"),
            "remi": sc.get("remi"),
            "checks": nina.get("practical_checks", []),
            "timeline": victor.get("career_timeline", {"jobs": [], "facts": []}),
            "missing": packet.missing.get(cid, []),
            "linkedin": not str(helene.get("linkedin", "")).startswith("No LinkedIn"),
            "has_practical": not str(packet.sources.get(cid, {}).get("practical", "")).startswith("No practical"),
            "application": label(packet.applied.get(cid)) if getattr(packet, "applied", None) else "",
        }
    return out


class Live:
    def __init__(self, path=None, packet=None):
        self.path = Path(path) if path else None
        self.data: dict = {"phase": "round0", "round": 0, "speaking": None, "on": [], "contested": [],
                           "launched": {}, "positions": {}, "round_start": {}, "ranking": [],
                           "candidates": candidates_info(packet) if packet is not None else {}}
        self._write()

    def update(self, **fields):
        for k, v in fields.items():
            self.data[k] = copy.deepcopy(v)
        self._write()

    def _write(self):
        if not self.path:
            return
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)             # the page never reads a half-written file


__all__ = ["Live", "candidates_info"]
