"""Committee tests: python -m unittest discover -s tests   (no extra dependency, no AI, no cost)."""
import json
import os
import unittest

os.environ["LLM_BACKEND"] = "fake"

from hiring import llm                                   # noqa: E402
from hiring.committee import orchestrator as O           # noqa: E402
from helpers import ids_of, make_packet, persona_of, round_of  # noqa: E402
from hiring.committee.packet import career_timeline, practical_checks  # noqa: E402


def scripted(table, bad=None):
    """Handler answering from table[persona][round][cid] = score (int). `bad` = {(persona, round, attempt)}
    for answers that must quote something that is not in the CV."""
    calls = []

    def handler(system, prompt, schema):
        who, r, ids = persona_of(system), round_of(prompt), ids_of(prompt)
        attempt = 1 + sum(1 for c in calls if c[:2] == (who, r))
        calls.append((who, r, attempt))
        wrong = bad and (who, r, attempt) in bad
        out = []
        for cid in ids:
            s = table[who][min(r, max(table[who]))][cid]
            src = "practical" if who == "nina" else "cv"
            quote = "this sentence is not in any CV at all" if wrong else f"Line for {cid} in {src}"
            out.append({"id": cid, "score": s, "verdict": "advance" if s >= 6 else "reject" if s <= 3 else "maybe",
                        "arguments": [{"point": "p", "quote": quote, "source": src}], "concerns": [], "reply": "r"})
        return {"candidates": out, "top5": sorted(ids, key=lambda c: -table[who][min(r, max(table[who]))][c])[:5] if r == 0 else []}
    handler.calls = calls
    return handler


def flat(score_by_member, ids, rounds=1):
    return {m: {r: {c: s for c in ids} for r in range(rounds)} for m, s in score_by_member.items()}


