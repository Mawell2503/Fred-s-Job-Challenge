"""Stage 1 + 2: Load (detect type, open) and Extract (raw lines + layout hints).

The file type is detected from the file's CONTENT (magic bytes)
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from ..config import Config
from ..models import ExtractedDoc, Message
from .errors import LoaderError

EXT_TYPES = {".pdf": "pdf", ".docx": "docx", ".docm": "docx", ".dotx": "docx",
             ".txt": "txt", ".text": "txt"}

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect_type(path: str | Path) -> str:
    """Return 'pdf' | 'docx' | 'txt' or raise LoaderError."""
    path = Path(path)
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError as e:
        raise LoaderError("UNREADABLE", f"Cannot open file: {e}")
    if not head:
        raise LoaderError("EMPTY_FILE", "The file is empty (0 bytes).")

    if b"%PDF" in head[:1024]:
        return "pdf"

    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "word/document.xml" in names:
                    return "docx"
                if "mimetype" in names:
                    raise LoaderError("UNSUPPORTED_FORMAT",
                                      "OpenDocument (.odt) is not supported. Save as .docx or PDF.")
                raise LoaderError("UNSUPPORTED_FORMAT",
                                  "This is a ZIP-based file but not a Word document "
                                  "(maybe .xlsx/.pptx/.zip).")
        except zipfile.BadZipFile:
            raise LoaderError("CORRUPT", "The file looks like a .docx but the ZIP structure is damaged.")

    if head.startswith(_OLE_MAGIC):
        raise LoaderError("UNSUPPORTED_FORMAT",
                          "Legacy binary Word file (.doc) is not supported. "
                          "Re-save it as .docx or PDF.")
    if head.startswith(b"{\\rtf"):
        raise LoaderError("UNSUPPORTED_FORMAT", "RTF is not supported. Re-save as .docx or PDF.")
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM")):
        raise LoaderError("UNSUPPORTED_FORMAT",
                          "Image files are not supported. Provide the CV as PDF, DOCX or TXT.")

    bom_utf16 = head.startswith((b"\xff\xfe", b"\xfe\xff"))
    if b"\x00" in head and not bom_utf16:
        raise LoaderError("UNSUPPORTED_FORMAT", "Binary file of unknown type.")
    return "txt"


def load(path: str | Path, cfg: Config) -> ExtractedDoc:
    """Detect the type and extract lines. Raises LoaderError on failure."""
    path = Path(path)
    size_mb = os.path.getsize(path) / (1024 * 1024) if path.exists() else 0
    if size_mb > cfg.max_file_mb:
        raise LoaderError("FILE_TOO_LARGE",
                          f"File is {size_mb:.1f} MB (limit {cfg.max_file_mb} MB).")

    ftype = detect_type(path)
    if ftype == "pdf":
        from .pdf import load_pdf
        doc = load_pdf(path, cfg)
    elif ftype == "docx":
        from .docx import load_docx
        doc = load_docx(path, cfg)
    else:
        from .txt import load_txt
        doc = load_txt(path, cfg)

    ext_type = EXT_TYPES.get(path.suffix.lower())
    if ext_type and ext_type != ftype:
        doc.messages.append(Message(
            "warning", "EXTENSION_MISMATCH",
            f"File extension says '{path.suffix}' but the content is {ftype.upper()}; "
            f"it was read as {ftype.upper()}."))
    return doc


__all__ = ["load", "detect_type", "LoaderError", "EXT_TYPES"]