"""DOCX loader"""
from __future__ import annotations

import re
import statistics
from pathlib import Path

from ..config import Config
from ..models import ExtractedDoc, Line, Message
from .errors import LoaderError

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


W_P, W_TBL, W_TR, W_TC, W_SDT, W_SDTCONTENT = (_w(t) for t in ("p", "tbl", "tr", "tc", "sdt", "sdtContent"))
W_T, W_TAB, W_BR, W_CR, W_NBH = (_w(t) for t in ("t", "tab", "br", "cr", "noBreakHyphen"))
W_R, W_RPR, W_B, W_SZ, W_VAL = (_w(t) for t in ("r", "rPr", "b", "sz", "val"))
W_PPR, W_PSTYLE, W_NUMPR, W_TXBX = (_w(t) for t in ("pPr", "pStyle", "numPr", "txbxContent"))
MC_FALLBACK = f"{{{MC}}}Fallback"

_OFF = {"0", "false", "off"}


def _own_nodes(el):
    """Descendants in document order, skipping text boxes and VML fallbacks
    (those are read separately / are duplicates)."""
    for ch in el:
        if ch.tag in (W_TXBX, MC_FALLBACK):
            continue
        yield ch
        yield from _own_nodes(ch)


def _is_on(el) -> bool:
    return el is not None and el.get(W_VAL) not in _OFF


class _Styles:
    """Resolve paragraph-style font size / bold through python-docx styles."""

    def __init__(self, document):
        self.doc = document
        self.cache: dict = {}
        self.default_size = None
        try:
            sz = document.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:sz")
            if sz:
                self.default_size = int(sz[0].get(W_VAL)) / 2
        except Exception:
            pass

    def lookup(self, style_id):
        if not style_id:
            return None, False, False
        if style_id in self.cache:
            return self.cache[style_id]
        size, bold, heading = None, False, False
        try:
            from docx.enum.style import WD_STYLE_TYPE
            style = self.doc.styles.get_by_id(style_id, WD_STYLE_TYPE.PARAGRAPH)
            name = (style.name or "").lower() if style is not None else style_id.lower()
            heading = name.startswith("heading") or name == "title" or style_id.lower().startswith("heading")
            hops = 0
            while style is not None and hops < 8:
                f = style.font
                if size is None and f.size is not None:
                    size = f.size.pt
                if f.bold:
                    bold = True
                style = style.base_style
                hops += 1
        except Exception:
            heading = style_id.lower().startswith("heading") or style_id.lower() == "title"
        self.cache[style_id] = (size, bold, heading)
        return self.cache[style_id]


def _para_text(p) -> str:
    parts: list[str] = []
    for n in _own_nodes(p):
        tag = n.tag
        if tag == W_T:
            parts.append(n.text or "")
        elif tag == W_TAB:
            parts.append("\t")
        elif tag in (W_BR, W_CR):
            if n.get(_w("type")) != "page":
                parts.append("\n")
        elif tag == W_NBH:
            parts.append("-")
    return "".join(parts)


def _para_font(p, styles: _Styles):
    ppr = p.find(W_PPR)
    style_id = None
    if ppr is not None:
        ps = ppr.find(W_PSTYLE)
        if ps is not None:
            style_id = ps.get(W_VAL)
    s_size, s_bold, heading = styles.lookup(style_id)

    sizes: list[float] = []
    bolds: list[bool] = []
    for run in (n for n in _own_nodes(p) if n.tag == W_R):
        text = "".join((t.text or "") for t in _own_nodes(run) if t.tag == W_T)
        if not text.strip():
            continue
        rpr = run.find(W_RPR)
        size = None
        bold = s_bold
        if rpr is not None:
            sz = rpr.find(W_SZ)
            if sz is not None and sz.get(W_VAL):
                try:
                    size = int(sz.get(W_VAL)) / 2
                except ValueError:
                    pass
            b = rpr.find(W_B)
            if b is not None:
                bold = _is_on(b)
        sizes.append(size if size is not None else (s_size or styles.default_size))
        bolds.append(bold)
    real = [s for s in sizes if s]
    size = statistics.median(real) if real else (s_size or styles.default_size)
    return size, (bool(bolds) and all(bolds)), heading


def _has_numbering(p) -> bool:
    ppr = p.find(W_PPR)
    return ppr is not None and ppr.find(W_NUMPR) is not None