class OrchestratorRules(unittest.TestCase):
    def tearDown(self):
        llm.set_fake_handler(None)

    def run_with(self, table, n=6, bad=None, practical=True):
        pk = make_packet(n, practical=practical)
        h = scripted(table, bad)
        llm.set_fake_handler(h)
        return O.run(pk), h

    def test_consensus_stops_before_any_debate(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        st, h = self.run_with(flat({"helene": 7, "victor": 7, "nina": 7}, ids))
        self.assertEqual(st["stop_reason"], "consensus")
        self.assertEqual(st["calls"], 3)
        self.assertEqual(len(st["rounds"]), 1)

    def test_nina_is_not_launched_without_practical_material(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        st, h = self.run_with(flat({"helene": 7, "victor": 7, "nina": 7}, ids), practical=False)
        self.assertEqual(st["members"], ["helene", "victor"])
        self.assertIn("nina", st["skipped"])
        self.assertNotIn("nina", {c[0] for c in h.calls})

    def test_deadlock_stops_and_keeps_the_disagreement_open(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        t = flat({"helene": 7, "victor": 7, "nina": 7}, ids, rounds=2)
        for r in (0, 1):
            t["helene"][r]["C0001"], t["victor"][r]["C0001"] = 2, 9
        st, h = self.run_with(t)
        self.assertEqual(st["stop_reason"], "deadlock")
        # the two ends of the conflict, plus Nina (in the middle) once, in this first debate round
        self.assertEqual([c[:2] for c in h.calls if c[1] == 1], [("helene", 1), ("victor", 1), ("nina", 1)])
        self.assertIn("C0001", [u["id"] for u in st["unresolved"]])

    def test_debate_never_goes_beyond_three_rounds(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        t = flat({"helene": 7, "victor": 7, "nina": 7}, ids, rounds=4)
        for r in range(4):                      # they move a point each round but stay apart
            t["helene"][r]["C0001"], t["victor"][r]["C0001"] = 1 + r, 10 - r
        st, h = self.run_with(t)
        self.assertEqual(st["stop_reason"], "max_rounds")
        self.assertEqual(max(c[1] for c in h.calls), 3)
        self.assertEqual(len(st["rounds"]), 4)

    def test_a_nonsense_answer_is_reprompted_once(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        st, h = self.run_with(flat({"helene": 7, "victor": 7, "nina": 7}, ids), bad={("helene", 0, 1)})
        self.assertEqual(st["reprompts"], 1)
        self.assertEqual(st["unreliable"], [])
        self.assertEqual([c for c in h.calls if c[0] == "helene"], [("helene", 0, 1), ("helene", 0, 2)])

    def test_nonsense_twice_drops_the_invalid_parts(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        st, h = self.run_with(flat({"helene": 7, "victor": 7, "nina": 7}, ids),
                              bad={("victor", 0, 1), ("victor", 0, 2)})
        self.assertEqual(st["unreliable"][0]["member"], "victor")
        self.assertTrue(all(not p["arguments"] for p in st["positions"]["victor"].values()))

    def test_victor_is_launched_to_rescue_a_candidate_helene_dismissed(self):
        pos = {"helene": {"C1": {"score": 2, "verdict": "reject"}},
               "victor": {"C1": {"score": 5, "verdict": "maybe"}},
               "nina": {"C1": {"score": 7, "verdict": "advance"}}}
        plan = O.assignments(pos, ["C1"])
        self.assertEqual(set(plan), {"helene", "victor", "nina"})
        self.assertIn("rescue", plan["victor"]["C1"])

    def test_the_member_in_the_middle_is_heard_once_then_only_the_two_ends(self):
        pos = {"helene": {"C1": {"score": 7, "verdict": "advance"}},
               "victor": {"C1": {"score": 5, "verdict": "maybe"}},
               "nina": {"C1": {"score": 1, "verdict": "reject"}}}
        first = O.assignments(pos, ["C1"], heard=set())
        self.assertEqual(set(first), {"helene", "victor", "nina"})
        self.assertIn("middle", first["victor"]["C1"])
        later = O.assignments(pos, ["C1"], heard={("helene", "C1"), ("victor", "C1"), ("nina", "C1")})
        self.assertEqual(set(later), {"helene", "nina"})

    def test_in_a_full_debate_the_middle_member_speaks_in_the_first_round_only(self):
        ids = [f"C{i:04d}" for i in range(1, 7)]
        table = flat({"helene": 7, "victor": 5, "nina": 1}, ids, rounds=4)     # nobody ever moves
        st, h = self.run_with(table)
        victor_rounds = sorted({r for who, r, _ in h.calls if who == "victor" and r > 0})
        self.assertEqual(victor_rounds, [1])
        self.assertEqual(st["stop_reason"], "deadlock")

    def test_top5_is_the_average_with_jules_then_remi_breaking_ties(self):
        pos = {"helene": {"A": {"score": 6, "verdict": "advance"}, "B": {"score": 6, "verdict": "advance"},
                          "C": {"score": 9, "verdict": "advance"}},
               "victor": {"A": {"score": 8, "verdict": "advance"}, "B": {"score": 8, "verdict": "advance"},
                          "C": {"score": 1, "verdict": "reject"}}}
        rows = O.rank(pos, ["A", "B", "C"], {"A": {"jules": 0.5, "remi": 0.9}, "B": {"jules": 0.8, "remi": 0.1},
                                             "C": {"jules": 1.0, "remi": 1.0}})
        self.assertEqual([r["id"] for r in rows], ["B", "A", "C"])
        self.assertEqual(rows[0]["score"], 7.0)

    def test_the_fake_backend_runs_a_whole_committee_the_same_way_twice(self):
        llm.set_fake_handler(None)
        a = O.run(make_packet(10, varied=True))
        b = O.run(make_packet(10, varied=True))
        self.assertEqual(a["top5"], b["top5"])
        self.assertEqual(a["reprompts"], 0, a["unreliable"])
        self.assertEqual(len(a["top5"]), 5)


class PracticalChecks(unittest.TestCase):
    role = {"currency": "EUR", "budget_max": 5000, "budget_period": "month", "start_date": "2026-12-01",
            "work_country": "France", "arrangement": "onsite", "travel": "occasional",
            "languages": [{"language": "French", "min_level": "C1"}, {"language": "English", "min_level": "B2"}]}

    def answers(self, **kw):
        p = {"salary": {"amount": 5000, "period": "month", "currency": "EUR"}, "available_from": "2026-11-20",
             "location": {"country": "France"}, "relocate": "no", "travel": "occasional", "arrangement": "onsite",
             "work_authorization": "yes", "languages": [{"language": "Français", "level": "C2"},
                                                        {"language": "English", "level": "B2"}]}
        p.update(kw)
        return {c["check"]: c["status"] for c in practical_checks(p, self.role)}

    def test_all_fine(self):
        self.assertEqual(set(self.answers().values()), {"ok"})

    def test_salary(self):
        self.assertEqual(self.answers(salary={"amount": 5300, "period": "month"})["salary vs budget"], "risk")
        self.assertEqual(self.answers(salary={"amount": 72000, "period": "year"})["salary vs budget"], "blocker")

    def test_languages_match_across_spellings_and_levels(self):
        self.assertEqual(self.answers(languages=[{"language": "French", "level": "B2"}])["language: French"], "risk")
        self.assertEqual(self.answers(languages=[{"language": "French", "level": "A2"}])["language: French"], "blocker")
        self.assertEqual(self.answers(languages=[{"language": "French", "level": "C1"}])["language: English"], "blocker")

    def test_location_start_travel_and_right_to_work(self):
        r = self.answers(location={"country": "Mauritius"}, relocate="no", available_from="2027-03-01",
                         travel="none", work_authorization="sponsorship")
        self.assertEqual((r["location"], r["start date"], r["travel"], r["right to work"]),
                         ("blocker", "blocker", "risk", "risk"))


class CareerTimeline(unittest.TestCase):
    def test_change_of_field_and_same_role_for_years(self):
        rec = {"total_years_experience": 9, "experience": [
            {"title": "Nurse | City Hospital", "start": "2015-01", "end": "2020-12", "months": 72},
            {"title": "Data Analyst | Acme", "start": "2021-03", "end": "2023-12", "months": 34}]}
        facts = " ".join(career_timeline(rec)["facts"])
        self.assertIn("Changed field", facts)
        self.assertIn("Same role (Nurse", facts)


class FeedAndLive(unittest.TestCase):
    """What the committee page draws from: feed lines with candidate ids and scores, and live.json."""

    def tearDown(self):
        llm.set_fake_handler(None)

    def test_feed_and_live_file_follow_the_debate(self):
        import tempfile
        from pathlib import Path
        from hiring.committee.live import Live
        ids = [f"C{i:04d}" for i in range(1, 7)]
        t = flat({"helene": 7, "victor": 7, "nina": 7}, ids, rounds=3)
        t["helene"][0]["C0001"], t["victor"][0]["C0001"] = 2, 9
        t["helene"][1]["C0001"] = t["helene"][2]["C0001"] = 6        # Hélène moves up in round 1
        pk = make_packet(6)
        seen = []
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "live.json"

            class Spy(Live):
                def update(self, **fields):
                    super().update(**fields)
                    seen.append(json.loads(path.read_text(encoding="utf-8")))
            llm.set_fake_handler(scripted(t))
            st = O.run(pk, live=Spy(path, pk))
            final = json.loads(path.read_text(encoding="utf-8"))

        feed = st["feed"]
        # round 0: one scored line per member and candidate, with the evidence
        scored = [f for f in feed if f["kind"] == "score"]
        self.assertEqual(len(scored), 3 * 6)
        self.assertTrue(all(f["cid"] in ids and "score" in f and f["arg"]["quote"] for f in scored))
        # the debate: the move carries from / to
        move = next(f for f in feed if f["kind"] == "change")
        self.assertEqual((move["member"], move["cid"], move["from"], move["to"]), ("helene", "C0001", 2, 6))
        self.assertIn("C0001", next(f for f in feed if f["kind"] == "decision" and f["round"] == 1)["cids"])
        # live.json: who speaks while they speak, the facts of each finalist, the final ranking
        self.assertIn("helene", [s["speaking"] for s in seen])
        self.assertTrue(any(s["phase"] == "debate" and "C0001" in s["contested"] for s in seen))
        self.assertEqual(final["phase"], "report")
        self.assertEqual(set(final["candidates"]), set(ids))
        self.assertIn("checks", final["candidates"]["C0001"])
        self.assertEqual([r["id"] for r in final["ranking"]][:5], st["top5"])
        self.assertEqual(final["positions"]["helene"]["C0001"]["score"], 6)


if __name__ == "__main__":
    unittest.main()
