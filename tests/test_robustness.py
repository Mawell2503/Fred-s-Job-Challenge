"""Regression tests for failures found in a full audit: bad data or a misbehaving AI must never
crash a run. python -m unittest discover -s tests   (no extra dependency, no AI, no cost)."""
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

os.environ["LLM_BACKEND"] = "fake"

from hiring import llm                                   # noqa: E402
from hiring.committee import gaston as G                 # noqa: E402
from hiring.committee import orchestrator as O           # noqa: E402
from hiring.committee.packet import practical_checks     # noqa: E402
from hiring.remi import CandidateView, check             # noqa: E402
from helpers import LOGISTICS, ids_of, make_packet, persona_of  # noqa: E402

ANSWER = {"salary": {"amount": 4000, "period": "month", "currency": "EUR"}, "available_from": "2026-11-15",
          "location": {"country": "Mauritius", "city": ""}, "relocate": "yes", "travel": "occasional",
          "arrangement": "hybrid", "work_countries": ["Mauritius"], "languages": [{"language": "French", "level": "C2"}]}


def by(checks, name):
    return next(c for c in checks if c["check"] == name)


class NinaChecksNeverCrash(unittest.TestCase):
    def test_impossible_start_date_is_unknown_not_a_crash(self):
        # "2026-02-31" was accepted by the form before it checked for real days: it must not stop the committee
        for p, l in (({**ANSWER, "available_from": "2026-02-31"}, LOGISTICS),
                     (ANSWER, {**LOGISTICS, "start_date": "2026-13-01"})):
            out = practical_checks(p, l)
            self.assertEqual(by(out, "start date")["status"], "unknown")

    def test_real_dates_still_compared(self):
        self.assertEqual(by(practical_checks(ANSWER, LOGISTICS), "start date")["status"], "ok")
        late = practical_checks({**ANSWER, "available_from": "2027-03-01"}, LOGISTICS)
        self.assertEqual(by(late, "start date")["status"], "blocker")

    def test_odd_levels_and_incomplete_requirements(self):
        l = {**LOGISTICS, "languages": [{"language": "French", "min_level": "C1"}, {"language": "", "min_level": "B2"},
                                        {"language": "English", "min_level": "fluent"}]}
        out = practical_checks({**ANSWER, "languages": [{"language": "French", "level": "expert"}]}, l)
        langs = [c for c in out if c["check"].startswith("language")]
        self.assertEqual([(c["check"], c["status"]) for c in langs], [("language: French", "unknown")])

    def test_country_spelling_does_not_matter_for_location(self):
        out = practical_checks({**ANSWER, "location": {"country": " mauritius "}, "relocate": "no"}, LOGISTICS)
        self.assertFalse(any(c["check"] == "location" for c in out))


class UnscoredCandidates(unittest.TestCase):
    def tearDown(self):
        llm.set_fake_handler(None)

    def test_a_candidate_nobody_can_score_is_left_out_with_a_warning(self):
        pk = make_packet(6)
        broken = "C0003"

        def handler(system, prompt, schema):
            who, ids = persona_of(system), ids_of(prompt)
            src = "practical" if who == "nina" else "cv"
            out = [{"id": c, "score": "n/a" if c == broken else 7, "verdict": "advance",
                    "arguments": [{"point": "p", "quote": f"Line for {c} in {src}", "source": src}], "concerns": []}
                   for c in ids]
            return {"candidates": out, "top5": [c for c in ids if c != broken][:5]}
        llm.set_fake_handler(handler)
        state = O.run(pk, max_rounds=1)
        self.assertNotIn(broken, [r["id"] for r in state["ranking"]])
        self.assertTrue(any(broken in w for w in state["warnings"]))
        llm.set_fake_handler(None)                       # Gaston: the built-in fake
        report = G.write_report(pk, state, {"title": "Data engineer"})     # formats every score: must not crash
        self.assertEqual(len(report["top5"]), 5)


class GastonMalformedAnswer(unittest.TestCase):
    def test_non_object_items_are_reported_not_fatal(self):
        pk = make_packet(6)
        state = O.run(pk, max_rounds=0)
        answer = {"top5": ["C0001", {"id": "C0002", "comment": "ok", "strengths": ["just text"], "improvements": []}],
                  "disagreements": [], "recommendation": "Hire", "interview_questions": []}
        written, errors = G.check(answer, pk, state)
        self.assertTrue(any("must be an object" in e for e in errors))
        self.assertIn("C0002", written["by_id"])


class RemiDegreeCheck(unittest.TestCase):
    def test_empty_education_entry(self):
        cand = {"candidate_id": "C0001", "text": "[EDUCATION]\nBSc\n", "full_text": "",
                "record": {"sections": {"education": "BSc Biology"}, "education": ["", "BSc Biology\n2015"]}}
        out = check({"id": "M1", "type": "degree", "keywords": ["master"]}, CandidateView(cand))
        self.assertEqual(out["status"], "FAIL")
        self.assertIn("BSc Biology", out["evidence"])


class TransientLLMErrors(unittest.TestCase):
    """A rate-limited or overloaded API is waited for; a bad request is not."""
    SCHEMA = {"title": "x", "required": ["ok"]}

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"LLM_BACKEND": "api", "ANTHROPIC_API_KEY": "test"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def run_with(self, codes):
        calls = []

        class Ok:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"content": [{"type": "tool_use", "input": {"ok": true}}]}'

        def urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) <= len(codes):
                raise urllib.error.HTTPError(req.full_url, codes[len(calls) - 1], "busy", {"retry-after": "1"}, None)
            return Ok()
        with mock.patch("urllib.request.urlopen", urlopen), mock.patch.object(llm.time, "sleep") as sleep:
            try:
                out = llm.complete_json("s", "p", self.SCHEMA)
            except llm.LLMError as e:
                out = e
        return out, len(calls), sleep

    def test_overloaded_api_is_waited_for(self):
        out, calls, sleep = self.run_with([529, 429])
        self.assertEqual(out, {"ok": True})
        self.assertEqual(calls, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.0, 1.0])     # the server's retry-after

    def test_bad_request_is_not_waited_for(self):
        out, calls, sleep = self.run_with([400, 400, 400])
        self.assertIsInstance(out, llm.LLMError)
        self.assertEqual(sleep.call_count, 0)
        self.assertEqual(calls, 2)                        # the normal single retry, nothing more

    def test_waiting_has_a_limit(self):
        out, calls, sleep = self.run_with([503] * 20)
        self.assertIsInstance(out, llm.LLMError)
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(calls, 5)                        # 3 waits + the normal retry


@unittest.skipUnless(__import__("importlib").util.find_spec("pypdf"), "pypdf not installed")
class PasswordProtectedPdf(unittest.TestCase):
    def test_reported_as_password_protected(self):
        from pypdf import PdfWriter
        from cv_reader.config import Config
        from cv_reader.loaders import LoaderError, load
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "locked.pdf"
            w = PdfWriter()
            w.add_blank_page(200, 200)
            w.encrypt("secret")
            w.write(p)
            with self.assertRaises(LoaderError) as ctx:
                load(p, Config())
            self.assertEqual(ctx.exception.code, "PASSWORD_PROTECTED")


if __name__ == "__main__":
    unittest.main()
