"""Shared data structures used by every stage of the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Message:
    """A status message attached to a CV. level: 'error' | 'warning' | 'info'."""
    level: str
    code: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Line:
    """One line of text plus whatever layout hints the extractor could recover."""
    text: str
    page: int = 1
    size: Optional[float] = None
    bold: bool = False
    edge: Optional[str] = None
    heading_style: bool = False

    repeated: bool = False

    def with_text(self, text: str) -> "Line":
        return Line(text, self.page, self.size, self.bold, self.edge,
                    self.heading_style, self.repeated)


@dataclass
class ExtractedDoc:
    """Output of the Extract stage."""
    lines: list
    source_type: str
    n_pages: Optional[int] = None
    aux_text: list = field(default_factory=list)
    ocr_pages: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    encoding: Optional[str] = None


@dataclass
class Block:
    """A logical section of the CV (e.g. Experience) split into entries."""
    type: str
    heading: str
    entries: list
    position: int = 0

    @property
    def lines(self) -> list:
        return [ln for entry in self.entries for ln in entry]

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


@dataclass
class DroppedItem:
    """One thing the system removed, and which rule removed it."""
    candidate_id: str
    scope: str
    block_type: str
    rule: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CVResult:
    candidate_id: str
    source_file: str
    file_type: Optional[str] = None
    sha256: str = ""
    status: str = "failed"
    messages: list = field(default_factory=list)
    output_text: str = ""
    pii: Optional[dict] = None
    dropped: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, level: str, code: str, text: str) -> None:
        self.messages.append(Message(level, code, text))

    def finalize_status(self) -> None:
        if any(m.level == "error" for m in self.messages):
            self.status = "failed"
        elif any(m.level == "warning" for m in self.messages):
            self.status = "warning"
        else:
            self.status = "ok"

    def summary_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "source_file": self.source_file,
            "file_type": self.file_type,
            "status": self.status,
            "messages": [m.to_dict() for m in self.messages],
            "stats": self.stats,
        }