def _para_lines(p, ctx):
    text = _para_text(p)
    out: list[Line] = []
    if text.strip():
        size, bold, heading = _para_font(p, ctx["styles"])
        prefix = "- " if _has_numbering(p) and not re.match(r"^\s*[-•▪●◦*]", text) else ""
        for i, part in enumerate(text.split("\n")):
            part = part.replace("\t", "  ")
            if part.strip():
                out.append(Line((prefix if i == 0 else "") + part, size=size, bold=bold,
                                heading_style=heading))
    else:
        out.append(Line(""))

    # text boxes anchored in this paragraph
    for tb in p.iter(W_TXBX):
        if any(a.tag == MC_FALLBACK for a in tb.iterancestors()):
            continue
        box = list(_walk_blocks(tb, ctx))
        if any(ln.text.strip() for ln in box):
            out.append(Line(""))
            out.extend(box)
            out.append(Line(""))
    return out


def _cell_lines(tc, ctx) -> list[Line]:
    lines = list(_walk_blocks(tc, ctx))
    while lines and not lines[-1].text.strip():
        lines.pop()
    while lines and not lines[0].text.strip():
        lines.pop(0)
    return lines


def _table_lines(tbl, ctx):
    rows = [tr for tr in tbl.findall(W_TR)]
    if ctx["cfg"].docx_table_order == "column":
        grid = [[tc for tc in tr.findall(W_TC)] for tr in rows]
        ncols = max((len(r) for r in grid), default=0)
        for ci in range(ncols):
            for r in grid:
                if ci < len(r):
                    cell = _cell_lines(r[ci], ctx)
                    if cell:
                        yield from cell
                        yield Line("")
        return

    for tr in rows:
        cells = [_cell_lines(tc, ctx) for tc in tr.findall(W_TC)]
        cells = [c for c in cells if c]
        if not cells:
            continue
        # a row like "Python | Expert | 5 yrs" or "2019-2022 | Developer": keep on one line
        if len(cells) >= 2 and all(len(c) == 1 for c in cells):
            first = cells[0][0]
            yield Line("  ".join(c[0].text.strip() for c in cells),
                       size=first.size, bold=first.bold, heading_style=first.heading_style)
        else:
            for c in cells:
                yield from c
                yield Line("")


def _walk_blocks(parent, ctx):
    for ch in parent:
        tag = ch.tag
        if tag == W_P:
            yield from _para_lines(ch, ctx)
        elif tag == W_TBL:
            yield from _table_lines(ch, ctx)
            yield Line("")
        elif tag == W_SDT:
            content = ch.find(W_SDTCONTENT)
            if content is not None:
                yield from _walk_blocks(content, ctx)
        elif tag.endswith("}customXml") or tag.endswith("}ins") or tag.endswith("}smartTag"):
            yield from _walk_blocks(ch, ctx)


def load_docx(path: Path, cfg: Config) -> ExtractedDoc:
    try:
        import docx  # python-docx
        document = docx.Document(str(path))
    except Exception as e:  # damaged zip/xml, password protected, ...
        raise LoaderError("CORRUPT", f"Could not open the DOCX ({type(e).__name__}: {e}).")

    ctx = {"styles": _Styles(document), "cfg": cfg}
    body = document.element.body
    body_lines = list(_walk_blocks(body, ctx))

    header_lines: list[Line] = []
    footer_lines: list[Line] = []
    seen_h: set[str] = set()
    seen_f: set[str] = set()
    try:
        for section in document.sections:
            for attr, target, seen, edge in (
                ("header", header_lines, seen_h, "header"),
                ("first_page_header", header_lines, seen_h, "header"),
                ("even_page_header", header_lines, seen_h, "header"),
                ("footer", footer_lines, seen_f, "footer"),
                ("first_page_footer", footer_lines, seen_f, "footer"),
                ("even_page_footer", footer_lines, seen_f, "footer"),
            ):
                part = getattr(section, attr)
                if part.is_linked_to_previous:
                    continue
                lines = [ln for ln in _walk_blocks(part._element, ctx)]
                key = "\n".join(ln.text for ln in lines if ln.text.strip())
                if not key or key in seen:
                    continue
                seen.add(key)
                for ln in lines:
                    if ln.text.strip():
                        ln.edge = edge
                        target.append(ln)
    except Exception:
        pass  # header/footer parts are optional extras; never fail the CV over them

    aux: list[str] = []
    try:
        for rel in document.part.rels.values():
            if rel.reltype.endswith("/hyperlink") and rel.is_external:
                aux.append(rel.target_ref)
    except Exception:
        pass

    lines = header_lines + ([Line("")] if header_lines else []) + body_lines + \
        ([Line("")] if footer_lines else []) + footer_lines

    doc = ExtractedDoc(lines=lines, source_type="docx", n_pages=None, aux_text=aux)
    try:
        n_images = len(document.inline_shapes)
    except Exception:
        n_images = 0
    text_len = sum(len(ln.text.strip()) for ln in lines)
    if n_images and text_len < 200:
        doc.messages.append(Message(
            "warning", "DOCX_IMAGES_ONLY",
            f"The DOCX contains {n_images} image(s) but almost no text; text inside images "
            f"is not read (OCR is only applied to PDFs)."))
    return doc