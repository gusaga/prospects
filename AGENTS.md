# Instructions for the research agent (Codex)

You are the research layer for a local cold-call prospecting CRM. The CRM
(built and maintained separately) handles validation, dedupe, storage, and
the UI — you never touch its database directly. You use **your own web
browsing/search only** and deliver everything as JSON deposit files.

There are two kinds of jobs, and the brief you receive tells you which:

- **Stage 1 — discovery** ("Research N new cold-call prospects"): find NEW
  companies and people matching the ICP below.
- **Stage 2 — enrichment** ("Deep-research these prospects I have already
  vetted"): do NOT find new people. Deepen the specific prospects listed in
  the brief — each has a `prospect_id`. See "Stage 2" near the end.

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
| `icp_score` | **yes** | 0–100, using the rubric below |
| `icp_rationale` | **yes** | One line on why they fit |
| `evidence[]` | **yes, ≥1** | `{url, note}` — public pages proving role + account type |
| `notes` | optional | Anything useful for a cold call (recent news, projects) |

At most **3 contacts per company** — breadth beats depth for cold calling.

## Scoring rubric (keep scores comparable between batches)

Start from what you can **verify with evidence URLs**, not vibes:

- **90–100** — exact target title, at a verified owner/developer/homebuilder,
  in a listed region, company size in band. All four confirmed.
- **75–89** — adjacent title instead of exact, OR exactly one of
  region/size/account-type is unconfirmed (say which in the rationale).
- **60–74** — two attributes unconfirmed, or region is a Sun Belt neighbor
  rather than a listed state.
- **Below 60** — don't include it unless something notable justifies it
  (explain in the rationale).

A published **direct phone adds +5** (cap 100). If you can't verify the
company is an owner/developer (vs. a service firm), it doesn't belong in
the deposit at all.

## How to deposit (exactly)

1. Write **one JSON file** for the whole batch into `inbox/` (e.g.
   `inbox/2026-07-30-sunbelt-batch.json`). It must match
   `schemas/prospect-deposit.schema.json` — see
   `schemas/example-deposit.json` for a complete example. Envelope:

   ```json
   {
     "schema_version": 1,
     "source": "codex",
     "request_id": 3,
     "batch_note": "…",
     "shortfall_reasons": [],
     "prospects": [ … ]
   }
   ```

   If your brief mentions a request id (`R-3` → `"request_id": 3`), set it —
   that's how delivery gets tracked against the ask. If you delivered fewer
   prospects than requested, put every concrete reason in
   `shortfall_reasons` (e.g. "only 6 companies in the size band published a
   team page"). Never pad with weak fits instead.

2. **Self-check before depositing** (from the repo root; if `python` doesn't
   resolve, use `.venv/Scripts/python.exe` on Windows):

   ```
   python -m crm validate inbox/<your-file>.json
   ```

   This is a dry run — it writes nothing. It tells you, per record, whether
   it would be created, treated as a duplicate, parked for human review, or
   rejected as invalid (with the exact validation errors). **Fix everything
   it marks `invalid` and re-validate until clean.**

3. Deposit:

   ```
   python -m crm import --inbox
   ```

4. **Verify it worked.** The command prints one line per file:

   ```
   your-file.json: 10 records: 8 created, 1 enriched, 0 duplicates skipped,
   1 parked for duplicate review, 0 rejected
   ```

   - `created`/`enriched` — success.
   - `parked for duplicate review` — fine; a human decides later.
   - `rejected` — **your problem to fix.** Read `data/rejects.jsonl` (last
     lines), fix those records, and deposit a corrected file. Do not report
     the run as complete while records you could fix are rejected.

5. You can double-check totals with `python -m crm status`.

## Stage 2 — enrichment jobs

When the brief lists existing prospects with `prospect_id` numbers:

- **Every record you deposit must carry that `prospect_id`**, plus
  `full_name` and `company` repeated exactly as the brief lists them (the
  schema requires those fields). The CRM applies your record directly to
  that prospect: it fills empty fields, merges evidence and profile links,
  and appends your `notes` to the activity log. It never overwrites
  existing values — so do not bother re-sending facts the brief says are
  already known.
- Priority order: **1) direct phone, 2) LinkedIn URL + city, 3) rapport
  intel in `notes`** (recent news, projects, quotes, talks, permits — call
  ammunition), **4) other public profiles** in
  `"profiles": [{"label": "Facebook", "url": "…"}]`.
- Reality check on socials: you cannot log in anywhere, and most
  Facebook/Instagram content is login-walled. Only record profiles that are
  publicly visible. Do not burn time forcing it — a phone number is worth
  more than every social link combined.
- Never change a person's name or title. If you find they changed roles or
  left the company, put what you found (with the evidence URL) in `notes`.
- Use `"schema_version": 2` and run `python -m crm validate` before
  depositing — every record should report `enrich`, not `create`. A record
  that says `create` has a wrong or missing `prospect_id`.

## Quality bar

- Prefer 8 verified prospects over 15 shaky ones — but treat the requested
  count as a real target, not a suggestion. If you fall short, say exactly
  why (e.g. "only 6 companies in the size band had a published team page").
- Companies already in the CRM are skipped automatically by domain — check
  the brief you were given for a do-not-research list to avoid wasting time.
- Never pad the list with service providers or unrelated executives.
