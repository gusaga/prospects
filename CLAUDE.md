# Prospecting CRM — project conventions

Read this before changing anything. The product owner is Gustavo
(non-technical; explain decisions in plain language, see the global
CLAUDE.md). He is the only user.

## Non-negotiable constraints

- **Local-only.** SQLite file database, server bound to 127.0.0.1 by default,
  no auth, no cloud multi-tenant CRM, no telemetry, works offline. Docker is
  allowed only as a *local packaging* story (friends run the image on their
  own machine with volumes). Never add a hosted SaaS / deployment story.
- **No paid research/enrichment APIs, ever.** No SerpAPI/Apollo/Clearbit/Hunter,
  no API keys. Stage-1 discovery still comes from an external Codex agent
  depositing JSON into `inbox/`. Stage-2 deepening can also be done by the
  **local enricher** (`crm/enrich/`, `python -m crm enrich`) which searches
  and scrapes the *public* web with no API keys, then writes the same
  Stage-2 deposit format into `inbox/`. The enricher is industry-agnostic;
  who to hunt is defined in Settings → ICP.
- **Boring stack, no build step.** FastAPI + Jinja + hand-written CSS/JS
  (no npm, no CDN scripts — the app must work with no internet). SQLAlchemy
  + SQLite. Keep it launchable with `python -m crm` for years.
- **Calling lists live outside this repo.** Never commit `data/`, `inbox/`, or
  `backups/`. Prefer `Documents\ProspectingCRM*` via `run-live.bat`. Never wipe
  or replace `Documents\ProspectingCRM\data\crm.db` without an explicit ask.
  Seeded fake rows (safe to remove) are marked `source='seed'`.

## Architecture in one paragraph

`crm/models.py` defines the schema (companies, prospects, append-only
activities, settings, import_batches, dupe_reviews). Every way records enter
— agent JSON deposits, local enricher deposits, CSV upload, and manual add —
goes through one pipeline in `crm/ingest.py`: per-record validation (pydantic
`ProspectRecord`), then dedupe (`crm/dedupe.py`: exact match by
company+normalized name → skip or enrich empty fields; near-match by shared
phone/email/LinkedIn or similar name at same company → parked in
`dupe_reviews` for human resolution), rejects appended to `data/rejects.jsonl`
with reasons. The web app (`crm/web/app.py` + `routes_data.py` + `routes_homes.py`) is
server-rendered Jinja with a small vanilla-JS layer (`static/app.js`) for copy
buttons, inline autosave (PATCH `/api/prospects/{id}`), live search
(`?partial=1` fragments), and kanban drag-drop. Local Stage-2 enrichment lives
in `crm/enrich/` (public search + scrape → inbox deposit).

## Working rules

- Statuses are the cold-call workflow in `models.STATUSES`; slugs are stored,
  labels rendered. Don't rename slugs without a data migration.
- The activity log is append-only — no edit/delete routes, by design.
- If `ProspectRecord`/`DepositFile` in `crm/ingest.py` changes, bump
  `schema_version`, regenerate the schema file
  (`python -c "import json; from crm.ingest import DepositFile; print(json.dumps(DepositFile.model_json_schema(), indent=2))" > schemas/prospect-deposit.schema.json`),
  update `schemas/example-deposit.json` and `AGENTS.md`.
- Tests: `.venv\Scripts\python.exe -m pytest -q` (all in `tests/`). Route
  tests use FastAPI's TestClient with `crm.config` monkeypatched to tmp
  paths — follow that pattern; never let tests touch `data/`.
- Windows quirks: `.pytest_tmp/` and `.pytest_cache/` at the repo root are
  ACL-locked leftovers that can't be deleted without elevation — ignore
  them (pytest cache is redirected to `.cache/`). The user may already be
  running the app; port 8765 in use usually means exactly that.

## History

Built by rewriting a Codex-generated Streamlit app (audit + rebuild
2026-07-30). This public repository starts from the FastAPI app; calling
lists are not in git. The one-time `python -m crm migrate` command still
exists for a local legacy `prospects.db` if someone has one — it refuses
to run twice.
