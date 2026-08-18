# Prospecting CRM

A local, single-user cold-call prospecting CRM. Everything runs and stays on
**your computer**: no cloud accounts, no paid enrichment APIs, no telemetry.
Works for **any industry** — you define who to hunt in Settings.

Repo: https://github.com/gusaga/prospects

## From the GitHub link → working app (Windows)

Visual guide: open [`docs/how-to-run.html`](docs/how-to-run.html) in your browser.

| Step | What you do |
|------|-------------|
| 1 | Install [Python 3.11+](https://www.python.org/downloads/) — check **Add python.exe to PATH** |
| 2 | On GitHub: **Code → Download ZIP** (or `git clone`), then unzip |
| 3 | Double-click **`setup.bat`** (creates `.venv` and installs the app) |
| 4 | Double-click **`run-live.bat`** — browser opens a list picker |
| 5 | Open or **start a new list** (saved under `Documents\`, not in GitHub) |
| 6 | **Settings** → fill your Ideal Customer Profile (or Load example ICP) |
| 7 | Use **Research** / **Today** to find people and dial |

That is enough to start. No Docker required.

### Calling lists live in Documents, never in GitHub

This repo is **application code only**. After setup, double-click
**`run-live.bat`**. It opens a home screen so you can pick a calling list
or start a new one. Lists are stored under `Documents\`, so updating or
re-downloading the GitHub folder will not mix or wipe them.

```text
Documents\ProspectingCRM\            ← one list
Documents\ProspectingCRM-Dental\     ← another list
  data\crm.db      ← prospects
  inbox\           ← research deposits
  backups\         ← automatic backups
```

Each list is a folder. Creating a new one does not copy or overwrite another.
You can also create a folder from the command line with
`python -m crm init-home --home "…"` (never overwrites an existing DB).

## Day-to-day launchers

| File | Use when |
|------|----------|
| `setup.bat` | First time on this machine (or after a fresh download) |
| `run-live.bat` (or `run.bat`) | Start, then pick or create a list in `Documents\` |

## Share / contribute

Friends clone the same repo, run `setup.bat`, set **their** ICP in Settings, and
keep **their** data on their machine. Pull requests welcome.

```powershell
git clone https://github.com/gusaga/prospects.git
cd prospects
# or just double-click setup.bat
```

Docker is optional — see [DOCKER.md](DOCKER.md).

## The daily loop

1. **Today** — due follow-ups and the call queue.
2. **Prospects** — full list, search, filters, export.
3. **Research** — request briefs, import agent deposits, run local enricher.
4. **Settings** — ICP (any industry) and database path.

## CLI reference

```text
python -m crm serve --pick-home  start at the Documents list picker (usual)
python -m crm init-home          create a Documents home (never overwrites)
python -m crm import --inbox     import deposits waiting in that home's inbox/
python -m crm validate FILE      dry-run a deposit
python -m crm status             row counts
python -m crm seed               optional fake sample prospects
python -m crm backup             back up now
```

With a separate home:

```powershell
$env:CRM_HOME = "$env:USERPROFILE\Documents\ProspectingCRM"
.venv\Scripts\python.exe -m crm
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Layout

```text
setup.bat / run.bat / run-live.bat
crm/              application
schemas/          deposit contract
docs/how-to-run.html
AGENTS.md         instructions for the research agent
```
