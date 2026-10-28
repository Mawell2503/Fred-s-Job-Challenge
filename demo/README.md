# Demo data (fictional)

Everything here is invented: the people, the companies, the e-mail addresses (example.com) and the
phone numbers. It exists to show the pipeline and the committee on a realistic, *contentious* set.

- `role.json`: a data engineer for a French client (criteria already reviewed, practical requirements set)
- `cvs/`: 15 CVs, each built around one tension (see `why` in `candidates.json`)
- `candidates.json`: name, CV file, practical answers, whether a LinkedIn PDF was "uploaded"

Load it, then run everything:

```
npm run demo                 # creates the role and submits the 15 applications (runs Oscar on each CV)
npm run pipeline -- R00x     # Oscar -> Remi -> Jules -> committee -> Gaston (the id is printed by the first command)
```

With `LLM_BACKEND=fake` in `.env` the whole thing runs offline in seconds, with simple rules instead of an AI
(good for checking the plumbing). With `claude_cli` or `api`, the agents are real.
The LinkedIn PDFs are one-line placeholders: the LinkedIn parser is not built yet.
