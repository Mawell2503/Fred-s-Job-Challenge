"""What each committee member reads. Plain code, no AI.

Every member gets the SAME core for each of Jules's top 10: the job, Jules's verified assessment and
the full CV (personal details removed). On top of that, each gets their own lens:

    Helene  <linkedin>            the LinkedIn profile (PDF parser to come: for now provided / not)
    Victor  career_timeline       tenures, progression, gaps, change of field (computed here)
    Nina    practical_answers     what the applicant answered on the form (quotable text), including
                                  what they applied for when the role runs on the whole CV pool
            practical_checks      those answers checked against the role's requirements (computed here)
            <requirements>        the role's practical requirements

The practical answers come straight from the application form (via the committee input file written
by the website), never through Oscar / Remi / Jules.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2", "native"]
_LEVEL_NAME = {"native": "native"}
_SENIORITY = [(r"\b(intern|trainee|apprentice|stagiaire)\b", 0), (r"\b(junior|assistant|associate)\b", 1),
              (r"\b(senior|confirmed|experienced)\b", 3), (r"\b(lead|principal|staff|head|manager|supervisor)\b", 4),
              (r"\b(director|vp|vice president|chief|cto|cfo|ceo|partner)\b", 5)]
_RELOCATE = {"yes": "yes", "no": "no", "open": "open to it"}
_TRAVEL = {"none": "no travel", "occasional": "occasionally", "frequent": "frequently"}
_ARR = {"onsite": "on-site", "hybrid": "hybrid", "remote": "remote"}
_AUTH = {"yes": "yes", "no": "no", "sponsorship": "needs sponsorship"}


@dataclass
class Packet:
    job: dict                                   # title, summary, must/nice texts
    logistics: dict
    ids: list                                   # Jules's top 10, in Jules's order
    core: dict = field(default_factory=dict)    # cid -> {jules, cv}
    lens: dict = field(default_factory=dict)    # persona -> cid -> extra material
    sources: dict = field(default_factory=dict)  # cid -> {"cv": text, "practical": text, "linkedin": text}
    scores: dict = field(default_factory=dict)  # cid -> {"jules": 0-1, "remi": float}
    missing: dict = field(default_factory=dict)  # cid -> [what is missing, plain words]
    names: dict = field(default_factory=dict)   # cid -> name (report only, never sent to the LLM)
    has_practical_data: bool = False
    applied: dict = field(default_factory=dict)  # cid -> {"kind": this|open|other, "roles": [...]} (whole-pool runs)
    tie_break_direct: bool = False               # a tied average: people who applied for this role first

    def job_text(self) -> str:
        lines = [f"Title: {self.job.get('title', '')}"]
        if self.job.get("summary"):
            lines.append(f"Summary: {self.job['summary']}")
        lines += [f"Must-have {c['id']}: {c['text']}" for c in self.job.get("must_haves", [])]
        lines += [f"Nice-to-have {c['id']}: {c['text']}" for c in self.job.get("nice_to_haves", [])]
        return "\n".join(lines)

    def requirements_text(self) -> str:
        return requirements_text(self.logistics)

    def material(self, persona: str, ids) -> str:
        """JSON array with what `persona` reads about each candidate in `ids`."""
        out = []
        for cid in ids:
            item = {"id": cid, **self.core[cid]}
            item.update(self.lens.get(persona, {}).get(cid, {}))
            out.append(item)
        return json.dumps(out, indent=1, ensure_ascii=False)


# ---------------- Nina: answers as quotable text + checks ----------------
def application_line(a: dict | None) -> str:
    """What the candidate applied for, towards the role being run ('' when not known / not relevant)."""
    if not a:
        return ""
    if a.get("kind") == "this":
        return "Application: applied for this position"
    if a.get("kind") == "open":
        return "Application: open application (did not choose a position)"
    if a.get("kind") == "other":
        roles = ", ".join(a.get("roles") or []) or "another position"
        return f"Application: applied for another position ({roles})"
    return ""


def application_label(a: dict | None) -> str:
    """The same, for people (report, badges): 'Applied for this position', 'Open application', ..."""
    line = application_line(a)
    return line.removeprefix("Application: ")[:1].upper() + line.removeprefix("Application: ")[1:] if line else ""


def practical_text(p: dict | None, work_country: str = "", application: dict | None = None) -> str:
    """One quotable line per answer. This exact text is what Nina's quotes are checked against."""
    app = application_line(application)
    if not p:
        return "\n".join(x for x in ("No practical details were provided.", app) if x)
    s = p.get("salary") or {}
    loc = p.get("location") or {}
    where = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
    lines = [f"Salary expectation: {s.get('amount')} {s.get('currency', '')} per {s.get('period', 'month')}".replace("  ", " "),
             f"Earliest start date: {p.get('available_from', '')}"]
    if p.get("notice_weeks") is not None:
        lines.append(f"Notice period: {p['notice_weeks']} weeks")
    lines += [f"Lives in: {where}",
              f"Willing to relocate: {_RELOCATE.get(p.get('relocate'), p.get('relocate'))}",
              f"Willing to travel: {_TRAVEL.get(p.get('travel'), p.get('travel'))}",
              f"Preferred way of working: {_ARR.get(p.get('arrangement'), p.get('arrangement'))}"]
    if p.get("work_countries"):
        lines.append(f"Can work without a visa in: {', '.join(p['work_countries'])}")
    elif p.get("work_authorization"):                       # answers saved before the CV pool
        lines.append(f"Right to work in {work_country or 'the country of the position'}: "
                     f"{_AUTH.get(p.get('work_authorization'), p.get('work_authorization'))}")
    lines += [f"Language: {l['language']} ({l['level']})" for l in p.get("languages") or []]
    if app:
        lines.append(app)
    return "\n".join(lines)


