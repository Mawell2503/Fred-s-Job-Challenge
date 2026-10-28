"""Checks every committee answer. Plain code, no AI.

validate_position(answer, expected_ids, persona, packet, round_no) -> (clean, errors)

  clean   {cid: {score, verdict, arguments (verified only), concerns, question, reply}} + "top5"
  errors  readable problems, sent back to the member when the orchestrator re-prompts it

An answer is "nonsense" (errors not empty) when: a candidate is missing or unknown, a score is out
of range, the verdict contradicts the score, a quote is not found in its source, the member left its
lane (Nina without a practical argument; Hélène / Victor without a CV argument), or the round-0 top5
is not five known, distinct ids.
"""
from __future__ import annotations

from ..citation_checker import quote_exists

VERDICTS = ("advance", "maybe", "reject")


def _band_ok(score: float, verdict: str) -> bool:
    return (verdict == "advance" and score >= 6) or (verdict == "maybe" and 3 < score < 6) or \
        (verdict == "reject" and score <= 3) or (verdict == "maybe" and score in (3, 6))


def validate_position(answer: dict, expected_ids, persona: str, packet, round_no: int):
    errors: list[str] = []
    expected = list(expected_ids)
    got = {}
    for item in answer.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id", "")).strip()
        if cid not in expected:
            errors.append(f"'{cid}' is not one of the candidates you were asked about ({', '.join(expected)}).")
            continue
        if cid in got:
            errors.append(f"{cid} appears twice.")
            continue
        got[cid] = item
    for cid in expected:
        if cid not in got:
            errors.append(f"{cid} is missing: give a position for every candidate you were asked about.")

    clean = {}
    for cid, item in got.items():
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            errors.append(f"{cid}: the score must be a number from 0 to 10.")
            continue
        if not 0 <= score <= 10:
            errors.append(f"{cid}: score {score:g} is outside 0-10.")
            continue
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            errors.append(f"{cid}: verdict must be advance, maybe or reject.")
            continue
        if not _band_ok(score, verdict):
            errors.append(f"{cid}: verdict '{verdict}' does not match score {score:g} "
                          f"(advance = 6+, maybe = 4-5, reject = 3 or less).")
        sources = packet.sources.get(cid, {})
        kept = []
        for a in item.get("arguments") or []:
            if not isinstance(a, dict):
                continue
            src = a.get("source")
            quote = str(a.get("quote", ""))
            text = sources.get(src, "")
            if src not in ("cv", "practical", "linkedin"):
                errors.append(f"{cid}: argument source must be cv, practical or linkedin.")
            elif not text:
                errors.append(f"{cid}: there is no '{src}' material for this candidate; quote the cv instead.")
            elif not quote_exists(quote, text):
                errors.append(f"{cid}: the quote \"{quote[:80]}\" is not in the candidate's {src} text "
                              f"(copy it exactly).")
            else:
                kept.append({"point": str(a.get("point", "")).strip(), "quote": quote.strip(), "source": src})
        if not kept:
            errors.append(f"{cid}: no argument with a verified quote.")
        elif persona == "nina" and sources.get("practical") and not any(a["source"] == "practical" for a in kept):
            errors.append(f"{cid}: at least one argument must quote the practical answers (source 'practical').")
        elif persona in ("helene", "victor") and not any(a["source"] in ("cv", "linkedin") for a in kept):
            errors.append(f"{cid}: at least one argument must quote the CV (source 'cv').")
        clean[cid] = {
            "score": round(score, 1), "verdict": verdict, "arguments": kept,
            "concerns": [str(c).strip() for c in (item.get("concerns") or []) if str(c).strip()][:3],
            "question": str(item.get("question", "")).strip(),
            "reply": str(item.get("reply", "")).strip(),
        }

    top5 = [str(x) for x in answer.get("top5") or []]
    if round_no == 0:
        want = min(5, len(expected))
        if len(set(top5)) != len(top5) or len(top5) != want or any(x not in expected for x in top5):
            errors.append(f"top5 must list exactly {want} different ids from the candidates, best first.")
    return {"positions": clean, "top5": [x for x in top5 if x in expected]}, errors


__all__ = ["validate_position"]
