# Prospecting CRM — project conventions

Read this before changing anything. The product owner is Gustavo
(non-technical; explain decisions in plain language, see the global
CLAUDE.md). He is the only user.

## Non-negotiable constraints

- **Local-only.** SQLite file database, server bound to 127.0.0.1, no auth,
  no cloud, no telemetry, works offline. Never add a deployment story.
- **No research/enrichment APIs, ever.** No SerpAPI/Apollo/Clearbit/Hunter,
  no API keys, nothing that phones home. Research is done by an external
  Codex agent using its own web browsing; it deposits JSON files into
  `inbox/` (contract in `schemas/prospect-deposit.schema.json`, agent
  instructions in `AGENTS.md`).
- **Boring stack, no build step.** FastAPI + Jinja + hand-written CSS/JS
  (no npm, no CDN scripts — the app must work with no internet). SQLAlchemy
  + SQLite. Keep it launchable with `python -m crm` for years.
- **`data/crm.db` is real business data.** Never wipe or regenerate it.
  Seeded fake rows (safe to remove) are marked `source='seed'`.

## Architecture in one paragraph

`crm/models.py` defines the schema (companies, prospects, append-only
activities, settings, import_batches, dupe_reviews). Every way records enter
— agent JSON deposits, CSV upload, manual add — goes through one pipeline in
`crm/ingest.py`: per-record validation (pydantic `ProspectRecord`), then
dedupe (`crm/dedupe.py`: exact match by company+normalized name → skip or
enrich empty fields; near-match by shared phone/email/LinkedIn or similar
name at same company → parked in `dupe_reviews` for human resolution),
rejects appended to `data/rejects.jsonl` with reasons. The web app
(`crm/web/app.py` + `routes_data.py`) is server-rendered Jinja with a small
vanilla-JS layer (`static/app.js`) for copy buttons, inline autosave
(PATCH `/api/prospects/{id}`), live search (`?partial=1` fragments), and
kanban drag-drop.

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
2026-07-30). The old system is preserved at git tag `codex-final`; its
database `data/prospects.db` stays on disk read-only (migrated via
`python -m crm migrate`, which refuses to run twice).
