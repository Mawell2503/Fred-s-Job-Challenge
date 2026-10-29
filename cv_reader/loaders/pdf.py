"""PDF loader."""
from __future__ import annotations

import re
import shutil
import statistics
from pathlib import Path

from ..config import Config
from ..models import ExtractedDoc, Line, Message
from .errors import LoaderError

_BOLD_RE = re.compile(r"bold|black|heavy|semibold|demi", re.I)

GUTTER_MIN_PT = 8.0          # >minimum width of a column gap
# >each column must be at least this share of the page width
SIDE_MIN_EXTENT = 0.18
SIDE_MIN_LINES = 5           # >... and contain at least this many lines
# >a narrower side still counts (a sidebar) when it is at least this wide and mostly words, not dates
SIDE_NARROW_MIN_EXTENT = 0.08
SIDE_NARROW_MIN_WORDS = 0.6
# >shorter column must have >= 20% of the longer one's lines
SIDE_MIN_RATIO = 0.20


def ocr_available() -> bool:
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
    except Exception:
        return False
    return True


def _norm_words(raw: list) -> list[dict]:
    out = []
    for w in raw:
        text = w.get("text", "")
        if not text or not text.strip():
            continue
        h = float(w["bottom"]) - float(w["top"])
        out.append({
            "text": text,
            "x0": float(w["x0"]), "x1": float(w["x1"]),
            "top": float(w["top"]), "bottom": float(w["bottom"]),
            "size": float(w.get("size") or h),
            "bold": bool(_BOLD_RE.search(w.get("fontname") or "")),
        })
    return out


def _cluster(words: list[dict]) -> list[list[dict]]:
    """Group words into visual lines (top to bottom, words left to right)."""
    lines: list[dict] = []
    for w in sorted(words, key=lambda w: (w["top"] + w["bottom"]) / 2):
        cy = (w["top"] + w["bottom"]) / 2
        h = max(w["bottom"] - w["top"], 1.0)
        target = None
        for ln in reversed(lines[-8:]):
            if abs(cy - ln["cy"]) <= max(1.5, 0.45 * min(h, ln["h"])):
                target = ln
                break
        if target is None:
            lines.append({"words": [w], "cy": cy, "h": h})
        else:
            n = len(target["words"])
            target["cy"] = (target["cy"] * n + cy) / (n + 1)
            target["h"] = (target["h"] * n + h) / (n + 1)
            target["words"].append(w)
    lines.sort(key=lambda ln: ln["cy"])
    return [sorted(ln["words"], key=lambda w: w["x0"]) for ln in lines]


def _valid_gutter(words: list[dict], gx0: float, gx1: float, pw: float) -> bool:
    mid = (gx0 + gx1) / 2
    left, right = [], []
    n_left = n_right = 0
    for ln in _cluster(words):
        if any(w["x0"] < gx1 and w["x1"] > gx0 for w in ln):
            continue  # spanning line (header/footer)
        l = [w for w in ln if (w["x0"] + w["x1"]) / 2 < mid]
        r = [w for w in ln if (w["x0"] + w["x1"]) / 2 >= mid]
        if l:
            n_left += 1
            left.extend(l)
        if r:
            n_right += 1
            right.extend(r)
    if n_left < SIDE_MIN_LINES or n_right < SIDE_MIN_LINES:
        return False
    for side in (left, right):
        ext = max(w["x1"] for w in side) - min(w["x0"] for w in side)
        if ext >= SIDE_MIN_EXTENT * pw:
            continue
        # A narrow side is either a column of right-aligned dates (not a real column: keep the
        # lines whole) or a sidebar of short items ("Python", "SQL", "French (native)"): a real
        # column. Tell them apart by how much of it is words.
        if ext < SIDE_NARROW_MIN_EXTENT * pw or _word_share(side) < SIDE_NARROW_MIN_WORDS:
            return False
    if min(n_left, n_right) < SIDE_MIN_RATIO * max(n_left, n_right):
        return False
    return True


def _word_share(words: list[dict]) -> float:
    """Share of tokens that are words (3+ letters), as opposed to dates, numbers and dashes."""
    return sum(1 for w in words if len(re.findall(r"[^\W\d_]", w["text"])) >= 3) / max(len(words), 1)


