"""The CV pool on the committee side: what Nina is told about what each finalist applied for, salaries in
another currency, the countries a candidate can work in, the optional tie-break, and a whole fake committee
run with labels. No AI (LLM_BACKEND=fake)."""
import os
import unittest

os.environ["LLM_BACKEND"] = "fake"

from hiring import llm                                                        # noqa: E402
from hiring.committee import orchestrator as O                                # noqa: E402
from hiring.committee.gaston import write_report                              # noqa: E402
from hiring.committee.packet import (application_label, application_line, build, practical_checks,  # noqa: E402
                                     practical_text)
from hiring.committee.report import to_markdown                               # noqa: E402
from helpers import LOGISTICS                                                 # noqa: E402

THIS, OPEN, OTHER = {"kind": "this"}, {"kind": "open"}, {"kind": "other", "roles": ["Backend Developer"]}


def answers(**kw):
    p = {"salary": {"amount": 4500, "period": "month", "currency": "EUR"}, "available_from": "2026-11-20",
         "location": {"country": "Mauritius"}, "relocate": "yes", "travel": "occasional", "arrangement": "hybrid",
         "work_countries": ["Mauritius"], "languages": [{"language": "French", "level": "C2"}]}
    p.update(kw)
    return p


def statuses(p, application=None, logistics=LOGISTICS):
    return {c["check"]: c["status"] for c in practical_checks(p, logistics, application)}


class ApplicationLabel(unittest.TestCase):
    def test_each_kind_is_a_quotable_line_and_a_check(self):
        self.assertEqual(statuses(answers(), THIS)["application"], "ok")
        self.assertEqual(statuses(answers(), OPEN)["application"], "unknown")
        self.assertEqual(statuses(answers(), OTHER)["application"], "risk")
        for a in (THIS, OPEN, OTHER):
            line = application_line(a)
            self.assertIn(line, practical_text(answers(), "Mauritius", a).splitlines())
        self.assertIn("Backend Developer", application_line(OTHER))
        self.assertEqual(application_label(OPEN), "Open application (did not choose a position)")

    def test_no_label_when_not_known_or_direct_only(self):
        self.assertNotIn("application", statuses(answers()))
        self.assertNotIn("Application:", practical_text(answers(), "Mauritius"))
        self.assertEqual(application_label(None), "")

    def test_the_label_still_reaches_nina_without_practical_answers(self):
        self.assertIn("Application: open application", practical_text(None, "", OPEN))
        self.assertEqual(statuses(None, OPEN)["application"], "unknown")

    def test_a_cv_uploaded_in_a_batch_is_neither_an_application_nor_an_open_one(self):
        batch = {"kind": "batch", "batch": "Client X · 31 Oct"}
        text = practical_text(None, "Mauritius", batch)             # uploaded CVs have no practical answers
        self.assertIn("Application: uploaded in batch Client X · 31 Oct (no application form)", text.splitlines())
        self.assertIn("No practical details were provided.", text)
        self.assertEqual(statuses(None, batch)["application"], "unknown")
        self.assertEqual(application_label(batch), "Uploaded in batch Client X · 31 Oct (no application form)")


class CurrencyAndWorkCountries(unittest.TestCase):
    def test_a_salary_in_another_currency_is_not_compared(self):
        r = practical_checks(answers(salary={"amount": 400000, "period": "month", "currency": "INR"}), LOGISTICS)
        sal = next(c for c in r if c["check"] == "salary vs budget")
        self.assertEqual(sal["status"], "unknown")
        self.assertIn("INR", sal["detail"])
        self.assertEqual(statuses(answers(salary={"amount": 6000, "period": "month", "currency": "EUR"}))["salary vs budget"],
                         "blocker")

    def test_countries_they_can_work_in(self):
        self.assertEqual(statuses(answers(work_countries=["mauritius ", "France"]))["right to work"], "ok")
        self.assertEqual(statuses(answers(work_countries=["India"]))["right to work"], "risk")
        self.assertEqual(statuses(answers(), logistics={**LOGISTICS, "work_country": ""})["right to work"], "unknown")
        self.assertIn("Can work without a visa in: Mauritius", practical_text(answers(), "Mauritius"))

    def test_answers_from_before_the_pool_still_work(self):
        old = answers(work_authorization="sponsorship")
        del old["work_countries"]
        self.assertEqual(statuses(old)["right to work"], "risk")
        self.assertIn("Right to work in Mauritius: needs sponsorship", practical_text(old, "Mauritius"))


