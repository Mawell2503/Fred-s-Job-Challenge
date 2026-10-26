"""The hiring agents that work on Oscar's output (cv_reader):

    clara.py              job description  -> must-haves + nice-to-haves (admin reviews them)
    remi.py               all candidates   -> must-have gate + top 20
    jules.py              top 20           -> full LLM read, verified quotes, top 5
    citation_checker.py   plain code that checks every quote exists in the CV
    llm.py                one switch for the LLM (Claude subscription / API / local Ollama)
"""
