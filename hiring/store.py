"""Find Oscar's output files for a candidate.

Supports both layouts:
  website : <runs>/<C0001>/cv_json/C0001.json      (one folder per applicant)
  batch   : <runs>/cv_json/C0001.json              (python -m cv_reader folder -o runs)
"""
from __future__ import annotations

import json
from pathlib import Path


def _find(runs: Path, cid: str, folder: str, ext: str) -> Path | None:
    for p in (runs / cid / folder / f"{cid}{ext}", runs / folder / f"{cid}{ext}"):
        if p.exists():
            return p
    return None


def _read_json(p: Path | None):
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def load_candidate(runs, cid: str) -> dict | None:
    """Everything Oscar produced for one candidate, or None if there is no usable output."""
    runs = Path(runs)
    record = _read_json(_find(runs, cid, "cv_json", ".json"))
    text_p = _find(runs, cid, "cv_text", ".txt")
    if record is None or text_p is None:
        return None
    full_p = _find(runs, cid, "cv_full", ".txt")
    return {
        "candidate_id": cid,
        "record": record,
        "text": text_p.read_text(encoding="utf-8"),
        "full_text": (full_p or text_p).read_text(encoding="utf-8"),
        "embedding": _read_json(_find(runs, cid, "embeddings", ".json")),
    }


def list_candidates(runs) -> list[str]:
    """All candidate ids that have a structured record under `runs`."""
    runs = Path(runs)
    ids = {p.stem for p in runs.glob("*/cv_json/*.json")} | {p.stem for p in runs.glob("cv_json/*.json")}
    return sorted(ids)


def load_personal(runs, cid: str) -> dict | None:
    """The candidate's personal details from Oscar's separate PII file (admin eyes only)."""
    runs = Path(runs)
    for p in (runs / cid / "pii_store.json", runs / "pii_store.json"):
        if p.exists():
            for rec in json.loads(p.read_text(encoding="utf-8")):
                if rec.get("candidate_id") == cid:
                    return rec
    return None
