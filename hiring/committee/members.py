"""The committee members are NOT three AIs: they are three instructions for the same call.

    ask(persona, packet, ids, round_no, debate=None, errors=None) -> the member's answer (dict)

The material stays the same (the job + the candidates), only the system prompt changes
(personas/<name>.md + the shared rules below). The orchestrator decides who is asked about what;
validate.py checks every answer. Nothing else here.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import DATA_NOTE, complete_json, tag

MEMBERS = ("helene", "victor", "nina")
DISPLAY = {"helene": "Hélène", "victor": "Victor", "nina": "Nina", "gaston": "Gaston"}
TEMPERATURE = 0.3
_DIR = Path(__file__).with_name("personas")

POSITION_SCHEMA = {
    "title": "committee_position",
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "score": {"type": "number", "minimum": 0, "maximum": 10},
                "verdict": {"type": "string", "enum": ["advance", "maybe", "reject"]},
                "arguments": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"point": {"type": "string"},
                                   "quote": {"type": "string"},
                                   "source": {"type": "string", "enum": ["cv", "practical", "linkedin"]}},
                    "required": ["point", "quote", "source"]}},
                "concerns": {"type": "array", "items": {"type": "string"}},
                "question": {"type": "string"},
                "reply": {"type": "string"},
            },
            "required": ["id", "score", "verdict", "arguments", "concerns"]}},
        "top5": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["candidates", "top5"],
}

RULES = """
HOW THE COMMITTEE WORKS (same rules for every member)
- Candidates are anonymous: they are known only by an id such as C0007. Never guess who they are.
- For each candidate you are asked about, give:
  score   0 to 10 (0-3 = no, 4-5 = unsure, 6-10 = yes, from YOUR point of view)
  verdict "advance" (score 6 or more), "maybe" (4 or 5) or "reject" (3 or less)
  arguments 1 to 3, each = a one-sentence point + a quote that supports it + its source:
          source "cv" = the candidate's "cv" text, "practical" = their "practical_answers",
          "linkedin" = their "linkedin" text. The quote is copied EXACTLY, character for character,
          5 to 30 words, never shortened with "..." and never translated. Every quote is checked
          automatically against that source; an argument whose quote is not found is thrown away.
  concerns 0 to 3 short sentences (what worries you)
  question one question you would ask this candidate in the interview
- top5: in round 0, the ids of your five preferred candidates, best first (fewer if fewer exist).
- Stay faithful to the material: never invent a fact, a number, a date or a skill.
- Stay in your role. Judge with your own priorities; the committee is balanced by the others.

DEBATE ROUNDS (when <committee> is present)
- <committee> shows your colleagues' current positions on the candidates you are asked about,
  and <your_previous_position> shows yours.
- Answer only for those candidates. In "reply", talk to your colleagues by name: say what convinced
  you, or why you hold your position. Change your score only if a colleague's argument (with its
  quote) actually convinces you; otherwise defend your score with evidence. top5 may be empty.
"""


def system_prompt(persona: str) -> str:
    return (_DIR / f"{persona}.md").read_text(encoding="utf-8").strip() + "\n" + RULES + "\n" + DATA_NOTE


def build_prompt(persona: str, packet, ids, round_no: int, debate: dict | None = None,
                 previous: dict | None = None, errors: list | None = None) -> str:
    parts = [tag("round", str(round_no)), tag("job", packet.job_text())]
    if persona == "nina":
        parts.append(tag("requirements", packet.requirements_text()))
    parts.append(tag("candidates", packet.material(persona, ids)))
    if debate:
        parts.append(tag("committee", json.dumps(debate, indent=1, ensure_ascii=False)))
        parts.append(tag("your_previous_position", json.dumps(previous or {}, indent=1, ensure_ascii=False)))
    if errors:
        parts.append(tag("validation_errors", "\n".join(f"- {e}" for e in errors)))
        parts.append("Your previous answer had the problems listed in <validation_errors>. "
                     "Answer again in full, fixing every one of them.")
    if round_no == 0:
        parts.append(f"Round 0. Assess every candidate in <candidates> ({len(ids)} of them) on your own, "
                     "then give your top5.")
    else:
        parts.append(f"Debate round {round_no}. Answer for these candidates only: {', '.join(ids)}.")
    return "\n\n".join(parts)


def ask(persona: str, packet, ids, round_no: int, debate=None, previous=None, errors=None) -> tuple[dict, str]:
    """One call. Returns (answer, prompt) so the orchestrator can keep the trace."""
    prompt = build_prompt(persona, packet, ids, round_no, debate, previous, errors)
    return complete_json(system_prompt(persona), prompt, POSITION_SCHEMA, temperature=TEMPERATURE), prompt


__all__ = ["ask", "build_prompt", "system_prompt", "MEMBERS", "DISPLAY", "POSITION_SCHEMA"]
