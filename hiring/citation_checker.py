"""citation_checker - verifies that a quoted excerpt really exists in the CV. Plain code, no AI.

Differences that don't change the words are ignored: upper/lower case, spacing and line
breaks, curly vs straight quotes, and the kind of dash. Anything else must match exactly.
"""
import re
import unicodedata

_SAME = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "•": "-"})


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").translate(_SAME).lower()
    t = re.sub(r"^\s*-\s+", " ", t, flags=re.M)          # bullet markers are not part of a quote
    return re.sub(r"\s+", " ", t).strip(" .,;:\"'")


def quote_exists(quote: str, cv_text: str) -> bool:
    q = normalise(quote)
    return len(q) >= 3 and q in normalise(cv_text)
