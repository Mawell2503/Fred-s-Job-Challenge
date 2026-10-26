"""Profile embeddings: a numeric "fingerprint" of what a CV (or a job) is about.

Two backends:
  * "sentence-transformers" - a real multilingual meaning model that runs locally
    (default model: paraphrase-multilingual-MiniLM-L12-v2, English + French).
    Install with:  pip install sentence-transformers   (first run downloads the model).
  * "hashing" - a small built-in fallback with no extra install. It compares
    standardised words and word pairs, so it captures topic overlap but not real
    meaning. Good enough for a demo; use the real model when you can.

CV_EMBED_BACKEND = auto (default) | sentence-transformers | hashing
CV_EMBED_MODEL   = any sentence-transformers model name

`auto` uses sentence-transformers if it is installed, otherwise hashing.
Vectors from different backends are NOT comparable; each stored vector records its
backend so Remi can recompute when they differ.
"""
from __future__ import annotations

import math
import os
import re
import zlib
from functools import lru_cache

from .vocab import get_vocab

HASH_DIM = 1024
DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_STOP = set("""a an the and or of in on at to for with by from as is are was were be been this that these those
it its i my me we our you your he she they their his her not no but if so than then also into over under
le la les un une des du de et ou en au aux pour par sur dans avec est sont ete ce cette ces il elle nous vous
ils elles son sa ses leur leurs mon ma mes ne pas plus""".split())
_TAG_LINE = re.compile(r"^\[[A-Z_]+(?::[^\]]*)?\]\s*$", re.M)


def strip_tags(text: str) -> str:
    """Remove Oscar's [SECTION] tag lines and the [CANDIDATE] placeholder."""
    t = _TAG_LINE.sub("", text)
    return t.replace("[CANDIDATE]", " ")


# ---------------- hashing backend ----------------
def _hash_vector(text: str) -> list[float]:
    toks = [t for t in get_vocab().canonicalize(text).split() if t not in _STOP and len(t) > 1]
    feats: dict[int, float] = {}
    grams = toks + [f"{a} {b}" for a, b in zip(toks, toks[1:])]
    counts: dict[str, int] = {}
    for g in grams:
        counts[g] = counts.get(g, 0) + 1
    for g, c in counts.items():
        h = zlib.crc32(g.encode("utf-8"))
        idx, sign = h % HASH_DIM, (1.0 if (h >> 16) & 1 else -1.0)
        w = (1.0 + math.log(c)) * (2.0 if g.startswith("k_") else 1.0)   # known terms count double
        feats[idx] = feats.get(idx, 0.0) + sign * w
    vec = [0.0] * HASH_DIM
    for i, v in feats.items():
        vec[i] = v
    return _normalise(vec)


# ---------------- sentence-transformers backend ----------------
@lru_cache(maxsize=2)
def _st_model(name: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(name)


def _chunks(text: str, words: int = 90) -> list[str]:
    toks = text.split()
    return [" ".join(toks[i:i + words]) for i in range(0, len(toks), words)] or [""]


def _st_vector(text: str, model_name: str) -> list[float]:
    model = _st_model(model_name)
    vecs = model.encode(_chunks(text), normalize_embeddings=True)   # long CVs: average the chunks
    mean = [float(sum(col) / len(vecs)) for col in zip(*vecs)]
    return _normalise(mean)


# ---------------- public API ----------------
def _normalise(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


def backend_name() -> str:
    """Which backend embed() will use right now."""
    choice = os.environ.get("CV_EMBED_BACKEND", "auto").lower()
    if choice == "hashing":
        return "hashing"
    if choice in ("auto", "sentence-transformers"):
        try:
            import sentence_transformers  # noqa: F401
            return "sentence-transformers:" + os.environ.get("CV_EMBED_MODEL", DEFAULT_MODEL)
        except ImportError:
            if choice == "sentence-transformers":
                raise RuntimeError("CV_EMBED_BACKEND=sentence-transformers but the package is "
                                   "not installed (pip install sentence-transformers).")
    return "hashing"


def embed(text: str, backend: str | None = None) -> dict:
    """Return {"backend": ..., "dim": n, "vector": [...]} for `text`."""
    backend = backend or backend_name()
    clean = strip_tags(text)
    if backend.startswith("sentence-transformers:"):
        vec = _st_vector(clean, backend.split(":", 1)[1])
    else:
        backend, vec = "hashing", _hash_vector(clean)
    return {"backend": backend, "dim": len(vec), "vector": [round(x, 6) for x in vec]}


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))       # vectors are already unit length


__all__ = ["embed", "cosine", "backend_name", "strip_tags"]
