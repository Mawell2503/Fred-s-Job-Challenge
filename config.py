"""Runtime settings"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields


@dataclass
class Config:
    min_chars: int = 100
    low_text_chars: int = 400
    min_output_chars: int = 60
    ocr_enabled: bool = True
    ocr_lang: str = "eng"
    ocr_dpi: int = 300
    ocr_min_chars_per_page: int = 25
    max_file_mb: int = 30
    docx_table_order: str = "row"

    @classmethod
    def from_dict(cls, data: dict | None) -> "Config":
        data = data or {}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)
