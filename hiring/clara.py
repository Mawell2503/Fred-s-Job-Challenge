"""CLARA - understands the job and proposes the criteria.

Input : the job title (Admin_INPUT#1), and either a free-text job ad, the admin's own
        must-have list (Admin_INPUT#2) and nice-to-have list (Admin_INPUT#3), or both.
Output: a job record (see criteria.py) for the admin to REVIEW AND EDIT before saving.
        Must-haves eliminate candidates, so a person should always confirm them.
"""
from __future__ import annotations

from .criteria import SECTION_TYPES, TYPES, clean_criteria
from .llm import complete_json

_CRITERION = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "type": {"type": "string", "enum": list(TYPES)},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "min_years": {"type": ["number", "null"]},
    },
    "required": ["text", "type", "keywords"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "must_haves": {"type": "array", "items": _CRITERION},
        "nice_to_haves": {"type": "array", "items": _CRITERION},
        "suggested_drop_sections": {"type": "array", "items": {"type": "string", "enum": list(SECTION_TYPES)}},
    },
    "required": ["summary", "must_haves", "nice_to_haves", "suggested_drop_sections"],
}

SYSTEM = """You are CLARA, a recruitment analyst. You turn a job description into a precise,
checkable list of hiring criteria. Candidates' CVs may be in English or French.

Rules:
- must_haves: ONLY requirements the employer states as essential (required, must, minimum,
  mandatory, indispensable). A candidate missing one is eliminated, so be conservative:
  usually 2 to 6 items. If the admin gave an explicit must-have list, use exactly those items
  (split or reword them only to make each one checkable) and do not add others.
- nice_to_haves: preferred / a plus / desirable items, or the admin's nice-to-have list.
- One idea per criterion. "text" is a short plain sentence a recruiter would recognise.
- "type": years (an amount of experience; set min_years), language, degree, certification,
  skill (a tool, technology or competence), or other.
- "keywords": 2 to 6 words or short phrases a CV would contain if it meets the criterion,
  including common spellings, abbreviations and the French equivalent
  (e.g. ["accounting", "accountant", "comptabilite", "comptable"]).
  For "years", the keywords describe the field of that experience.
- suggested_drop_sections: CV sections that are irrelevant for judging this job
  (usually interests, references, declaration; keep projects/publications when relevant).
- summary: 2-4 sentences on what the role really needs.
Do not invent requirements that are not stated or clearly implied."""


def propose(title: str, ad_text: str = "", must_text: str = "", nice_text: str = "") -> dict:
    if not (ad_text.strip() or must_text.strip() or nice_text.strip()):
        raise ValueError("Give Clara a job description or at least a must-have list.")
    parts = [f"PROFESSION BEING HIRED (Admin input #1): {title.strip()}"]
    if ad_text.strip():
        parts.append(f"JOB DESCRIPTION:\n<<<\n{ad_text.strip()[:20000]}\n>>>")
    if must_text.strip():
        parts.append(f"ADMIN'S MUST-HAVE LIST (Admin input #2):\n{must_text.strip()[:5000]}")
    if nice_text.strip():
        parts.append(f"ADMIN'S NICE-TO-HAVE LIST (Admin input #3):\n{nice_text.strip()[:5000]}")
    parts.append("Produce the criteria now.")
    raw = complete_json(SYSTEM, "\n\n".join(parts), SCHEMA)
    raw["title"] = title
    return clean_criteria(raw)


__all__ = ["propose"]
