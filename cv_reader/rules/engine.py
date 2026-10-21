"""Stage 5: Apply rules - classify every block / line as keep or drop."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from ..models import Block, DroppedItem
from ..pii import mask_text

DEFAULT_RULES_PATH = Path(__file__).with_name("default_rules.yaml")

KNOWN_SECTION_TYPES = {
    "header", "unsorted", "summary", "experience", "education", "skills", "languages",
    "certifications", "training", "projects", "awards", "publications", "volunteering",
    "interests", "references", "personal", "declaration", "unknown",
}
_ALLOWED_KEYS = {
    "version", "default_section_action", "sections", "drop_headings", "drop_line_patterns",
    "keep_line_patterns", "drop_blocks_containing", "drop_empty_blocks",
    "drop_repeated_headers_footers", "drop_decorative_lines", "drop_docx_footers",
    "min_block_chars", "replace_default_lists",
}
_BOOL_KEYS = ("replace_default_lists", "drop_empty_blocks", "drop_repeated_headers_footers", "drop_decorative_lines",
              "drop_docx_footers")
_LIST_KEYS = ("drop_headings", "drop_line_patterns",
              "keep_line_patterns", "drop_blocks_containing")
_LOG_TEXT_MAX = 300


class RulesError(ValueError):
    """Raised when a rules dict is malformed. Carries ALL problems, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("Invalid rules:\n  - " + "\n  - ".join(problems))
        self.problems = problems


