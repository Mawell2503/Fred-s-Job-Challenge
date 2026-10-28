"""The hiring committee: Jules's top 10 -> debate -> top 5 -> Gaston's report.

    personas/*.md     the instructions that make Hélène, Victor, Nina and Gaston (edit freely)
    packet.py         what each member reads (plain code)
    members.py        ask(): the one AI call, same material, different instructions
    validate.py       checks every answer (plain code)
    orchestrator.py   who speaks, when to stop, re-prompts, final top 5, trace (plain code)
    gaston.py         the report writer (one AI call, checked) + report.py (Markdown)
    fake.py           answers for LLM_BACKEND=fake (no AI)
"""
