"""Ties the six stages together and handles batches.

    1 Load      detect file type (by content), open it          loaders/
    2 Extract   raw lines + layout hints                        loaders/
    3 Normalize clean up extraction mess                        normalize.py
    4 Segment   labeled blocks / entries                        segment.py
      (+ PII split: personal info moved to a separate record)   pii.py
    5 Rules     keep / drop per block and per line              rules/
    6 Assemble  one output text block per CV                    assemble.py

Every file is processed in isolation: one bad CV is reported, never stops the batch.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .assemble import assemble
from .config import Config
from .loaders import EXT_TYPES, LoaderError, load
from .models import CVResult, DroppedItem
from .normalize import normalize
from .pii import split_pii
from .rules import RuleEngine, load_rules
from .segment import segment

_SCAN_EXTS = set(EXT_TYPES) | {".doc", ".rtf", ".odt"}
_ERROR_RELATED = {"NO_TEXT_LAYER", "OCR_FAILED",
                  "GARBLED_TEXT", "DOCX_IMAGES_ONLY"}


@dataclass
class BatchResult:
    results: list = field(default_factory=list)          # list[CVResult]
    # files ignored while scanning folders
    skipped: list = field(default_factory=list)
    rules_used: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)

    @property
    def pii_records(self) -> list:
        return [r.pii for r in self.results if r.pii]

    @property
    def dropped_log(self) -> list:
        return [d.to_dict() for r in self.results for d in r.dropped]

    def counts(self) -> dict:
        c = {"files": len(self.results), "ok": 0, "warning": 0, "failed": 0}
        for r in self.results:
            c[r.status] += 1
        return c

    def summary(self) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "totals": self.counts(),
            "skipped_files": self.skipped,
            "files": [r.summary_dict() for r in self.results],
            "config": self.config,
        }


# ---------------------------------------------------------------------------
def collect_files(inputs) -> tuple[list[Path], list[str]]:
    """Expand folders (recursively). Explicitly named files are always processed;
    files found inside folders are filtered by extension and the rest reported as skipped."""
    files: list[Path] = []
    skipped: list[str] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    if f.suffix.lower() in _SCAN_EXTS:
                        files.append(f)
                    else:
                        skipped.append(str(f))
        else:
            # a missing path is reported as a failed file later
            files.append(p)
    return files, skipped


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_file(path, candidate_id: str, engine: RuleEngine, cfg: Config) -> CVResult:
    path = Path(path)
    res = CVResult(candidate_id=candidate_id, source_file=path.name)

    try:
        res.sha256 = _sha256(path)
    except OSError as e:
        res.add("error", "UNREADABLE", f"Cannot read the file: {e}")
        res.finalize_status()
        return res

    # 1+2 Load + Extract
    try:
        doc = load(path, cfg)
    except LoaderError as e:
        res.add("error", e.code, e.message)
        res.finalize_status()
        return res
    res.file_type = doc.source_type
    res.messages.extend(doc.messages)

    # 3 Normalize
    lines = normalize(doc)
    n_chars = sum(len(ln.text.strip()) for ln in lines)
    res.stats["extracted_chars"] = n_chars
    if doc.n_pages:
        res.stats["pages"] = doc.n_pages
    if doc.ocr_pages:
        res.stats["ocr_pages"] = doc.ocr_pages
    if n_chars < cfg.min_chars:
        extra = " See the related warnings." if any(
            m.code in _ERROR_RELATED for m in res.messages) else ""
        res.add("error", "EMPTY_EXTRACTION",
                f"Only {n_chars} readable characters were extracted (minimum is {cfg.min_chars}). "
                f"The file may be scanned, image-only, corrupted or empty.{extra} "
                f"No output was produced for this CV.")
        res.finalize_status()
        return res
    if n_chars < cfg.low_text_chars:
        res.add("warning", "LOW_TEXT",
                f"Very little text was extracted ({n_chars} characters); the CV may be incomplete.")

    # 4 Segment (+ PII split)
    blocks, info = segment(lines)
    if info["n_known_headings"] == 0:
        res.add("warning", "NO_SECTIONS_FOUND",
                "No standard section headings (Experience, Education, Skills ...) were recognised; "
                "the content is kept as one [UNSORTED] block.")
    pii, removed = split_pii(blocks, doc.aux_text)
    res.pii = {"candidate_id": candidate_id,
               "source_file": path.name, "sha256": res.sha256, **pii}
    for cat, count in sorted(removed.items()):
        res.dropped.append(DroppedItem(candidate_id, "pii", "-", f"pii:{cat}",
                                       f"[{count} item(s) moved to the PII store]"))
    if not pii["name"]:
        res.add("warning", "NAME_NOT_FOUND",
                "The candidate's name could not be identified.")
    if not pii["emails"] and not pii["phones"]:
        res.add("warning", "NO_CONTACT_FOUND",
                "No email address or phone number was found.")

    chars_before = sum(len(ln.text.strip()) for b in blocks for ln in b.lines)

    # 5 Rules
    kept, dropped = engine.apply(blocks, candidate_id)
    res.dropped.extend(dropped)

    # 6 Assemble
    chars_after = sum(len(ln.text.strip()) for b in kept for ln in b.lines)
    res.stats.update({
        "blocks_found": len(blocks),
        "blocks_kept": len(kept),
        "kept_section_types": [b.type for b in kept],
        "chars_after_pii_split": chars_before,
        "chars_in_output": chars_after,
        "dropped_items": len([d for d in res.dropped if d.scope != "pii"]),
    })
    if chars_after < cfg.min_output_chars:
        res.add("error", "OUTPUT_EMPTY",
                f"Only {chars_after} characters remain after personal-info extraction and rules "
                f"(minimum is {cfg.min_output_chars}). No output was produced; check dropped_log.json.")
    else:
        res.output_text = assemble(kept, candidate_id)
        if chars_before and (chars_before - chars_after) / chars_before > 0.6:
            res.add("warning", "RULES_DROPPED_MOST",
                    f"The rules removed {100 * (chars_before - chars_after) // chars_before}% of the text.")
    res.finalize_status()
    return res


def run_pipeline(inputs, rules=None, config=None, out_dir=None, id_start: int = 1,
                 on_progress=None) -> BatchResult:
    """Process files and/or folders.

    inputs  : list of file/folder paths
    rules   : None | dict (partial is fine) | path to .yaml/.json   <- UI rules enter here
    config  : None | dict | Config
    out_dir : if given, results are written there (see write_outputs)
    """
    cfg = config if isinstance(config, Config) else Config.from_dict(config)
    rules_dict = load_rules(rules)
    engine = RuleEngine(rules_dict)

    files, skipped = collect_files(inputs)
    batch = BatchResult(
        skipped=skipped, rules_used=rules_dict, config=cfg.to_dict())

    first_by_hash: dict[str, str] = {}
    for n, path in enumerate(files, start=id_start):
        cid = f"C{n:04d}"
        try:
            res = process_file(path, cid, engine, cfg)
        except Exception as e:     # a bug on one file must never stop the batch
            res = CVResult(candidate_id=cid, source_file=Path(path).name)
            res.add("error", "UNEXPECTED_ERROR", f"{type(e).__name__}: {e}")
            res.finalize_status()
        if res.sha256:
            if res.sha256 in first_by_hash:
                res.add("warning", "DUPLICATE_OF",
                        f"Identical file content to {first_by_hash[res.sha256]}.")
                res.finalize_status()
            else:
                first_by_hash[res.sha256] = cid
        batch.results.append(res)
        if on_progress:
            on_progress(res)

    if out_dir:
        write_outputs(batch, out_dir)
    return batch


def write_outputs(batch: BatchResult, out_dir) -> None:
    out = Path(out_dir)
    text_dir = out / "cv_text"
    text_dir.mkdir(parents=True, exist_ok=True)
    # never leave stale outputs from a previous run
    for old in text_dir.glob("*.txt"):
        old.unlink()
    for r in batch.results:
        if r.status != "failed" and r.output_text:
            (text_dir /
             f"{r.candidate_id}.txt").write_text(r.output_text, encoding="utf-8")

    def dump(name: str, data) -> None:
        (out / name).write_text(json.dumps(data,
                                           indent=2, ensure_ascii=False), encoding="utf-8")

    # personal info ONLY, one file for the batch
    dump("pii_store.json", batch.pii_records)
    # what was removed + which rule (ids only, no names)
    dump("dropped_log.json", batch.dropped_log)
    dump("batch_summary.json", batch.summary())
    dump("rules_used.json", batch.rules_used)