def _find_gutter(words: list[dict], pw: float):
    if len(words) < 25:
        return None
    xmin = min(w["x0"] for w in words)
    xmax = max(w["x1"] for w in words)
    width = xmax - xmin
    if width < 0.4 * pw:
        return None
    rows = _cluster(words)
    if len(rows) < 6:
        return None
    tol = max(2, int(0.06 * len(rows)))
    nb = int(width) + 2
    cov = [0] * nb
    for row in rows:
        covered = set()
        for w in row:
            a = max(int(w["x0"] - xmin), 0)
            b = min(int(w["x1"] - xmin), nb - 1)
            covered.update(range(a, b + 1))
        for i in covered:
            cov[i] += 1

    runs, i = [], 0
    while i < nb:
        if cov[i] <= tol:
            j = i
            while j < nb and cov[j] <= tol:
                j += 1
            runs.append((i, j - 1))
            i = j
        else:
            i += 1

    cands = []
    for a, b in runs:
        if (b - a + 1) < GUTTER_MIN_PT:
            continue
        # The run tolerates a few lines reaching into it (a long email in a sidebar). Keep only its
        # emptiest stretch as the gutter, so those lines are not mistaken for lines spanning both columns.
        low = min(cov[a:b + 1])
        core = [i for i in range(a, b + 1) if cov[i] == low]
        best, start = (core[0], core[0]), core[0]
        for i, j in zip(core, core[1:] + [None]):
            if j != i + 1:
                if i - start > best[1] - best[0]:
                    best = (start, i)
                start = j
        if best[1] - best[0] + 1 >= GUTTER_MIN_PT:
            a, b = best
        gx0, gx1 = xmin + a, xmin + b + 1
        mid = (gx0 + gx1) / 2
        if not (xmin + 0.12 * width < mid < xmax - 0.12 * width):
            continue
        cands.append((b - a, gx0, gx1))
    cands.sort(reverse=True)
    for _, gx0, gx1 in cands:
        if _valid_gutter(words, gx0, gx1, pw):
            return gx0, gx1
    return None


