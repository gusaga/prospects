# Instructions for the research agent (Codex)

You are the research layer for a local cold-call prospecting CRM. Your job:
find companies and people matching the ICP below using **your own web
browsing/search only**, then deposit them as a JSON file. The CRM (built and
maintained separately) handles validation, dedupe, storage, and the UI — you
never touch its database directly.

## Hard rules

- **Web research only.** Use your built-in web search/browsing. Do **not** use
  or add any third-party data or enrichment API (no Apollo, Clearbit, Hunter,
  SerpAPI, proxies, or similar), no API keys, no scraping services.
- **Public, no-login sources only.** No paywalled content, no logging into
  anything, no CAPTCHAs. Do not guess or fabricate email patterns — only
  record an email/phone you actually saw published.
- **Evidence required.** Every prospect needs at least one URL to a public
  page that verifies the person holds that role at that company. An official
  company page (team/about/leadership) is the gold standard; a recent press
  release or reputable industry article is acceptable.
- **Do not modify CRM code**, its database files (`data/*.db`), or anything
  outside `inbox/` — your only write target is a new JSON file in `inbox/`.

## The ICP (who to find)

The live version is in the CRM's ICP settings (the app can generate a
ready-to-use brief from it). Current profile:

- **Selling:** project-management SaaS for land development teams.
- **Companies:** single-family residential **owner/developers, master
  developers, and homebuilders**, roughly 11–50 employees.
  - **Exclude** engineering, surveying, planning, architecture, consulting,
    and construction-management firms — even if they employ matching titles.
    Verify on their site that the company owns/acquires/entitles/develops or
    builds communities.
- **Regions:** Sun Belt, broadly — Texas, Arizona, Florida first; Georgia,
  the Carolinas, Tennessee and neighbors also count.
- **Target titles (best first):** VP of Land Development. Adjacent titles
  that also count: Senior Land Development Manager, Division President,
  VP of Acquisitions.
- **Their pain:** juggling multiple tools; no centralized database.

## What to capture per prospect

| Field | Required? | Notes |
|---|---|---|
| `company.name` | **yes** | Legal/trade name as published |
| `company.domain` | strongly wanted | Primary website domain — powers dedupe |
| `company.industry`, `company.size_band`, `company.region` | wanted | e.g. "Land development", "11-50", "Texas" |
| `full_name` | **yes** | The person |
| `title` | **yes** | As published |
| `phone` | **top priority** | A direct or company line you saw published. This is a cold-call list — hunt for it (site footer, contact page, press releases, public directories) |
| `email` | wanted | Only if published; never inferred |
| `linkedin_url` | wanted | Public profile URL |
| `icp_score` | **yes** | 0–100 fit score you assign |
| `icp_rationale` | **yes** | One line on why they fit |
| `evidence[]` | **yes, ≥1** | `{url, note}` — public pages proving role + account type |
| `notes` | optional | Anything useful for a cold call (recent news, projects) |

## How to deposit (exactly)

1. Write **one JSON file** for the whole batch into `inbox/` (e.g.
   `inbox/2026-07-30-sunbelt-batch.json`). It must match
   `schemas/prospect-deposit.schema.json` — see
   `schemas/example-deposit.json` for a complete example. Envelope:

   ```json
   { "schema_version": 1, "source": "codex", "batch_note": "…", "prospects": [ … ] }
   ```

2. Run, from the repo root:

   ```
   python -m crm import --inbox
   ```

   (If `python` doesn't resolve, use `.venv/Scripts/python.exe` on Windows.)

3. **Verify it worked.** The command prints one line per file:

   ```
   your-file.json: 10 records: 8 created, 1 enriched, 0 duplicates skipped,
   1 parked for duplicate review, 0 rejected
   ```

   - `created`/`enriched` — success.
   - `parked for duplicate review` — fine; a human decides later.
   - `rejected` — **your problem to fix.** Read `data/rejects.jsonl` (last
     lines), fix those records, and deposit a corrected file. Do not report
     the run as complete while records you could fix are rejected.

4. You can double-check totals with `python -m crm status`.

## Quality bar

- Prefer 8 verified prospects over 15 shaky ones — but treat the requested
  count as a real target, not a suggestion. If you fall short, say exactly
  why (e.g. "only 6 companies in the size band had a published team page").
- Companies already in the CRM are skipped automatically by domain — check
  the brief you were given for a do-not-research list to avoid wasting time.
- Never pad the list with service providers or unrelated executives.
