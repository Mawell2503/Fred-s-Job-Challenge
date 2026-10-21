"""Plain text loader. Main difficulty: encoding."""
from __future__ import annotations

import codecs
import re
from pathlib import Path

from ..config import Config
from ..models import ExtractedDoc, Line
from .errors import LoaderError


def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def load_txt(path: Path, cfg: Config) -> ExtractedDoc:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise LoaderError("UNREADABLE", f"Cannot read file: {e}")
    text, enc = _decode(data)
    lines: list[Line] = []
    pages = text.split("\f")          # form feed = page break in plain-text exports
    for page_no, page_text in enumerate(pages, start=1):
        for raw in re.split(r"\r\n|\n|\r", page_text):
            lines.append(Line(raw, page=page_no))
    return ExtractedDoc(lines=lines, source_type="txt", n_pages=len(pages), encoding=enc)