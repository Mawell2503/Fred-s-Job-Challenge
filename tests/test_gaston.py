"""Gaston + report tests (no AI)."""
import os
import unittest

os.environ["LLM_BACKEND"] = "fake"

from hiring import llm                                         # noqa: E402
from hiring.citation_checker import quote_exists               # noqa: E402
from hiring.committee import orchestrator as O                 # noqa: E402
from hiring.committee.gaston import confidence, write_report   # noqa: E402
from hiring.committee.report import to_markdown                # noqa: E402
from helpers import make_packet, persona_of                    # noqa: E402

META = {"title": "Data engineer", "role_id": "R001", "applications": 42, "readable": 40, "passed_gate": 25,
        "read_by_jules": 20}
SECTIONS = ["# Hiring committee report", "## Top 5", "## Candidates", "**Strengths**", "**Areas for improvement**",
            "**What's missing**", "## Unresolved disagreements", "## Recommendation", "**Confidence:",
            "## Questions to ask in the interview", "## Appendix: positions before and after the debate"]


class GastonReport(unittest.TestCase):
    def tearDown(self):
        llm.set_fake_handler(None)

    def full_run(self):
        pk = make_packet(10, varied=True)
        st = O.run(pk)
        return pk, st, write_report(pk, st, META)

    def test_an_unresolved_candidate_outside_the_top_5_reaches_gaston_with_arguments(self):
        import json
        from hiring.committee.gaston import build_prompt
        from hiring.fake_llm import json_section
        pk = make_packet(10, varied=True)
        st = O.run(pk)
        sixth = st["ranking"][5]
        if sixth["id"] not in {u["id"] for u in st["unresolved"]}:   # make sure a 6th-ranked one is still open
            st["unresolved"].append({"id": sixth["id"], "scores": sixth["scores"], "verdicts": sixth["verdicts"],
                                     "spread": sixth["spread"]})
        prompt = build_prompt(pk, st, META, ("Medium", []))
        positions = json_section(prompt, "positions")
        self.assertIn(sixth["id"], positions)
        self.assertTrue(all(p["arguments"] for p in positions[sixth["id"]].values()))
        self.assertIn("Write the report for the five candidates", prompt)      # still only five written up
        rep = write_report(pk, st, META)
        d = next(x for x in rep["disagreements"] if x["id"] == sixth["id"])
        self.assertTrue(d["summary"])

    def test_every_required_section_is_there_and_every_strength_is_proven(self):
        pk, st, rep = self.full_run()
        md = to_markdown(rep)
        for s in SECTIONS:
            self.assertIn(s, md)
        self.assertEqual([t["id"] for t in rep["top5"]], st["top5"])
        self.assertEqual(rep["header"]["applications"], 42)
        for c in rep["candidates"]:
            self.assertEqual(len(c["strengths"]), 3, c)
            self.assertEqual(len(c["improvements"]), 2, c)
            for s in c["strengths"]:
                self.assertTrue(quote_exists(s["quote"], pk.sources[c["id"]][s["source"]]), s)
        self.assertEqual(len(rep["appendix"]), 10)
        self.assertEqual(rep["recommendation"]["confidence"] in ("High", "Medium", "Low"), True)
        # names only appear for the top 5
        self.assertTrue(all(t["name"].startswith("Person") for t in rep["top5"]))
        self.assertTrue(all(a["name"] == a["id"] for a in rep["appendix"] if a["id"] not in st["top5"]))

    def test_made_up_quotes_are_replaced_by_the_members_own_verified_words(self):
        pk = make_packet(10, varied=True)
        st = O.run(pk)
        calls = []

        def handler(system, prompt, schema):
            if persona_of(system) != "gaston":
                raise AssertionError("only Gaston should be called here")
            calls.append(1)
            ids = st["top5"]
            return {"top5": [{"id": c, "comment": "ok", "missing": [], "improvements": ["a", "b"],
                              "strengths": [{"point": "x", "quote": "an invented achievement nobody wrote", "source": "cv"}] * 3}
                             for c in ids],
                    "disagreements": [], "recommendation": "Interview them.",
                    "interview_questions": [{"id": c, "questions": ["Why?"]} for c in ids]}
        llm.set_fake_handler(handler)
        rep = write_report(pk, st, META)
        self.assertEqual(len(calls), 2)                                    # one re-prompt, then filled by code
        self.assertTrue(any("strengths" in f for f in rep["notes"]["filled_by_code"]))
        for c in rep["candidates"]:
            for s in c["strengths"]:
                self.assertNotIn("invented", s["quote"])
                self.assertTrue(quote_exists(s["quote"], pk.sources[c["id"]][s["source"]]))

    def test_report_is_still_written_when_the_ai_is_down(self):
        pk = make_packet(10, varied=True)
        st = O.run(pk)

        def down(system, prompt, schema):
            raise llm.LLMError("offline")
        llm.set_fake_handler(down)
        rep = write_report(pk, st, META)
        self.assertFalse(rep["notes"]["written_by_ai"])
        self.assertTrue(rep["recommendation"]["text"])
        self.assertIn("## Recommendation", to_markdown(rep))


class Confidence(unittest.TestCase):
    def state(self, unresolved_lead=False, verdicts=("advance", "advance", "advance"), spread=1, margin=1.0):
        lead = {"id": "A", "score": 8, "spread": spread, "verdicts": dict(zip(("helene", "victor", "nina"), verdicts)),
                "scores": {}}
        second = {"id": "B", "score": 8 - margin, "spread": 1, "verdicts": {}, "scores": {}}
        return {"ranking": [lead, second], "unresolved": [{"id": "A"}] if unresolved_lead else [],
                "unreliable": [], "members": ["helene", "victor", "nina"], "skipped": {}}

    def test_levels(self):
        pk = make_packet(2)
        self.assertEqual(confidence(self.state(), pk)[0], "High")
        self.assertEqual(confidence(self.state(margin=0.2), pk)[0], "Medium")
        self.assertEqual(confidence(self.state(spread=4), pk)[0], "Medium")
        self.assertEqual(confidence(self.state(unresolved_lead=True), pk)[0], "Low")

    def test_a_medium_level_never_lists_a_strength_as_a_reason_for_doubt(self):
        level, reasons = confidence(self.state(margin=0.2), make_packet(2))
        self.assertEqual(level, "Medium")
        good = [r for r in reasons if "says 'advance'" in r]
        self.assertTrue(good and all(r.startswith("in its favour:") for r in good), reasons)


if __name__ == "__main__":
    unittest.main()