class TieBreak(unittest.TestCase):
    POS = {"helene": {"A": {"score": 7, "verdict": "advance"}, "B": {"score": 7, "verdict": "advance"}},
           "victor": {"A": {"score": 7, "verdict": "advance"}, "B": {"score": 7, "verdict": "advance"}}}
    SCORES = {"A": {"jules": 0.9, "remi": 0.9, "direct": 0}, "B": {"jules": 0.5, "remi": 0.5, "direct": 1}}

    def test_off_by_default_jules_breaks_the_tie(self):
        self.assertEqual([r["id"] for r in O.rank(self.POS, ["A", "B"], self.SCORES)], ["A", "B"])

    def test_on_the_direct_applicant_ranks_first_among_equals_only(self):
        self.assertEqual([r["id"] for r in O.rank(self.POS, ["A", "B"], self.SCORES, True)], ["B", "A"])
        pos = {m: {**p, "A": {"score": 8, "verdict": "advance"}} for m, p in self.POS.items()}
        self.assertEqual([r["id"] for r in O.rank(pos, ["A", "B"], self.SCORES, True)], ["A", "B"])   # not a tie


class WholeCommitteeWithLabels(unittest.TestCase):
    def setUp(self):
        llm.set_fake_handler(None)

    def run_committee(self, tie_break=False):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        cvs = {cid: {"full_text": f"[CANDIDATE_ID: {cid}]\n\n[EXPERIENCE]\nData Engineer  Acme  Jan 2016 - Present\n"
                                 f"- Built ETL pipelines in Python and SQL ({cid})\n",
                     "record": {"total_years_experience": 8, "experience": [
                         {"title": "Data Engineer | Acme", "start": "2016-01", "end": None, "months": 100}]}}
               for cid in ids}
        jules = {"finalists": [{"candidate_id": cid, "rank": i + 1, "score": 3, "max_score": 3, "remi_score": 0.5,
                                "criteria": [{"id": "M1", "requirement": "Python", "verdict": "met",
                                              "cv_quotes": ["Built ETL pipelines in Python and SQL"]}],
                                "list_missing": [], "personal": {"name": f"Person {i}"}} for i, cid in enumerate(ids)]}
        kinds = [THIS, OPEN, OTHER]
        cin = {"logistics": LOGISTICS, "practical": {cid: answers() for cid in ids}, "linkedin": {},
               "application": {cid: kinds[i % 3] for i, cid in enumerate(ids)}, "tie_break_direct": tie_break}
        job = {"title": "Data engineer", "must_haves": [{"id": "M1", "text": "Python"}], "nice_to_haves": []}
        pk = build(job, jules, lambda cid: cvs[cid], cin)
        return pk, O.run(pk)

    def test_nina_sits_reads_the_label_and_the_report_shows_it(self):
        pk, state = self.run_committee()
        self.assertIn("nina", state["members"])
        self.assertEqual(state["reprompts"], 0, state["unreliable"])
        nina_material = pk.material("nina", pk.ids)
        self.assertIn("Application: applied for another position (Backend Developer)", nina_material)
        self.assertNotIn("Application:", pk.material("helene", pk.ids))      # Hélène and Victor stay on the CV
        self.assertNotIn("Application:", pk.material("victor", pk.ids))
        report = write_report(pk, state, {"title": "Data engineer", "role_id": "R001"})
        labels = {c["id"]: c["application"] for c in report["candidates"]}
        self.assertTrue(all(labels.values()), labels)
        md = to_markdown(report)
        self.assertIn("| Application |", md)
        for c in report["candidates"]:                                          # never quoted as a strength
            self.assertFalse(any(s["quote"].startswith("Application:") for s in c["strengths"]))

    def test_the_name_on_the_application_names_the_report_but_never_reaches_the_ai(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        cvs = {cid: {"full_text": f"[CANDIDATE_ID: {cid}]\n\n[EXPERIENCE]\n- Built ETL pipelines in Python ({cid})\n",
                     "record": {}} for cid in ids}
        jules = {"finalists": [{"candidate_id": cid, "rank": i + 1, "score": 1, "max_score": 1, "criteria": [],
                                "list_missing": [], "personal": {"name": "" if i == 0 else f"Oscar Name {i}"}}
                               for i, cid in enumerate(ids)]}
        cin = {"logistics": LOGISTICS, "practical": {}, "linkedin": {},
               "names": {"C0001": "Test subject #0001", "C0002": "Given Name"}}
        pk = build({"title": "Data engineer", "must_haves": [], "nice_to_haves": []}, jules, lambda cid: cvs[cid], cin)
        self.assertEqual(pk.names["C0001"], "Test subject #0001")   # no name in the CV: the one from the import
        self.assertEqual(pk.names["C0002"], "Given Name")           # the applicant's own name wins
        self.assertEqual(pk.names["C0003"], "Oscar Name 2")         # nothing given: the one Oscar found
        for m in ("helene", "victor", "nina"):
            material = pk.material(m, pk.ids)
            self.assertNotIn("Given Name", material)
            self.assertNotIn("Test subject", material)

    def test_with_the_tie_break_direct_applicants_lead_equal_scores(self):
        _, state = self.run_committee(tie_break=True)
        ranking = state["ranking"]
        for a, b in zip(ranking, ranking[1:]):
            if a["score"] == b["score"]:
                self.assertGreaterEqual(a["direct"], b["direct"])


if __name__ == "__main__":
    unittest.main()