def requirements_text(l: dict | None) -> str:
    l = l or {}
    lines = []
    if l.get("budget_min") is not None or l.get("budget_max") is not None:
        lo, hi = l.get("budget_min"), l.get("budget_max")
        rng = f"{lo} to {hi}" if lo is not None and hi is not None else (f"up to {hi}" if hi is not None else f"from {lo}")
        lines.append(f"Budget: {rng} {l.get('currency', '')} per {l.get('budget_period', 'month')}")
    if l.get("client_country"):
        lines.append(f"Client country: {l['client_country']}")
    if l.get("work_country") or l.get("work_city"):
        lines.append("Place of work: " + ", ".join(x for x in (l.get("work_city"), l.get("work_country")) if x))
    if l.get("arrangement"):
        lines.append(f"Way of working: {_ARR.get(l['arrangement'], l['arrangement'])}")
    if l.get("travel"):
        lines.append(f"Travel: {_TRAVEL.get(l['travel'], l['travel'])}")
    if l.get("start_date"):
        lines.append(f"Start date: {l['start_date']}")
    lines += [f"Required language: {x['language']} (at least {x['min_level']})" for x in l.get("languages") or []]
    if l.get("notes"):
        lines.append(f"Notes: {l['notes']}")
    return "\n".join(lines) or "No practical requirements were set for this role."


def _monthly(amount, period):
    return amount / 12 if period == "year" else amount


def _lang_key(name: str) -> str:
    try:
        from cv_reader.vocab import get_vocab
        canon = get_vocab().canonicalize(name)
        return canon or name.strip().lower()
    except Exception:
        return name.strip().lower()


def _application_check(a: dict | None) -> dict | None:
    if not a:
        return None
    kind = a.get("kind")
    if kind == "this":
        return {"check": "application", "status": "ok", "detail": "Applied for this position."}
    if kind == "open":
        return {"check": "application", "status": "unknown",
                "detail": "Open application: did not choose a position, so interest in this one is not confirmed."}
    if kind == "other":
        roles = ", ".join(a.get("roles") or []) or "another position"
        return {"check": "application", "status": "risk",
                "detail": f"Applied for another position ({roles}), not this one: interest in this one is not confirmed."}
    return None


def _norm_country(c: str) -> str:
    return re.sub(r"[^a-z]", "", (c or "").lower())


