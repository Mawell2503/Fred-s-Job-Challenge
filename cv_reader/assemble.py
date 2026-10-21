"""Stage 6: Assemble - join the kept blocks into one text block.

Format (light section tags, as agreed):

    [CANDIDATE_ID: C0001]

    [HEADER]
    Senior Software Engineer

    [EXPERIENCE]
    Software Engineer  Acme Ltd  2019 - 2022
    - Built X

    Junior Developer  Beta  2017 - 2019
    - Built Y

    [UNKNOWN: Volunteer Work Abroad]
    ...
"""
from __future__ import annotations

from .models import Block


def block_label(block: Block) -> str:
    if block.type == "unknown":
        return f"[UNKNOWN: {block.heading}]"
    return f"[{block.type.upper()}]"


def assemble(blocks: list[Block], candidate_id: str) -> str:
    parts = [f"[CANDIDATE_ID: {candidate_id}]"]
    for block in blocks:
        entries = ["\n".join(ln.text.rstrip() for ln in e)
                   for e in block.entries if e]
        if not entries:
            continue
        parts.append(block_label(block) + "\n" + "\n\n".join(entries))
    return "\n\n".join(parts) + "\n"
