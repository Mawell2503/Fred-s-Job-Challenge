"""Jules tests: python -m unittest discover -s tests   (no AI, no cost).

What they guard: an answer that skips criteria (or names them oddly) is never turned silently into a
low score. It gets one re-prompt, and anything still missing is reported."""
import os
import unittest

os.environ["LLM_BACKEND"] = "fake"

from hiring import jules, llm                              # noqa: E402
from hiring.criteria import clean_criteria                 # noqa: E402

JOB = clean_criteria({"title": "Data engineer",
                      "must_haves": [{"text": "Python", "type": "skill", "keywords": ["python"]},
                                     {"text": "SQL", "type": "skill", "keywords": ["sql"]}],
                      "nice_to_haves": [{"text": "Airflow", "type": "skill", "keywords": ["airflow"]}]})
CV = "[EXPERIENCE]\nData Engineer  Acme  2019 - Present\n- Built pipelines in Python and SQL\n- Ran Airflow for the finance team\n"
CAND = {"candidate_id": "C0001", "full_text": CV}
QUOTES = {"M1": "Built pipelines in Python and SQL", "M2": "Built pipelines in Python and SQL",
          "N1": "Ran Airflow for the finance team"}


def met(cid_or_label):
    cid = cid_or_label.split()[0].strip(":").upper()
    return {"id": cid_or_label, "verdict": "met", "quotes": [QUOTES[cid]], "reason": "shown"}


def scripted(*answers):
    """Return the given answers one call at a time (the last one repeats)."""
    calls = []

    def handler(system, prompt, schema):
        calls.append(prompt)
        return answers[min(len(calls), len(answers)) - 1]
    handler.calls = calls
    return handler


class JulesAnswers(unittest.TestCase):
    def tearDown(self):
        llm.set_fake_handler(None)

    def test_a_complete_answer_is_scored_without_a_reprompt(self):
        h = scripted({"criteria": [met("M1"), met("M2"), met("N1")], "overall": "ok"})
        llm.set_fake_handler(h)
        r = jules.assess(JOB, CAND)
        self.assertEqual((r["score"], r["max_score"]), (3, 3))
        self.assertEqual(len(h.calls), 1)
        self.assertEqual(r["incomplete"], [])

    def test_ids_written_differently_still_count(self):
        h = scripted({"criteria": [met("m1"), met("M2 (MUST-HAVE)"), met("N1:")], "overall": "ok"})
        llm.set_fake_handler(h)
        r = jules.assess(JOB, CAND)
        self.assertEqual(r["score"], 3)
        self.assertEqual(len(h.calls), 1)

    def test_skipped_criteria_get_one_reprompt_that_names_them(self):
        h = scripted({"criteria": [met("M1")], "overall": "partial"},
                     {"criteria": [met("M1"), met("M2"), met("N1")], "overall": "full"})
        llm.set_fake_handler(h)
        r = jules.assess(JOB, CAND)
        self.assertEqual(len(h.calls), 2)
        self.assertIn("did not cover: M2, N1", h.calls[1])
        self.assertEqual(r["score"], 3)
        self.assertTrue(r["reprompted"])
        self.assertEqual(r["incomplete"], [])

    def test_an_empty_answer_is_reported_never_silent(self):
        llm.set_fake_handler(scripted({"criteria": [], "overall": ""}))
        r = jules.assess(JOB, CAND)
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["incomplete"], ["M1", "M2", "N1"])
        out = jules.run(JOB, {"shortlist": [{"candidate_id": "C0001", "score": 0.5, "gate": []}]},
                        lambda cid: CAND, top=10)
        self.assertTrue(any("skipped M1, M2, N1" in w for w in out["warnings"]), out["warnings"])

    def test_a_zero_next_to_remi_passes_is_read_again_and_flagged(self):
        h = scripted({"criteria": [dict(met("M1"), verdict="not_mentioned", quotes=[]),
                                   dict(met("M2"), verdict="not_mentioned", quotes=[]),
                                   dict(met("N1"), verdict="not_mentioned", quotes=[])], "overall": "nothing"},
                     {"criteria": [met("M1"), met("M2"), met("N1")], "overall": "good"})
        llm.set_fake_handler(h)
        shortlist = [{"candidate_id": "C0001", "score": 0.7, "gate": [{"id": "M1", "status": "PASS"}]}]
        out = jules.run(JOB, {"shortlist": shortlist}, lambda cid: CAND, top=10)
        f = out["finalists"][0]
        self.assertEqual(f["score"], 3)                  # the better of the two reads is kept
        self.assertTrue(f["reread"])
        self.assertEqual(out["counts"]["reread"], 1)
        self.assertEqual(out["warnings"], [])            # fixed by the re-read: nothing left to flag

    def test_others_keep_their_verdicts_for_checking(self):
        llm.set_fake_handler(scripted({"criteria": [met("M1"), met("M2"), met("N1")], "overall": "ok"}))
        shortlist = [{"candidate_id": f"C000{i}", "score": 1 - i / 10, "gate": []} for i in (1, 2)]
        out = jules.run(JOB, {"shortlist": shortlist},
                        lambda cid: {"candidate_id": cid, "full_text": CV}, top=1)
        self.assertEqual([c["verdict"] for c in out["others"][0]["criteria"]], ["met", "met", "met"])


if __name__ == "__main__":
    unittest.main()