def practical_checks(p: dict | None, l: dict | None, application: dict | None = None) -> list[dict]:
    """The candidate's answers against the role's requirements: facts for Nina, not judgements.
    status: ok | risk | blocker | unknown."""
    l = l or {}
    app = _application_check(application)
    if not p:
        return [{"check": "practical details", "status": "unknown", "detail": "The candidate gave no practical details."}] \
            + ([app] if app else [])
    out = []

    def add(check, status, detail):
        out.append({"check": check, "status": status, "detail": detail})

    s = p.get("salary") or {}
    ask_cur = (s.get("currency") or "").upper()
    budget_cur = (l.get("currency") or "").upper()
    if l.get("budget_max") is not None and s.get("amount") and ask_cur and budget_cur and ask_cur != budget_cur:
        add("salary vs budget", "unknown",
            f"Asked in {ask_cur}, budget in {budget_cur}: not compared (no exchange rate in the system).")
    elif l.get("budget_max") is not None and s.get("amount"):
        ask = _monthly(s["amount"], s.get("period"))
        top = _monthly(l["budget_max"], l.get("budget_period"))
        over = (ask - top) / top * 100 if top else 0
        if over <= 0:
            add("salary vs budget", "ok", "Within the budget.")
        elif over <= 10:
            add("salary vs budget", "risk", f"{over:.0f}% above the top of the budget.")
        else:
            add("salary vs budget", "blocker", f"{over:.0f}% above the top of the budget.")
    else:
        add("salary vs budget", "unknown", "No budget set for the role.")

    if l.get("start_date") and p.get("available_from"):
        late = (date.fromisoformat(p["available_from"]) - date.fromisoformat(l["start_date"])).days
        if late <= 0:
            add("start date", "ok", "Available by the start date.")
        elif late <= 30:
            add("start date", "risk", f"Available {late} days after the start date.")
        else:
            add("start date", "blocker", f"Available {late} days after the start date.")

    have = {_lang_key(x["language"]): x["level"] for x in p.get("languages") or []}
    for req in l.get("languages") or []:
        lvl = have.get(_lang_key(req["language"]))
        need = LEVELS.index(req["min_level"])
        if lvl is None:
            add(f"language: {req['language']}", "blocker", f"{req['language']} is not among the candidate's languages.")
        elif LEVELS.index(lvl) >= need:
            add(f"language: {req['language']}", "ok", f"{lvl}, at least {req['min_level']} required.")
        elif LEVELS.index(lvl) == need - 1:
            add(f"language: {req['language']}", "risk", f"{lvl}, one level below the {req['min_level']} required.")
        else:
            add(f"language: {req['language']}", "blocker", f"{lvl}, well below the {req['min_level']} required.")

    wc = (l.get("work_country") or "").strip().lower()
    here = ((p.get("location") or {}).get("country") or "").strip().lower()
    if wc and l.get("arrangement") in ("onsite", "hybrid") and here and here != wc:
        rel = p.get("relocate")
        add("location", {"yes": "ok", "open": "risk", "no": "blocker"}.get(rel, "unknown"),
            f"Lives outside {l['work_country']}; willing to relocate: {_RELOCATE.get(rel, rel)}.")
    if l.get("arrangement") and p.get("arrangement") and l["arrangement"] != p["arrangement"]:
        add("way of working", "risk", f"Role is {_ARR[l['arrangement']]}, candidate prefers {_ARR.get(p['arrangement'])}.")

    order = ["none", "occasional", "frequent"]
    if l.get("travel") in order and p.get("travel") in order:
        gap = order.index(l["travel"]) - order.index(p["travel"])
        if gap > 0:
            add("travel", "blocker" if gap == 2 else "risk",
                f"Role needs {_TRAVEL[l['travel']]} travel; candidate: {_TRAVEL[p['travel']]}.")

    countries = p.get("work_countries")
    if isinstance(countries, list) and (countries or not p.get("work_authorization")):
        if not wc:
            add("right to work", "unknown", "No place of work set for the role.")
        elif any(_norm_country(c) == _norm_country(l["work_country"]) for c in countries):
            add("right to work", "ok", f"Can work in {l['work_country']} without a visa.")
        else:
            add("right to work", "risk", f"{l['work_country']} is not among the countries they can work in without "
                                         f"a visa ({', '.join(countries) or 'none given'}): would need a work permit.")
    else:                                                   # answers saved before the CV pool
        auth = p.get("work_authorization")
        add("right to work", {"yes": "ok", "sponsorship": "risk", "no": "blocker"}.get(auth, "unknown"),
            f"Right to work: {_AUTH.get(auth, auth)}.")
    if app:
        out.append(app)
    return out


# ---------------- Victor: the career timeline ----------------
def _seniority(title: str) -> int:
    t = title.lower()
    for rx, rank in reversed(_SENIORITY):
        if re.search(rx, t):
            return rank
    return 2


def _role_words(title: str) -> str:
    t = re.sub(r"\b(intern|trainee|junior|senior|lead|principal|head|chief|assistant|associate)\b", " ", title.lower())
    return " ".join(re.findall(r"[a-z]{3,}", t))


