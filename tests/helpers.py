"""Shared test fixtures: committee packets built in memory (no files, no Oscar run)."""
import json

from hiring.committee.packet import Packet, career_timeline, practical_checks, practical_text
from hiring.fake_llm import json_section, section

LOGISTICS = {"currency": "EUR", "budget_min": 3500, "budget_max": 5000, "budget_period": "month",
             "client_country": "France", "work_country": "Mauritius", "arrangement": "hybrid", "travel": "occasional",
             "start_date": "2026-12-01", "languages": [{"language": "French", "min_level": "C1"}], "notes": ""}


def persona_of(system):
    for key, name in (("HÉLÈNE", "helene"), ("VICTOR", "victor"), ("NINA", "nina")):
        if f"You are {key}" in system:
            return name
    return "gaston"


def round_of(prompt):
    return int(section(prompt, "round") or 0)


def ids_of(prompt):
    return [c["id"] for c in json_section(prompt, "candidates")]


def _cv(cid, i, varied):
    if not varied:
        return f"[CANDIDATE_ID: {cid}]\n\n[EXPERIENCE]\nLine for {cid} in cv\n"
    jobs = [("Nurse  City Hospital  Jan 2012 - Dec 2018", "Data Analyst  Acme  Mar 2019 - Present"),
            ("Data Engineer  Beta  Jan 2016 - Present", "Junior Data Engineer  Gamma  Jan 2014 - Dec 2015"),
            ("Data Engineer  Delta  Jan 2015 - Present", "Built ETL pipelines in Python and SQL for 40 clients")][i % 3]
    return (f"[CANDIDATE_ID: {cid}]\n\n[SUMMARY]\nData engineer with Python, SQL and Airflow.\n\n"
            f"[EXPERIENCE]\n{jobs[0]}\n- Built ETL pipelines in Python and SQL\n\n{jobs[1]}\n"
            f"- Ran the Airflow platform for the {'finance' if i % 2 else 'sales'} team\n")


def _practical(i):
    return {"salary": {"amount": [4000, 5200, 6500][i % 3], "period": "month", "currency": "EUR"},
            "available_from": ["2026-11-15", "2026-12-20", "2027-03-01"][i % 3], "notice_weeks": 4,
            "location": {"country": ["Mauritius", "France", "India"][i % 3], "city": ""},
            "relocate": ["yes", "open", "no"][i % 3], "travel": ["occasional", "none", "frequent"][i % 3],
            "arrangement": "hybrid", "work_authorization": ["yes", "sponsorship", "yes"][i % 3],
            "languages": [{"language": "French", "level": ["C2", "B2", "A2"][i % 3]},
                          {"language": "English", "level": "C1"}]}


def make_packet(n=6, practical=True, varied=False):
    ids = [f"C{i:04d}" for i in range(1, n + 1)]
    pk = Packet(job={"title": "Data engineer", "summary": "", "must_haves": [{"id": "M1", "text": "Python"}],
                     "nice_to_haves": [{"id": "N1", "text": "Airflow"}]},
                logistics=LOGISTICS if practical else {}, ids=list(ids))
    pk.lens = {"helene": {}, "victor": {}, "nina": {}}
    for i, cid in enumerate(ids):
        cv = _cv(cid, i, varied)
        p = _practical(i) if practical else None
        ptext = practical_text(p, "Mauritius") if varied else (f"Line for {cid} in practical" if practical else
                                                                "No practical details were provided.")
        met = (i % 3) != 2 if varied else True
        crit = [{"id": "M1", "requirement": "Python", "verdict": "met" if met else "not_mentioned",
                 "quotes": ["Built ETL pipelines in Python and SQL"] if met else []},
                {"id": "N1", "requirement": "Airflow", "verdict": "met" if i % 2 else "not_mentioned",
                 "quotes": [f"Ran the Airflow platform for the finance team"] if i % 2 and varied else []}]
        if not varied:
            crit[1]["verdict"] = "met"
            crit[1]["quotes"] = [f"Line for {cid} in cv"]
            crit[0]["quotes"] = [f"Line for {cid} in cv"]
        pk.core[cid] = {"jules_assessment": {"score": "2 / 2", "criteria": crit}, "cv": cv}
        pk.sources[cid] = {"cv": cv, "practical": ptext if practical or varied else "", "linkedin": ""}
        rec = {"total_years_experience": 6, "experience": []}
        if varied:
            from cv_reader.structure import parse_range
            for ln in cv.splitlines():
                r = parse_range(ln)
                if r:
                    a, b, raw = r
                    rec["experience"].append({"title": ln.split("  ")[0], "start": f"{a // 12:04d}-{a % 12 + 1:02d}",
                                              "end": f"{b // 12:04d}-{b % 12 + 1:02d}", "months": b - a + 1})
        pk.lens["helene"][cid] = {"linkedin": "No LinkedIn profile provided."}
        pk.lens["victor"][cid] = {"career_timeline": career_timeline(rec)}
        pk.lens["nina"][cid] = {"practical_answers": ptext, "practical_checks": practical_checks(p, LOGISTICS if practical else {})}
        pk.scores[cid] = {"jules": 1.0 - i / 20, "remi": 0.5, "jules_raw": "2/2"}
        pk.missing[cid] = []
        pk.names[cid] = f"Person {i + 1}"
    pk.has_practical_data = practical
    return pk
