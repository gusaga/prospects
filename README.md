# Prospecting CRM

A local, single-user cold-call prospecting CRM. Everything runs and stays on
this machine: no cloud, no accounts, no APIs, no telemetry. Research is done
by an external LLM agent (Codex) with its own web browsing, which deposits
prospect records as JSON files — see [AGENTS.md](AGENTS.md).

## Run it

```powershell
.venv\Scripts\python.exe -m crm
```

or double-click **run.bat**. Either starts the app at
<http://127.0.0.1:8765> and opens your browser. `Ctrl+C` in the terminal
stops it.

## First-time setup (new machine only)

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m crm migrate   # only if data/prospects.db (legacy) exists
.venv\Scripts\python.exe -m crm seed      # optional: 25 fake prospects to play with
```

Python 3.11+ required. The database is a single file, `data/crm.db`.

## The daily loop

1. **Today's calls** (the home page) shows everything due plus the queue,
   sorted by priority. Log outcomes with one click: *No answer* schedules a
   retry automatically; *Conversation* stamps the contact.
2. **Prospects** is the full list — search with `/`, filter by status,
   region, priority, or score, sort any column, export the current view to
   CSV. Click a row to open the detail page; every field edits inline and
   saves automatically.
3. **Import** is where research lands. Deposits from the Codex agent are
   validated, deduped, and summarized; near-duplicates wait for your
   merge/keep/discard decision; rejected records are explained in
   `data/rejects.jsonl`.
4. **ICP & Settings** holds your ideal customer profile. *Generate research
   brief* turns it into a ready-to-paste prompt for the Codex agent,
   including a do-not-research list of companies you already have.

## Getting research done

Open this repo in a Codex task, paste the generated brief (or just point it
at `AGENTS.md`), and let it research. It writes a JSON file into `inbox/`
and runs `python -m crm import --inbox`. New prospects appear in the app —
nothing else to wire up.

## CLI reference

```text
python -m crm                 start the app (and open the browser)
python -m crm serve --port N  start on a different port
python -m crm import FILE     import one deposit JSON file
python -m crm import --inbox  import everything waiting in inbox/
python -m crm status          row counts and pending work
python -m crm seed [--wipe]   add / remove the 25 fake sample prospects
python -m crm backup          back up the database now
python -m crm migrate         one-time import of the legacy prospects.db
```

## Backup & restore

- A backup is written automatically to `backups/` once a day when the app
  starts (14 most recent are kept). `python -m crm backup` forces one.
- **Restore:** stop the app, copy the chosen `backups/crm-*.db` over
  `data/crm.db`, start the app.
- The pre-rebuild system (Streamlit app and its database) is preserved
  twice: the code at git tag `codex-final`, the data untouched at
  `data/prospects.db` plus a copy in `backups/`.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Layout

```text
crm/              the application
  models.py       SQLite schema (companies, prospects, activities, …)
  ingest.py       deposit pipeline: validate -> dedupe -> create/enrich/park/reject
  dedupe.py       normalization + duplicate rules
  web/            FastAPI app, Jinja templates, hand-written CSS/JS
  migrate_legacy.py, seed.py, backup.py, inbox.py, __main__.py
schemas/          versioned deposit contract + example file
inbox/            where the research agent drops JSON deposits
data/             crm.db (live), prospects.db (legacy, read-only)
backups/          timestamped database copies
AGENTS.md         instructions for the Codex research agent
```