def _read_file(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if str(path).lower().endswith(".json"):
        return json.loads(text)
    import yaml
    return yaml.safe_load(text) or {}


def load_default_rules() -> dict:
    return _read_file(DEFAULT_RULES_PATH)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def validate_rules(rules: dict) -> None:
    problems: list[str] = []
    if not isinstance(rules, dict):
        raise RulesError(["rules must be a mapping/object"])
    for k in rules:
        if k not in _ALLOWED_KEYS:
            problems.append(
                f"unknown key '{k}' (allowed: {sorted(_ALLOWED_KEYS)})")
    act = rules.get("default_section_action", "keep")
    if act not in ("keep", "drop"):
        problems.append(
            f"default_section_action must be 'keep' or 'drop', got {act!r}")
    sections = rules.get("sections", {})
    if not isinstance(sections, dict):
        problems.append(
            "'sections' must be a mapping of section type -> keep|drop")
    else:
        for typ, val in sections.items():
            if typ not in KNOWN_SECTION_TYPES:
                problems.append(f"sections.{typ}: unknown section type "
                                f"(known: {sorted(KNOWN_SECTION_TYPES)})")
            if val not in ("keep", "drop"):
                problems.append(
                    f"sections.{typ}: must be 'keep' or 'drop', got {val!r}")
    for key in _LIST_KEYS:
        val = rules.get(key, [])
        if not isinstance(val, list):
            problems.append(f"{key} must be a list of regex strings")
            continue
        for pat in val:
            try:
                re.compile(pat)
            except (re.error, TypeError) as e:
                problems.append(f"{key}: bad regex {pat!r} ({e})")
    for key in _BOOL_KEYS:
        if key in rules and not isinstance(rules[key], bool):
            problems.append(f"{key} must be true or false")
    mbc = rules.get("min_block_chars", 0)
    if not isinstance(mbc, int) or isinstance(mbc, bool) or mbc < 0:
        problems.append("min_block_chars must be a non-negative integer")
    if problems:
        raise RulesError(problems)


def load_rules(source=None) -> dict:
    """Build the effective rules: defaults, overridden by `source`.

    source may be None (defaults only), a dict (e.g. JSON sent by the UI; may be
    partial) or a path to a .yaml/.yml/.json file. Raises RulesError if invalid.
    """
    base = load_default_rules()
    if source is None:
        override = {}
    elif isinstance(source, (str, Path)):
        override = _read_file(Path(source))
    elif isinstance(source, dict):
        override = source
    else:
        raise RulesError(
            [f"unsupported rules source type: {type(source).__name__}"])
    validate_rules(override)           # catch typos in what the user supplied
    merged = deep_merge(base, override)
    if not override.get("replace_default_lists"):
        for k in _LIST_KEYS:
            if k in override:
                merged[k] = list(base.get(k, [])) + \
                    [x for x in override[k] if x not in base.get(k, [])]
    validate_rules(merged)
    return merged


def _clip(text: str) -> str:
    text = mask_text(text)
    return text if len(text) <= _LOG_TEXT_MAX else text[:_LOG_TEXT_MAX] + " ..."


class RuleEngine:
    def __init__(self, rules: dict):
        validate_rules(rules)
        self.rules = rules
        self._drop_headings = [re.compile(p, re.I)
                               for p in rules.get("drop_headings", [])]
        self._drop_lines = [(p, re.compile(p, re.I))
                            for p in rules.get("drop_line_patterns", [])]
        self._keep_lines = [re.compile(p, re.I)
                            for p in rules.get("keep_line_patterns", [])]
        self._block_contains = [(p, re.compile(p, re.I))
                                for p in rules.get("drop_blocks_containing", [])]
        self._sections = rules.get("sections", {})
        self._default_action = rules.get("default_section_action", "keep")

    # block level
    def _block_rule(self, block: Block):
        action = self._sections.get(block.type, self._default_action)
        if action == "drop":
            return f"section:{block.type}"
        for rx in self._drop_headings:
            if block.heading and rx.search(block.heading):
                return f"drop_headings:{rx.pattern}"
        for pat, rx in self._block_contains:
            if any(rx.search(ln.text) for ln in block.lines):
                return f"drop_blocks_containing:{pat}"
        return None

    # line level
    def _line_rule(self, ln):
        text = ln.text
        if any(rx.search(text) for rx in self._keep_lines):
            return None
        r = self.rules
        if r.get("drop_docx_footers", True) and ln.edge == "footer":
            return "docx_footer"
        if r.get("drop_repeated_headers_footers", True) and ln.repeated:
            return "repeated_header_footer"
        if r.get("drop_decorative_lines", True) and not re.search(r"\w", text):
            return "decorative_line"
        for pat, rx in self._drop_lines:
            if rx.search(text):
                return f"line_pattern:{pat}"
        return None

    def apply(self, blocks: list[Block], candidate_id: str) -> tuple[list[Block], list[DroppedItem]]:
        kept: list[Block] = []
        dropped: list[DroppedItem] = []
        min_chars = self.rules.get("min_block_chars", 0)

        for block in blocks:
            rule = self._block_rule(block)
            if rule:
                dropped.append(DroppedItem(candidate_id, "block", block.type, rule,
                                           _clip(self._block_label(block) + " / ".join(
                                               ln.text for ln in block.lines))))
                continue

            new_entries = []
            for entry in block.entries:
                keep_lines = []
                for ln in entry:
                    lr = self._line_rule(ln)
                    if lr:
                        dropped.append(DroppedItem(
                            candidate_id, "line", block.type, lr, _clip(ln.text)))
                    else:
                        keep_lines.append(ln)
                if keep_lines:
                    new_entries.append(keep_lines)
            nb = Block(block.type, block.heading, new_entries, block.position)

            n_chars = sum(len(ln.text.strip()) for ln in nb.lines)
            if n_chars == 0:
                if self.rules.get("drop_empty_blocks", True):
                    dropped.append(DroppedItem(candidate_id, "block", block.type, "empty_block",
                                               _clip(self._block_label(block) + "(no text left)")))
                    continue
            elif min_chars and n_chars < min_chars:
                dropped.append(DroppedItem(candidate_id, "block", block.type, "min_block_chars",
                                           _clip(self._block_label(block) + " / ".join(ln.text for ln in nb.lines))))
                continue
            kept.append(nb)
        return kept, dropped

    @staticmethod
    def _block_label(block: Block) -> str:
        return f"[{block.heading}] " if block.heading else ""
