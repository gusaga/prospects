# Evidence-Backed Local Prospecting Engine

Local Streamlit application for public-web account and prospect research. Its default **Codex handoff mode** uses your signed-in Codex agent as the human-triggered worker—no LLM API key required. Optional API mode uses `browser-use` with an OpenAI-compatible LLM. Results stay in SQLite and only human-approved prospects are exported.

## What it does

- Captures a structured Ideal Customer Profile (ICP) in Streamlit.
- Runs three bounded browser agents: account discovery, contact discovery, and public rapport/trigger research.
- Keeps field-level evidence: source URL, source type, excerpt, extraction time, fingerprint, and freshness state.
- Scores evidence transparently; scores above `0.85` require an official company source and independent corroboration.
- Maps a public buying committee, tracks account signals, captures review feedback, excludes suppressions, and exports only approved prospects.
- Provides a stale-evidence refresh script that flags changed pages instead of overwriting evidence.
- Treats the configured account and qualified-prospect counts as delivery targets. A partial run is labeled `completed_with_shortfall`, with reasons and a deduplicated gap-fill job.

## Local setup

1. Install Python 3.11+ and create a virtual environment.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e ".[dev]"
   browser-use install
   ```

2. Create `.env` from `.env.example`.

   ```powershell
   Copy-Item .env.example .env
   ```

   Leave `RESEARCH_MODE=codex_handoff` to use Codex with your existing ChatGPT subscription. The default database is the local `data/prospects.db` file. Set `RESEARCH_MODE=api` plus `LLM_MODEL`, `LLM_API_KEY`, and `LLM_BASE_URL` only if you later want direct API execution.

3. Start the UI.

   ```powershell
   streamlit run app.py
   ```

`BROWSER_HEADLESS=false` opens a local Chromium window while agents work. Agents use temporary browser profiles and public no-login pages only. The application automatically keeps Browser Use and its browser harness state under `data/browser-use/` rather than your existing Chrome profile.

## Codex handoff mode (no API key)

1. In Streamlit, save an ICP and select **Queue research for Codex**. The app creates a local run and a job file under `data/codex-handoffs/`.
2. In a Codex task opened on this workspace, send the displayed prompt, or: `Use $prospecting-codex-worker to process queued prospecting run <run-id>.`
3. The Codex worker researches public no-login pages, writes a validated JSON result file, and uses the local ingestion command to persist it through the normal scoring/evidence pipeline.

The Codex subscription remains a human-triggered agent workflow; Streamlit cannot invoke the subscription as a background API. OpenAI's ChatGPT and API billing are separate. [OpenAI billing guidance](https://help.openai.com/en/articles/9039756-billing-settings-in-chatgpt-vs-platform)

## Operations

Run the high-confidence query for a completed run:

```powershell
python scripts/query_high_confidence.py --run-id <run-uuid>
```

Inspect, validate, or ingest a Codex handoff manually:

```powershell
python scripts/codex_handoff.py list
python scripts/codex_handoff.py show --run-id <run-uuid>
python scripts/codex_handoff.py validate --run-id <run-uuid>
python scripts/codex_handoff.py ingest --run-id <run-uuid>
```

Recalculate an existing completed run after an alignment-rule update, without re-researching sources:

```powershell
python scripts/rescore_runs.py --run-id <run-uuid>
```

Refresh stale public evidence (appropriate for a local scheduled task):

```powershell
python scripts/refresh_stale.py --limit 100
```

Run the test suite without live browser or model credentials:

```powershell
python -m pytest -q
```

## Boundaries

- No Hunter, Apollo, Clearbit, data brokers, or third-party B2B data APIs.
- No logins, CAPTCHA/paywall bypassing, private-data collection, or inferred email patterns.
- No autonomous emails, social messages, calendar requests, or CRM write-backs.
- CSV/JSON import and export are provided as the v1 handoff boundary.
