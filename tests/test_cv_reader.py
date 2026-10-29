"""Oscar tests on real-world CV layouts: python -m unittest discover -s tests   (no AI).

The PDFs are written by hand (a few lines of PDF syntax) so the tests need nothing beyond the
project's own requirements."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from cv_reader.pipeline import run_pipeline
from cv_reader.structure import parse_range


def make_pdf(path: Path, items):
    """items = [(x, y, size, bold, text)] on one A4 page, Helvetica."""
    ops = ["BT"]
    for x, y, size, bold, text in items:
        esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"/{'F2' if bold else 'F1'} {size} Tf 1 0 0 1 {x} {y} Tm ({esc}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))


def oscar(*files):
    """Run Oscar on the files; return [(record, full_text, pii)] in order."""
    with tempfile.TemporaryDirectory() as out:
        run_pipeline([str(f) for f in files], config={"embed_enabled": False, "ocr_enabled": False}, out_dir=out)
        o = Path(out)
        pii = {p["candidate_id"]: p for p in json.loads((o / "pii_store.json").read_text())}
        res = []
        for f in json.loads((o / "batch_summary.json").read_text())["files"]:
            cid = f["candidate_id"]
            res.append((json.loads((o / "cv_json" / f"{cid}.json").read_text()),
                        (o / "cv_full" / f"{cid}.txt").read_text(), pii.get(cid, {})))
        return res


JOBS = [("Senior Data Engineer  Lagoon Retail  Feb 2022 - Present",
         ["- Built the Airflow platform that loads 300 stores every night", "- Weekly calls in French with the client"]),
        ("Data Engineer  Coral Analytics  Jan 2019 - Jan 2022",
         ["- Wrote Python ETL jobs into PostgreSQL", "- Moved the jobs to Docker on AWS"])]


class Layouts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_a_two_column_cv_with_a_sidebar_is_read_column_by_column(self):
        side = [("Jordan Ellery", 13, True), ("", 9, False), ("CONTACT", 9, True), ("jordan.ellery@example.com", 9, False),
                ("+230 5799 0001", 9, False), ("Curepipe, Mauritius", 9, False), ("", 9, False), ("SKILLS", 9, True),
                ("Python", 9, False), ("SQL", 9, False), ("PostgreSQL", 9, False), ("Airflow", 9, False),
                ("Docker", 9, False), ("AWS", 9, False), ("", 9, False), ("LANGUAGES", 9, True),
                ("French (native)", 9, False), ("English (fluent)", 9, False)]
        main = [("SUMMARY", True), ("Data engineer with 6 years building Python and SQL pipelines", False), ("", False),
                ("EXPERIENCE", True)] + [x for head, bullets in JOBS for x in [(head, False)] + [(b, False) for b in bullets]
                                         + [("", False)]] + [("EDUCATION", True), ("BSc Computer Science, 2015 - 2018", False)]
        items = [(20, 790 - 15 * i, size, bold, t) for i, (t, size, bold) in enumerate(side) if t]
        items += [(210, 790 - 15 * i, 9, bold, t) for i, (t, bold) in enumerate(main) if t]
        make_pdf(self.tmp / "two_columns.pdf", items)
        rec, full, pii = oscar(self.tmp / "two_columns.pdf")[0]
        self.assertEqual(pii.get("name"), "Jordan Ellery")
        self.assertNotIn("Jordan", full)                       # personal details never reach the AI's text
        self.assertNotIn("jordan.ellery@example.com", full)
        self.assertEqual(len(rec["experience"]), 2)
        self.assertGreater(rec["total_years_experience"] or 0, 7)
        self.assertEqual(len(rec["skills_listed"]), 6)         # "SQL" and "AWS" are items, not headings

    def test_right_aligned_dates_do_not_split_a_one_column_cv(self):
        lines = [("Sam Rivers", 14, True, None), ("SUMMARY", 10, True, None),
                 ("Data engineer building Python and SQL pipelines for retail.", 10, False, None),
                 ("EXPERIENCE", 10, True, None)]
        for head, bullets in JOBS:
            title, _, dates = head.rpartition("  ")
            lines.append((title, 10, True, dates))
            lines += [(b, 10, False, None) for b in bullets]
        lines += [("SKILLS", 10, True, None), ("Python, SQL, Airflow, Docker, AWS", 10, False, None)]
        items = []
        for i, (t, size, bold, dates) in enumerate(lines):
            items.append((50, 800 - 18 * i, size, bold, t))
            if dates:
                items.append((440, 800 - 18 * i, size, False, dates))
        make_pdf(self.tmp / "one_column.pdf", items)
        rec, full, pii = oscar(self.tmp / "one_column.pdf")[0]
        self.assertIn("Feb 2022 - Present", full)
        self.assertEqual(len(rec["experience"]), 2)
        self.assertEqual(pii.get("name"), "Sam Rivers")

    def test_skills_in_capitals_one_per_line_stay_in_the_skills_list(self):
        cv = "Ana Pires\nana@example.com\n\nEXPERIENCE\n" + "\n".join(x for h, b in JOBS for x in [h, *b, ""]) + \
             "\nSKILLS\nPython\n\nSQL\n\nAWS\n\nPHP\n\nDocker\n\nLANGUAGES\nEnglish\n"   # one per paragraph, as Word files come out
        (self.tmp / "caps.txt").write_text(cv)
        rec, full, _ = oscar(self.tmp / "caps.txt")[0]
        self.assertNotIn("[UNKNOWN", full)
        self.assertGreaterEqual(len(rec["skills_listed"]), 4)


class FrenchDates(unittest.TestCase):
    TODAY = date(2026, 10, 1)

    def ym(self, text):
        r = parse_range(text, self.TODAY)
        return r and (f"{r[0] // 12}-{r[0] % 12 + 1:02d}", f"{r[1] // 12}-{r[1] % 12 + 1:02d}")

    def test_french_months_and_present_words(self):
        self.assertEqual(self.ym("févr. 2022 - aujourd'hui"), ("2022-02", "2026-10"))
        self.assertEqual(self.ym("janv. 2019 - janv. 2022"), ("2019-01", "2022-01"))
        self.assertEqual(self.ym("juillet 2015 à décembre 2017"), ("2015-07", "2017-12"))
        self.assertEqual(self.ym("juin 2020 - en cours"), ("2020-06", "2026-10"))
        self.assertEqual(self.ym("août 2014 - présent"), ("2014-08", "2026-10"))

    def test_english_dates_are_unchanged(self):
        self.assertEqual(self.ym("Feb 2022 - Present"), ("2022-02", "2026-10"))
        self.assertEqual(self.ym("Sept 2018 – June 2020"), ("2018-09", "2020-06"))
        self.assertEqual(self.ym("2016 - 2019"), ("2016-01", "2019-12"))
        self.assertEqual(self.ym("03/2019 - 11/2021"), ("2019-03", "2021-11"))

    def test_a_french_cv_gets_its_years_of_experience(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fr.txt"
            p.write_text("Léa Martin\nlea@example.com\n\nEXPÉRIENCE PROFESSIONNELLE\n"
                         "Ingénieure Data  Lagoon  févr. 2022 - aujourd'hui\n- Pipelines Python\n\n"
                         "Analyste  Coral  janv. 2019 - janv. 2022\n- SQL\n\nCOMPÉTENCES\nPython, SQL\n", encoding="utf-8")
            rec, _, _ = oscar(p)[0]
        self.assertEqual(len(rec["experience"]), 2)
        self.assertIsNotNone(rec["total_years_experience"])


if __name__ == "__main__":
    unittest.main()
