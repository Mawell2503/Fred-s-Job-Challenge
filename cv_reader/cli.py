"""Command line interface:  python -m cv_reader <files/folders> -o output_dir"""
from __future__ import annotations

import argparse
import sys

from .pipeline import run_pipeline
from .rules import RulesError

_TAG = {"ok": "[OK]    ", "warning": "[WARN]  ", "failed": "[FAILED]"}


def _print_result(res, show_text: bool = False) -> None:
    st = res.stats
    bits = [res.file_type or "?"]
    if "blocks_kept" in st:
        bits.append(f"blocks={st['blocks_kept']}/{st['blocks_found']}")
        bits.append(f"chars={st['chars_in_output']}")
    print(
        f"{_TAG[res.status]} {res.candidate_id}  {res.source_file}  ({', '.join(bits)})")
    for m in res.messages:
        mark = "x" if m.level == "error" else "!"
        print(f"           {mark} {m.code}: {m.text}")
    if show_text and res.output_text:
        print("-" * 70)
        print(res.output_text)
        print("-" * 70)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="cv_reader",
        description="Read CVs (pdf/docx/txt), separate personal info, apply rules, "
                    "and output one clean text block per CV.")
    ap.add_argument("inputs", nargs="+", help="CV files and/or folders")
    ap.add_argument("-o", "--out", default="cv_output",
                    help="output folder (default: cv_output)")
    ap.add_argument(
        "--rules", help="rules file (.yaml/.json); overrides the defaults")
    ap.add_argument("--min-chars", type=int,
                    help="minimum extracted characters (default 100)")
    ap.add_argument("--no-ocr", action="store_true",
                    help="disable OCR for scanned PDFs")
    ap.add_argument(
        "--ocr-lang", help="Tesseract language code (default: eng)")
    ap.add_argument("--no-embed", action="store_true",
                    help="skip computing the profile embedding")
    ap.add_argument("--id-start", type=int, default=1,
                    help="first candidate number (default 1)")
    ap.add_argument("--print", dest="show_text",
                    action="store_true", help="print each output")
    args = ap.parse_args(argv)

    cfg = {}
    if args.min_chars is not None:
        cfg["min_chars"] = args.min_chars
    if args.no_ocr:
        cfg["ocr_enabled"] = False
    if args.ocr_lang:
        cfg["ocr_lang"] = args.ocr_lang
    if args.no_embed:
        cfg["embed_enabled"] = False

    try:
        batch = run_pipeline(args.inputs, rules=args.rules, config=cfg, out_dir=args.out,
                             id_start=args.id_start,
                             on_progress=lambda r: _print_result(r, args.show_text))
    except RulesError as e:
        print(f"RULES ERROR - nothing was processed.\n{e}", file=sys.stderr)
        return 2

    c = batch.counts()
    print(f"\nProcessed {c['files']} file(s): {c['ok']} ok, {c['warning']} with warnings, "
          f"{c['failed']} FAILED.  Output: {args.out}/")
    if batch.skipped:
        print(f"Skipped {len(batch.skipped)} unsupported file(s) found in folders "
              f"(listed in batch_summary.json).")
    if c["failed"]:
        print("\nFAILED FILES (no output was produced for these):", file=sys.stderr)
        for r in batch.results:
            if r.status == "failed":
                why = "; ".join(
                    f"{m.code}" for m in r.messages if m.level == "error")
                print(f"  {r.candidate_id}  {r.source_file}: {why}",
                      file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
