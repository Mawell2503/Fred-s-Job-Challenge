"""Vocabulary standardisation shared by Oscar (CVs), Clara (job criteria) and Remi.

    canonicalize("Built UIs in ReactJS and Node")   ->  "built uis in k_react and node"
    terms_in("Dev front, React.js")                 ->  {"k_front_end_development", "k_react"}
    contains(canon_cv_text, "react js")             ->  True if the CV mentions React in any spelling

Text is lower-cased, accents and most punctuation are removed, then every known
variant from synonyms.yaml is replaced by one token (k_<canonical>). Because the CV and
the criterion go through the same function, "ReactJS" on a CV matches "React" in a job.
"""
from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DEFAULT_SYNONYMS = Path(__file__).with_name("synonyms.yaml")
_TOKEN_CHARS = "a-z0-9+#._"


def prep(text: str) -> str:
    """Lower-case, strip accents and punctuation. Keeps '+', '#' and dots inside words
    (c++, c#, node.js, b.sc)."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"(?<![a-z0-9])\.net\b", " dotnet ", t)
    t = re.sub(r"[^a-z0-9+#.]+", " ", t)
    t = re.sub(r"(?<![a-z0-9])\.|\.(?![a-z0-9])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _slug(name: str) -> str:
    s = prep(name).replace("#", " sharp ").replace("+", " plus ").replace(".", " dot ")
    return "k_" + re.sub(r"[^a-z0-9]+", "_", s).strip("_")


class Vocabulary:
    def __init__(self, synonyms: dict):
        self.display: dict[str, str] = {}          # k_react -> "React"
        variant_to_id: dict[str, str] = {}
        for canonical, variants in (synonyms or {}).items():
            cid = _slug(str(canonical))
            self.display[cid] = str(canonical)
            for v in [canonical, *(variants or [])]:
                pv = prep(str(v))
                if pv:
                    variant_to_id.setdefault(pv, cid)
        self._variant_to_id = variant_to_id
        alts = sorted(variant_to_id, key=len, reverse=True)
        self._rx = re.compile(
            rf"(?<![{_TOKEN_CHARS}])(?:{'|'.join(re.escape(a) for a in alts)})(?![{_TOKEN_CHARS}])"
        ) if alts else None

    def canonicalize(self, text: str) -> str:
        t = prep(text)
        if self._rx is not None:
            t = self._rx.sub(lambda m: f" {self._variant_to_id[m.group(0)]} ", t)
        return re.sub(r"\s+", " ", t).strip()

    def terms_in(self, text: str, already_canonical: bool = False) -> set[str]:
        ct = text if already_canonical else self.canonicalize(text)
        return {tok for tok in ct.split() if tok in self.display}

    def contains(self, canonical_text: str, phrase: str) -> bool:
        """True if `phrase` (any spelling) occurs in an already-canonicalized text."""
        cp = self.canonicalize(phrase)
        return bool(cp) and f" {cp} " in f" {canonical_text} "

    def name(self, cid: str) -> str:
        return self.display.get(cid, cid)


@lru_cache(maxsize=4)
def _load(path: str) -> Vocabulary:
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Vocabulary(data)


def get_vocab(path: str | None = None) -> Vocabulary:
    """The shared vocabulary. Override the file with the CV_SYNONYMS_FILE env variable."""
    return _load(str(path or os.environ.get("CV_SYNONYMS_FILE") or DEFAULT_SYNONYMS))


__all__ = ["Vocabulary", "get_vocab", "prep"]