def career_timeline(record: dict | None) -> dict:
    """Tenures, progression, gaps and change of field from Oscar's structured record."""
    exp = [e for e in (record or {}).get("experience") or [] if e.get("title")]
    dated = sorted([e for e in exp if e.get("start")], key=lambda e: e["start"])
    jobs = [{"title": e["title"][:120], "from": e.get("start"), "to": e.get("end"), "months": e.get("months")}
            for e in (dated or exp)]
    facts = []
    total = (record or {}).get("total_years_experience")
    if total is not None:
        facts.append(f"{total} years of dated experience over {len(exp)} job(s).")
    months = [e["months"] for e in dated if e.get("months")]
    if months:
        avg = sum(months) / len(months)
        facts.append(f"Average time in a job: {avg:.0f} months; longest {max(months)}, shortest {min(months)}.")
        if len(months) >= 3 and avg < 12:
            facts.append("Many short stays (under a year on average).")
    if len(exp) - len(dated):
        facts.append(f"{len(exp) - len(dated)} job(s) without dates.")

    def idx(s):
        y, m = s.split("-")
        return int(y) * 12 + int(m) - 1
    for a, b in zip(dated, dated[1:]):
        gap = idx(b["start"]) - idx(a["end"]) - 1
        if gap >= 6:
            facts.append(f"Gap of {gap} months before '{b['title'][:60]}'.")
    if len(dated) >= 2:
        first, last = _seniority(dated[0]["title"]), _seniority(dated[-1]["title"])
        facts.append("Progression: " + ("moved up" if last > first else "moved down" if last < first else "same level"))
        same = {}
        for e in dated:
            same.setdefault(_role_words(e["title"]), []).append(e)
        for words, es in same.items():
            span = sum(e.get("months") or 0 for e in es)
            if words and span >= 60 and len({_seniority(e["title"]) for e in es}) == 1:
                facts.append(f"Same role ({es[0]['title'][:50]}) for {span // 12} years without a change of level.")
        early = set(_role_words(dated[0]["title"]).split())
        late = set(_role_words(dated[-1]["title"]).split())
        if early and late and not early & late:
            facts.append(f"Changed field: started as '{dated[0]['title'][:50]}', now '{dated[-1]['title'][:50]}'.")
    return {"jobs": jobs, "facts": facts}


# ---------------- build ----------------
def build(job: dict, jules_out: dict, load_candidate, cin: dict, top: int | None = None) -> Packet:
    """`load_candidate(cid)` -> Oscar's output (hiring.store.load_candidate);
    `cin` -> the committee input written by the website: logistics, practical answers, linkedin flags."""
    logistics = cin.get("logistics") or {}
    practical = cin.get("practical") or {}
    linkedin = cin.get("linkedin") or {}
    application = cin.get("application") or {}
    given_names = cin.get("names") or {}                      # the name on the application (report only)
    finalists = jules_out.get("finalists", [])[: top or None]
    pk = Packet(job={"title": job.get("title", ""), "summary": job.get("summary", ""),
                     "must_haves": job.get("must_haves", []), "nice_to_haves": job.get("nice_to_haves", [])},
                logistics=logistics, ids=[])
    pk.lens = {"helene": {}, "victor": {}, "nina": {}}
    work_country = logistics.get("work_country", "")
    for f in finalists:
        cid = f["candidate_id"]
        cand = load_candidate(cid)
        if not cand:
            continue
        pk.ids.append(cid)
        pk.core[cid] = {
            "jules_assessment": {
                "score": f"{f.get('score', 0):g} / {f.get('max_score', 0):g}",
                "criteria": [{"id": c["id"], "requirement": c["requirement"], "verdict": c["verdict"],
                              "quotes": c.get("cv_quotes", [])} for c in f.get("criteria", [])],
            },
            "cv": cand["full_text"],
        }
        ptext = practical_text(practical.get(cid), work_country, application.get(cid))
        pk.sources[cid] = {"cv": cand["full_text"], "practical": ptext, "linkedin": ""}
        pk.lens["helene"][cid] = {"linkedin": ("The candidate uploaded a LinkedIn PDF, but the system cannot read it "
                                               "yet: judge from the CV.") if linkedin.get(cid) else
                                  "No LinkedIn profile provided."}
        pk.lens["victor"][cid] = {"career_timeline": career_timeline(cand.get("record"))}
        pk.lens["nina"][cid] = {"practical_answers": ptext,
                                "practical_checks": practical_checks(practical.get(cid), logistics, application.get(cid))}
        mx = f.get("max_score") or 0
        pk.scores[cid] = {"jules": (f.get("score", 0) / mx) if mx else 0.0, "remi": f.get("remi_score") or 0.0,
                          "jules_raw": f"{f.get('score', 0):g}/{mx:g}",
                          "direct": 1 if (application.get(cid) or {}).get("kind") == "this" else 0}
        if application.get(cid):
            pk.applied[cid] = application[cid]
        miss = [f"{m['id']} {m['requirement']} ({m['verdict'].replace('_', ' ')})" for m in f.get("list_missing", [])]
        if not linkedin.get(cid):
            miss.append("No LinkedIn profile PDF.")
        if not practical.get(cid):
            miss.append("No practical details.")
        pk.missing[cid] = miss
        pk.names[cid] = given_names.get(cid) or (f.get("personal") or {}).get("name") or cid
    pk.tie_break_direct = bool(cin.get("tie_break_direct")) and bool(application)
    pk.has_practical_data = bool(practical) or bool(application) or any(
        logistics.get(k) for k in ("budget_min", "budget_max", "client_country", "work_country", "arrangement",
                                   "travel", "start_date", "languages", "notes"))
    return pk


__all__ = ["Packet", "build", "practical_text", "practical_checks", "requirements_text", "career_timeline",
           "application_line", "application_label", "LEVELS"]