def _hint_gutter(words: list[dict], hints: list) -> tuple | None:
    """Sparse page (e.g. a short 2nd page): reuse a gutter found on another page of the
    same document if its strip is empty here and both sides have text."""
    rows = _cluster(words)
    for gx0, gx1 in hints:
        mid = (gx0 + gx1) / 2
        crossing = sum(1 for r in rows if any(
            w["x0"] < gx1 and w["x1"] > gx0 for w in r))
        left = sum(1 for r in rows if any(
            (w["x0"] + w["x1"]) / 2 < mid for w in r))
        right = sum(1 for r in rows if any(
            (w["x0"] + w["x1"]) / 2 >= mid for w in r))
        if crossing <= max(1, len(rows) // 8) and left >= 2 and right >= 2:
            return gx0, gx1
    return None


def _order_region(words: list[dict], pw: float, depth: int = 0, hints: list | None = None
                  ) -> list[list[dict]]:
    if depth >= 2 or not words:
        return _cluster(words)
    gutter = _find_gutter(words, pw)
    if gutter is not None and depth == 0 and hints is not None:
        hints.append(gutter)
    if gutter is None and depth == 0 and hints:
        gutter = _hint_gutter(words, hints)
    if gutter is None:
        return _cluster(words)
    gx0, gx1 = gutter
    mid = (gx0 + gx1) / 2
    out: list[list[dict]] = []
    band_l: list[dict] = []
    band_r: list[dict] = []

    def flush():
        if band_l:
            out.extend(_order_region(list(band_l), pw, depth + 1))
        if band_r:
            out.extend(_order_region(list(band_r), pw, depth + 1))
        band_l.clear()
        band_r.clear()

    for ln in _cluster(words):
        if any(w["x0"] < gx1 and w["x1"] > gx0 for w in ln):   # spans the gutter
            flush()
            out.append(ln)
        else:
            for w in ln:
                (band_l if (w["x0"] + w["x1"]) / 2 < mid else band_r).append(w)
    flush()
    return out


def _join(words: list[dict]) -> str:
    parts = [words[0]["text"]]
    for prev, w in zip(words, words[1:]):
        gap = w["x0"] - prev["x1"]
        size = max(prev["size"], 1.0)
        if gap < 0.15 * size:
            # touching (font change, punctuation)
            parts.append(w["text"])
        elif gap > 1.0 * size:
            # big gap (tab / column inside a line)
            parts.append("  " + w["text"])
        else:
            parts.append(" " + w["text"])
    return "".join(parts)


def _to_lines(ordered: list[list[dict]], page_no: int, page_h: float) -> list[Line]:
    out: list[Line] = []
    prev = None
    for words in ordered:
        text = _join(words)
        size = statistics.median(w["size"] for w in words)
        top = min(w["top"] for w in words)
        bottom = max(w["bottom"] for w in words)
        h = max(bottom - top, 1.0)
        bold = all(w["bold"] for w in words)
        if prev is not None:
            p_top, p_bottom, p_h = prev
            gap = top - p_bottom
            if top < p_top - 0.3 * p_h or gap > 0.6 * max(p_h, h):
                # paragraph / column break
                out.append(Line("", page=page_no))
        edge = "top" if bottom < 0.09 * \
            page_h else ("bottom" if top > 0.91 * page_h else None)
        out.append(Line(text, page=page_no, size=round(
            size, 1), bold=bold, edge=edge))
        prev = (top, bottom, h)
    return out
# OCR


def _ocr_words(page, cfg: Config) -> list[dict]:
    import pytesseract
    from pytesseract import Output

    img = page.to_image(resolution=cfg.ocr_dpi).original
    data = pytesseract.image_to_data(
        img, lang=cfg.ocr_lang, output_type=Output.DICT)
    scale = 72.0 / cfg.ocr_dpi
    words = []
    for i, text in enumerate(data["text"]):
        text = (text or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if conf < 25:
            continue
        x, y = data["left"][i] * scale, data["top"][i] * scale
        w, h = data["width"][i] * scale, data["height"][i] * scale
        words.append({"text": text, "x0": x, "x1": x + w, "top": y, "bottom": y + h,
                      "size": h, "bold": False})
    return words


def _garbled(words: list[dict]) -> bool:
    if len(words) < 10:
        return False
    bad = sum(1 for w in words if "(cid:" in w["text"] or "�" in w["text"])
    return bad / len(words) > 0.3
# Entry point


def load_pdf(path: Path, cfg: Config) -> ExtractedDoc:
    import pdfplumber

    doc = ExtractedDoc(lines=[], source_type="pdf")
    ocr_ok = cfg.ocr_enabled and ocr_available()
    ocr_off_reason = ("OCR is disabled in the settings" if not cfg.ocr_enabled
                      else "the OCR engine (Tesseract) is not installed")
    try:
        with pdfplumber.open(str(path)) as pdf:
            n = len(pdf.pages)
            if n == 0:
                raise LoaderError("EMPTY_FILE", "The PDF has no pages.")
            doc.n_pages = n
            gutter_hints: list = []
            for pno, page in enumerate(pdf.pages, start=1):
                try:
                    raw = page.extract_words(x_tolerance=1.5, y_tolerance=2,
                                             keep_blank_chars=False, use_text_flow=False,
                                             extra_attrs=["size", "fontname"])
                except Exception as e:
                    raw = []
                    doc.messages.append(Message(
                        "warning", "PAGE_READ_ERROR",
                        f"Page {pno} could not be read ({type(e).__name__}: {e})."))
                words = _norm_words(raw)
                n_chars = sum(len(w["text"]) for w in words)
                garbled = _garbled(words)

                if n_chars < cfg.ocr_min_chars_per_page or garbled:
                    if ocr_ok:
                        try:
                            ocr_words = _ocr_words(page, cfg)
                            if sum(len(w["text"]) for w in ocr_words) > n_chars or garbled:
                                words = ocr_words
                                doc.ocr_pages.append(pno)
                        except Exception as e:
                            doc.messages.append(Message(
                                "warning", "OCR_FAILED",
                                f"OCR failed on page {pno} ({type(e).__name__}: {e})."))
                    elif garbled:
                        doc.messages.append(Message(
                            "warning", "GARBLED_TEXT",
                            f"Page {pno} has unreadable (garbled) text and {ocr_off_reason}."))
                    else:
                        doc.messages.append(Message(
                            "warning", "NO_TEXT_LAYER",
                            f"Page {pno} has no text layer (scanned?) and {ocr_off_reason}."))

                ordered = _order_region(words, float(
                    page.width), hints=gutter_hints)
                doc.lines.extend(_to_lines(ordered, pno, float(page.height)))
                if pno < n:
                    doc.lines.append(Line("", page=pno))
                try:
                    for h in page.hyperlinks:
                        if h.get("uri"):
                            doc.aux_text.append(h["uri"])
                except Exception:
                    pass
                try:
                    page.flush_cache()
                except Exception:
                    pass
    except LoaderError:
        raise
    except Exception as e:
        raise LoaderError("UNREADABLE",
                          f"Could not read the PDF ({type(e).__name__}: {e}). "
                          f"It may be corrupted or password-protected.")

    if doc.ocr_pages:
        doc.messages.append(Message(
            "warning", "OCR_USED",
            f"Page(s) {', '.join(map(str, doc.ocr_pages))} had no usable text layer and were "
            f"read with OCR; expect some recognition errors."))
    return doc
