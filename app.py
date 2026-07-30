"""Streamlit UI for evidence-backed local prospecting."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timezone

import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from prospecting.account_types import account_type_label
from prospecting.agents import BrowserUseRunner
from prospecting.codex_handoff import handoff_prompt, job_path, queue_codex_run, queue_gap_fill_run
from prospecting.config import get_settings
from prospecting.database import build_session_factory, create_db_engine, initialize_database, session_scope
from prospecting.dashboard import build_dashboard_snapshot
from prospecting.exports import account_brief, export_csv, export_json, import_prospects_csv, message_angles
from prospecting.models import Company, EvidenceRecord, OutreachActivity, Prospect, RelationshipNote, ResearchRun
from prospecting.orchestrator import ProspectingOrchestrator
from prospecting.outreach import OUTREACH_STAGES, outreach_stage_label, update_outreach
from prospecting.rescoring import rescore_run
from prospecting.review import add_suppression, apply_review, list_review_queue, record_feedback, request_run_cancel
from prospecting.relationship import RELATIONSHIP_NOTE_BUCKETS, add_relationship_note, relationship_note_label
from prospecting.schemas import AccountType, ICPProfile, TargetAccountType


st.set_page_config(page_title="Local SDR Workspace", page_icon="🔎", layout="wide")

st.markdown(
    """
    <style>
      :root {
        --ink: #25243f;
        --muted: #797a91;
        --line: #e9e9f3;
        --lavender: #6966e9;
        --lavender-soft: #f0efff;
        --mint: #55c9b5;
        --canvas: #f8f8fc;
        --surface: #ffffff;
      }
      .stApp { background: var(--canvas); color: var(--ink); }
      [data-testid="stHeader"] { background: rgba(248, 248, 252, 0.86); border-bottom: 1px solid rgba(233, 233, 243, 0.72); }
      .block-container { max-width: 1440px; padding-top: 1.15rem; padding-bottom: 2.5rem; }
      h1, h2, h3 { color: var(--ink) !important; letter-spacing: -0.035em; }
      h2 { font-size: 1.55rem !important; }
      [data-testid="stCaptionContainer"] { color: var(--muted); }
      .sdr-eyebrow { color: var(--lavender); font-size: 0.69rem; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; }
      .app-brand { display: flex; align-items: center; justify-content: space-between; margin: 0.15rem 0 1rem; }
      .app-brand__left { display: flex; align-items: center; gap: 0.7rem; }
      .app-brand__mark { display: grid; place-items: center; width: 2.25rem; height: 2.25rem; border-radius: 0.78rem; background: linear-gradient(135deg, #6d68e9, #9a91f2); color: white; font-size: 0.74rem; font-weight: 850; letter-spacing: -0.06em; }
      .app-brand__name { color: var(--ink); font-size: 1rem; font-weight: 800; letter-spacing: -0.03em; }
      .app-brand__sub { color: var(--muted); font-size: 0.75rem; margin-top: 0.05rem; }
      .app-brand__badge { border: 1px solid #dfe0f5; border-radius: 99px; background: #fff; color: #5b58bd; padding: 0.32rem 0.62rem; font-size: 0.72rem; font-weight: 700; }
      [data-testid="stTabs"] [data-baseweb="tab-list"] { gap: 0.4rem; border-bottom: 0; margin: 0 0 1.25rem; }
      [data-testid="stTabs"] button[role="tab"] { min-height: 2.15rem; padding: 0 0.85rem; border: 1px solid transparent; border-radius: 99px; color: #727288; background: transparent; font-size: 0.78rem; font-weight: 700; }
      [data-testid="stTabs"] button[role="tab"]:hover { color: #4f4cc5; background: #f2f1ff; }
      [data-testid="stTabs"] button[role="tab"][aria-selected="true"] { border-color: #dcdcf1; background: #fff; color: #4f4cc5; box-shadow: 0 2px 7px rgba(46, 43, 109, 0.05); }
      [data-testid="stTabs"] [data-baseweb="tab-highlight"] { display: none; }
      [data-testid="stMetric"] { min-height: 7.1rem; background: var(--surface); border: 1px solid var(--line); border-radius: 0.9rem; box-shadow: 0 5px 18px rgba(38, 37, 67, 0.035); padding: 0.9rem 1rem; }
      [data-testid="stMetricLabel"] { color: #5a5a70; font-size: 0.72rem; font-weight: 750; }
      [data-testid="stMetricValue"] { color: var(--ink); font-size: 1.55rem; font-weight: 800; letter-spacing: -0.045em; }
      [data-testid="stMetricDelta"] { color: #49a890; font-size: 0.7rem; font-weight: 700; }
      [data-testid="stDataFrame"] { overflow: hidden; border: 1px solid var(--line); border-radius: 0.9rem; background: var(--surface); box-shadow: 0 5px 18px rgba(38, 37, 67, 0.025); }
      [data-testid="stExpander"] { border: 1px solid var(--line); border-radius: 0.8rem; background: var(--surface); }
      [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea, [data-testid="stSelectbox"] > div > div, [data-testid="stMultiSelect"] > div > div { border-color: #e1e1ed !important; border-radius: 0.65rem !important; background: #fff !important; }
      .stButton > button, .stDownloadButton > button { border: 1px solid #dfe0f0; border-radius: 0.65rem; background: #fff; color: #4d4a91; font-size: 0.8rem; font-weight: 750; box-shadow: 0 1px 3px rgba(38, 37, 67, 0.03); }
      .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] { border: 0; background: linear-gradient(105deg, #7771e8, #69c8da); color: white; }
      .dashboard-hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 1rem; padding: 0.55rem 0 1rem; }
      .dashboard-hero h1 { margin: 0.18rem 0 0; font-size: 2rem !important; }
      .dashboard-hero p { margin: 0.35rem 0 0; max-width: 42rem; color: var(--muted); font-size: 0.88rem; }
      .dashboard-pill { flex: 0 0 auto; border: 1px solid #dfe0f5; background: #fff; border-radius: 99px; color: #5e5bc2; padding: 0.42rem 0.68rem; font-size: 0.72rem; font-weight: 750; }
      .panel-title { margin: 1.35rem 0 0.55rem; color: var(--ink); font-size: 0.9rem; font-weight: 800; letter-spacing: -0.02em; }
      .priority-panel { min-height: 14.15rem; box-sizing: border-box; border: 1px solid var(--line); border-radius: 0.9rem; background: var(--surface); box-shadow: 0 5px 18px rgba(38, 37, 67, 0.035); padding: 0.95rem; }
      .priority-panel__top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.65rem; }
      .priority-panel__top span { color: var(--ink); font-size: 0.85rem; font-weight: 800; }
      .priority-panel__top b { color: #5f5ccd; font-size: 0.68rem; }
      .priority-item { display: flex; gap: 0.55rem; padding: 0.58rem 0; border-top: 1px solid #f0f0f6; }
      .priority-item:first-of-type { border-top: 0; }
      .priority-dot { flex: 0 0 auto; width: 0.48rem; height: 0.48rem; border-radius: 99px; margin-top: 0.34rem; background: #817bea; box-shadow: 0 0 0 3px #f0efff; }
      .priority-item strong { display: block; color: #424158; font-size: 0.74rem; }
      .priority-item small { display: block; margin-top: 0.13rem; color: #8a8a9b; font-size: 0.66rem; line-height: 1.35; }
      .workflow-note { margin-top: 1.15rem; border: 1px solid #dfe6fc; border-radius: 0.9rem; background: linear-gradient(135deg, #f2f1ff, #eef9fb); padding: 1rem; }
      .workflow-note h3 { margin: 0; font-size: 0.9rem !important; }
      .workflow-note p { margin: 0.35rem 0 0; color: #65657a; font-size: 0.75rem; line-height: 1.5; }
      .lead-count { color: #706be2; font-size: 0.72rem; font-weight: 750; }
      @media (max-width: 780px) {
        .app-brand__badge, .dashboard-pill { display: none; }
        .dashboard-hero { align-items: flex-start; }
        [data-testid="stMetric"] { min-height: 5.7rem; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def resources():
    settings = get_settings()
    engine = create_db_engine(settings)
    initialize_database(engine)
    return settings, build_session_factory(engine)


def csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


def _metric(metrics: dict, key: str, default: int = 0) -> int:
    """Read an integer metric defensively from a persisted research-run payload."""
    try:
        return int(metrics.get(key, default))
    except (AttributeError, TypeError, ValueError):
        return default


def navigate_workspace(page: str) -> None:
    """Switch sections on the next rerun without mutating a live navigation widget."""
    st.session_state["pending_workspace_navigation"] = page
    st.rerun()


def render_saved_contact_detail(
    session_factory,
    prospect_id: int,
    alignment: float | None,
    *,
    view_key: str = "contact_detail",
) -> None:
    """Show the saved, cited record without exposing raw database structure."""
    relationship_note_saved = False
    with session_scope(session_factory) as session:
        prospect = session.get(Prospect, prospect_id)
        if not prospect:
            st.warning("That saved contact is no longer available. Refresh the dashboard and try again.")
            return
        company = prospect.company
        prospect_evidence = list(
            session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.prospect_id == prospect.id)
                .order_by(EvidenceRecord.extracted_at.desc())
            )
        )
        company_evidence = list(
            session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.company_id == company.id, EvidenceRecord.prospect_id.is_(None))
                .order_by(EvidenceRecord.extracted_at.desc())
            )
        )
        related_contacts = list(
            session.scalars(
                select(Prospect)
                .where(Prospect.company_id == company.id, Prospect.id != prospect.id)
                .order_by(Prospect.confidence_score.desc(), Prospect.full_name)
            )
        )
        relationship_notes = list(
            session.scalars(
                select(RelationshipNote)
                .where(RelationshipNote.prospect_id == prospect.id)
                .order_by(RelationshipNote.created_at.desc())
            )
        )
        evidence = {item.id: item for item in [*prospect_evidence, *company_evidence]}

        st.divider()
        st.subheader(f"{prospect.full_name} · {prospect.role}")
        score_columns = st.columns(6)
        score_columns[0].metric("Company", company.name)
        score_columns[1].metric("ICP alignment", f"{alignment:.2f}" if alignment is not None else "Not scored")
        score_columns[2].metric("Confidence", f"{prospect.confidence_score:.2f}")
        score_columns[3].metric("Review status", prospect.review_status.replace("_", " ").title())
        score_columns[4].metric("Outreach stage", outreach_stage_label(prospect.outreach_stage))
        score_columns[5].metric(
            "Next action",
            prospect.next_action_at.date().isoformat() if prospect.next_action_at else "Not scheduled",
        )

        overview, context_tab, evidence_tab, signals_tab = st.tabs(
            ["Overview", "Relationship context", "Evidence", "Signals & related contacts"]
        )
        with overview:
            left, right = st.columns(2)
            with left:
                st.markdown(f"**Industry:** {company.industry or 'Not established'}")
                st.markdown(f"**Geography:** {company.geography or 'Not established'}")
                st.markdown(f"**Company size:** {company.company_size_band or 'Not established'}")
                st.markdown(f"**Account audience:** {account_type_label(company.account_type)}")
                if company.website_url:
                    st.link_button("Open company website", company.website_url)
            with right:
                public_contact = prospect.email or prospect.phone or prospect.profile_url
                st.markdown(f"**Public contact:** {public_contact or 'Not published'}")
                if prospect.email:
                    st.markdown(f"**Work email:** {prospect.email}")
                if prospect.phone:
                    st.markdown(f"**Work phone:** {prospect.phone}")
                if prospect.profile_url:
                    st.link_button("Open public profile", prospect.profile_url)
                if prospect.linkedin_url:
                    st.link_button("Open saved LinkedIn profile", prospect.linkedin_url)
                st.caption(
                    "Approve this prospect in Review queue, then manage LinkedIn lookup and outreach in the Outreach workspace."
                )
        with context_tab:
            st.caption(
                "Save professional context, education, projects, research, or conversation notes. Keep private family and sensitive personal information out of this workspace."
            )
            if relationship_notes:
                st.dataframe(
                    [
                        {
                            "Bucket": relationship_note_label(note.bucket),
                            "Context": note.content,
                            "Source": note.source_type.replace("_", " ").title(),
                            "Captured": note.created_at.isoformat(timespec="minutes"),
                        }
                        for note in relationship_notes
                    ],
                    hide_index=True,
                    width="stretch",
                )
                for note in relationship_notes:
                    if note.source_url:
                        st.link_button(
                            f"Open source for {relationship_note_label(note.bucket)}",
                            note.source_url,
                            key=f"{view_key}_relationship_source_{note.id}",
                        )
            else:
                st.caption("No relationship context saved yet. New research will add cited professional signals here.")
            with st.form(f"{view_key}_relationship_note_{prospect.id}"):
                bucket = st.selectbox(
                    "Context bucket",
                    list(RELATIONSHIP_NOTE_BUCKETS),
                    format_func=relationship_note_label,
                )
                note_content = st.text_area(
                    "Add a professional relationship note",
                    placeholder="Example: Led the Austin land-development team through a new community launch.",
                )
                note_source = st.text_input("Optional public source URL")
                save_relationship_note = st.form_submit_button("Save relationship note")
            if save_relationship_note:
                try:
                    add_relationship_note(
                        session,
                        prospect,
                        bucket=bucket,
                        content=note_content,
                        source_url=note_source or None,
                        source_type="public" if note_source else "manual",
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Relationship context saved locally.")
                    relationship_note_saved = True
        with evidence_tab:
            if not evidence:
                st.info("No cited evidence records are stored for this contact yet.")
            else:
                evidence_rows = [
                    {
                        "Field": record.field_name.replace("_", " ").title(),
                        "Value": record.value,
                        "Source": record.source_type,
                        "Freshness": record.freshness_status,
                        "Verified": record.verified,
                        "Captured": record.extracted_at.isoformat(timespec="seconds"),
                    }
                    for record in evidence.values()
                ]
                st.dataframe(evidence_rows, hide_index=True, width="stretch")
                for record in evidence.values():
                    with st.expander(f"{record.field_name.replace('_', ' ').title()} · {record.source_type}"):
                        st.caption(record.supporting_excerpt)
                        st.link_button("Open cited source", record.source_url, key=f"{view_key}_source_{record.id}")
        with signals_tab:
            signal_left, signal_right = st.columns(2)
            with signal_left:
                st.markdown("#### Public rapport signals")
                if prospect.rapport_signals:
                    st.json(prospect.rapport_signals)
                else:
                    st.caption("No prospect-level public signals stored.")
            with signal_right:
                st.markdown("#### Other saved contacts")
                if related_contacts:
                    st.dataframe(
                        [
                            {
                                "Name": item.full_name,
                                "Role": item.role,
                                "Confidence": round(item.confidence_score, 2),
                                "Status": item.review_status,
                            }
                            for item in related_contacts
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    st.caption("No other saved contacts for this company.")
            st.markdown("#### Account signals")
            if company.account_signals:
                st.dataframe(
                    [
                        {
                            "Signal": item.kind.replace("_", " ").title(),
                            "Description": item.description,
                            "Source type": item.source_type,
                            "Freshness": item.freshness_status,
                        }
                        for item in company.account_signals
                    ],
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption("No account-level signals stored.")
    if relationship_note_saved:
        st.rerun()


def render_home(session_factory) -> None:
    """Render a concise command center instead of a second copy of the prospect ledger."""
    with session_scope(session_factory) as session:
        snapshot = build_dashboard_snapshot(session)

    title_column, action_column = st.columns([4.5, 1.5])
    with title_column:
        st.markdown(
            """
            <div class="dashboard-hero">
              <div>
                <div class="sdr-eyebrow">Home</div>
                <h1>What needs your attention?</h1>
                <p>Start a search, review the newest owner/developer prospects, and keep follow-up moving without digging through the full database.</p>
              </div>
              <span class="dashboard-pill">Local · evidence-backed</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with action_column:
        st.markdown("<div style='height: 1.9rem'></div>", unsafe_allow_html=True)
        if st.button("Start new research", type="primary", use_container_width=True, key="home_start_research"):
            navigate_workspace("Research")
        if st.button("Open pipeline", use_container_width=True, key="home_open_pipeline"):
            navigate_workspace("Pipeline")

    metric_columns = st.columns(4)
    metric_columns[0].metric("Direct owner accounts", snapshot.totals["direct_accounts"])
    metric_columns[1].metric("Needs review", snapshot.totals["reviewable"])
    metric_columns[2].metric("Outreach in motion", snapshot.totals["active_outreach"])
    metric_columns[3].metric("Published work contacts", snapshot.totals["published_contacts"])

    latest_run = snapshot.runs[0] if snapshot.runs else None
    latest_rows = [
        item for item in snapshot.prospects if latest_run and latest_run["id"] in item["run_ids"]
    ]
    latest_rows.sort(key=lambda item: (item["best_alignment"] is None, -(item["best_alignment"] or 0), -item["confidence"]))

    latest_column, priority_column = st.columns([2.2, 1])
    with latest_column:
        st.markdown("<div class='panel-title'>Newest research</div>", unsafe_allow_html=True)
        if not latest_run:
            st.info("No research has been saved yet. Start a new public-web research run to populate your local pipeline.")
        else:
            latest_metrics = latest_run["metrics"]
            st.caption(
                f"Latest run · {latest_run['status'].replace('_', ' ').title()} · "
                f"{_metric(latest_metrics, 'qualified_prospects')} qualified prospects"
            )
            st.dataframe(
                [
                    {
                        "Contact": item["full_name"],
                        "Company": item["company"],
                        "Role": item["role"],
                        "Fit": item["best_alignment"],
                        "Review": item["review_status"].replace("_", " ").title(),
                    }
                    for item in latest_rows[:6]
                ],
                hide_index=True,
                width="stretch",
                column_config={"Fit": st.column_config.NumberColumn(format="%.2f")},
            )
            if len(latest_rows) > 6:
                st.caption(f"Showing 6 of {len(latest_rows)} prospects from this run.")
    with priority_column:
        st.markdown(
            f"""
            <div class="priority-panel">
              <div class="priority-panel__top"><span>Priority actions</span><b>Today</b></div>
              <div class="priority-item"><i class="priority-dot"></i><div><strong>Review {snapshot.totals['reviewable']} researched prospects</strong><small>Approve strong fits before beginning outreach.</small></div></div>
              <div class="priority-item"><i class="priority-dot"></i><div><strong>Find {snapshot.totals['needs_linkedin']} LinkedIn profiles</strong><small>Save the public profile URL before your first touch.</small></div></div>
              <div class="priority-item"><i class="priority-dot"></i><div><strong>Move {snapshot.totals['active_outreach']} active conversations</strong><small>Record the next action so the local queue stays current.</small></div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("Review prospects", use_container_width=True, key="home_review_prospects"):
            navigate_workspace("Pipeline")

    if not snapshot.runs:
        return

    st.markdown("<div class='panel-title'>Recent research runs</div>", unsafe_allow_html=True)
    st.dataframe(
        [
            {
                "Run": item["id"][:8],
                "Status": item["status"].replace("_", " ").title(),
                "Accounts": _metric(item["metrics"], "accounts_discovered"),
                "Qualified": _metric(item["metrics"], "qualified_prospects"),
                "Created": item["created_at"].replace("T", " ")[:16],
            }
            for item in snapshot.runs[:5]
        ],
        hide_index=True,
        width="stretch",
    )


def render_pipeline(session_factory) -> None:
    """Render the searchable prospect ledger and its record-detail view."""
    with session_scope(session_factory) as session:
        snapshot = build_dashboard_snapshot(session)

    title_column, refresh_column = st.columns([5, 1])
    with title_column:
        st.markdown("<div class='sdr-eyebrow'>Pipeline</div>", unsafe_allow_html=True)
        st.subheader("Prospects, evidence, and follow-up")
        st.caption("Browse every saved person, open the full record, and keep review plus outreach status current.")
    with refresh_column:
        st.markdown("<div style='height: 1.7rem'></div>", unsafe_allow_html=True)
        if st.button("Refresh", key="pipeline_refresh", use_container_width=True):
            st.rerun()

    if not snapshot.runs:
        st.info("No saved research runs yet. Start a public-web research run from Research to populate this pipeline.")
        return

    run_options = {"All saved contacts": None, **{item["label"]: item["id"] for item in snapshot.runs}}
    selected_label = st.selectbox("Show results from", list(run_options), key="dashboard_run")
    selected_run_id = run_options[selected_label]
    selected_run = next((item for item in snapshot.runs if item["id"] == selected_run_id), None)
    if selected_run:
        metrics = selected_run["metrics"]
        target_accounts = _metric(metrics, "target_accounts")
        target_contacts = _metric(metrics, "target_qualified_prospects")
        saved_accounts = _metric(metrics, "accounts_discovered")
        qualified_contacts = _metric(metrics, "qualified_prospects")
        coverage_columns = st.columns(4)
        coverage_columns[0].metric("Run status", selected_run["status"].replace("_", " ").title())
        coverage_columns[1].metric("Accounts saved", f"{saved_accounts} / {target_accounts}" if target_accounts else saved_accounts)
        coverage_columns[2].metric("Qualified contacts", f"{qualified_contacts} / {target_contacts}" if target_contacts else qualified_contacts)
        coverage_columns[3].metric("Alignment threshold", f"{metrics.get('qualified_prospect_alignment_threshold', 0):.2f}")
        shortfalls = metrics.get("shortfall_reasons", []) or metrics.get("worker_shortfall_reasons", [])
        if shortfalls:
            for reason in shortfalls:
                st.warning(reason)
        elif selected_run["status"] == "completed":
            st.success("This run met its recorded delivery targets.")

    scoped_rows = [
        item
        for item in snapshot.prospects
        if selected_run_id is None or selected_run_id in item["run_ids"]
    ]
    audience_options = {
        "Direct owners/developers": AccountType.OWNER_DEVELOPER.value,
        "Potential partners/service firms": AccountType.PROFESSIONAL_SERVICES.value,
        "Needs account-type verification": AccountType.UNKNOWN.value,
        "All saved contacts": None,
    }
    selected_audience_label = st.radio(
        "Account audience",
        list(audience_options),
        horizontal=True,
        key="dashboard_account_audience",
    )
    selected_account_type = audience_options[selected_audience_label]
    if selected_account_type is not None:
        scoped_rows = [item for item in scoped_rows if item["account_type"] == selected_account_type]
    if not scoped_rows:
        st.info("This scope has no saved contacts in the selected account audience yet.")
        return

    statuses = sorted({item["review_status"] for item in scoped_rows})
    geographies = sorted({item["geography"] for item in scoped_rows})
    filter_columns = st.columns([2, 1, 1])
    with filter_columns[0]:
        search = st.text_input("Search company, contact, title, or location", key="dashboard_search")
    with filter_columns[1]:
        selected_statuses = st.multiselect("Review status", statuses, default=statuses, key="dashboard_status")
    with filter_columns[2]:
        selected_geographies = st.multiselect("Geography", geographies, default=geographies, key="dashboard_geo")

    query = search.casefold().strip()
    filtered_rows = []
    for item in scoped_rows:
        search_text = " ".join(
            str(item[field]) for field in ("company", "full_name", "role", "industry", "geography", "domain")
        ).casefold()
        if query and query not in search_text:
            continue
        if item["review_status"] not in selected_statuses or item["geography"] not in selected_geographies:
            continue
        alignment = item["alignment_by_run"].get(selected_run_id, item["best_alignment"])
        filtered_rows.append({**item, "alignment": alignment})
    filtered_rows.sort(key=lambda item: (item["alignment"] is None, -(item["alignment"] or 0), -item["confidence"], item["company"]))

    table_column, guide_column = st.columns([2.2, 1])
    table_selection = None
    with table_column:
        st.markdown(f"<div class='panel-title'>Saved prospects <span class='lead-count'>{len(filtered_rows)} in this view</span></div>", unsafe_allow_html=True)
        if not filtered_rows:
            st.info("No saved contacts match these filters.")
            return
        table_selection = st.dataframe(
            [
                {
                    "Company": item["company"],
                    "Contact": item["full_name"],
                    "Role": item["role"],
                    "Geography": item["geography"],
                    "Alignment": item["alignment"],
                    "Evidence": item["evidence_records"],
                    "Public contact": "Yes" if item["public_contact"] else "No",
                    "Review": item["review_status"].replace("_", " ").title(),
                    "Outreach": item["outreach_stage_label"],
                    "Next action": item["next_action_at"] or "—",
                }
                for item in filtered_rows
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "Alignment": st.column_config.NumberColumn(format="%.2f"),
            },
            key="pipeline_prospect_table",
            on_select="rerun",
            selection_mode="single-row",
        )
    with guide_column:
        st.markdown(
            """
            <div class="workflow-note">
              <h3>Suggested flow</h3>
              <p><b>1.</b> Inspect evidence and approve the right owner.</p>
              <p><b>2.</b> Save LinkedIn after you find it manually.</p>
              <p><b>3.</b> Capture an outreach note and schedule the next move.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    selected_rows = list(table_selection.selection.rows) if table_selection else []
    if selected_rows:
        selected_contact = filtered_rows[selected_rows[0]]
        st.markdown("<div class='panel-title'>Selected prospect</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='panel-title'>Open a saved prospect</div>", unsafe_allow_html=True)
        st.caption("Select a table row to open it directly, or choose a contact below.")
        contacts_by_label = {
            f"{item['full_name']} — {item['company']} ({item['role']})": item
            for item in filtered_rows
        }
        detail_label = st.selectbox("Open a saved contact", list(contacts_by_label), key="dashboard_contact")
        selected_contact = contacts_by_label[detail_label]
    render_saved_contact_detail(
        session_factory,
        selected_contact["id"],
        selected_contact["alignment"],
        view_key="dashboard_contact_detail",
    )


def render_intake(session_factory, settings) -> None:
    st.subheader("Find new prospects")
    st.caption(
        "The saved JSON is sent verbatim to the browser-research agents. "
        f"This run targets {settings.max_accounts_per_run} accounts and "
        f"{settings.target_qualified_prospects_per_run} qualified prospects."
    )
    with st.form("icp_form"):
        left, right = st.columns(2)
        with left:
            industry = st.text_input("Industry", placeholder="B2B SaaS")
            company_size_band = st.selectbox("Company size", ["1–10", "11–50", "51–200", "201–1,000", "1,000+"])
            geography = st.text_input("Target geography", value="United States")
            account_audience = st.selectbox(
                "Account audience",
                ["Direct owners/developers", "Any company matching the ICP"],
                help="Direct-owner runs exclude engineering, surveying, consulting, and similar service providers.",
            )
        with right:
            pain_points = st.text_area("Pain points (comma-separated)", placeholder="Manual reporting, slow sales handoffs")
            titles = st.text_area("Target job titles (comma-separated)", placeholder="VP Sales, Head of Revenue Operations")
            personas = st.text_area("Adjacent personas (comma-separated)", placeholder="Sales Operations Manager, Chief Revenue Officer")
        notes = st.text_area("Optional notes", placeholder="Exclusions, vertical focus, language, or buying context")
        submitted = st.form_submit_button("Save ICP JSON")
    if submitted:
        try:
            icp = ICPProfile(
                industry=industry,
                company_size_band=company_size_band,
                geography=geography,
                pain_points=csv_values(pain_points),
                target_job_titles=csv_values(titles),
                adjacent_personas=csv_values(personas),
                target_account_type=(
                    TargetAccountType.OWNER_DEVELOPER
                    if account_audience == "Direct owners/developers"
                    else TargetAccountType.ANY
                ),
                notes=notes or None,
            )
        except Exception as exc:
            st.error(f"Please correct the ICP: {exc}")
        else:
            st.session_state["icp"] = icp.model_dump(mode="json")
            st.success("ICP saved for this browser session.")
    if not st.session_state.get("icp"):
        return

    st.json(st.session_state["icp"])
    st.download_button(
        "Download ICP JSON",
        data=json.dumps(st.session_state["icp"], indent=2),
        file_name="icp.json",
        mime="application/json",
    )
    if settings.research_mode == "codex_handoff":
        st.info("Codex handoff mode is active. Queue the run here, then ask your signed-in Codex agent to process it. No API key is needed.")
        if st.button("Queue research for Codex", type="primary"):
            icp = ICPProfile.model_validate(st.session_state["icp"])
            run_id = queue_codex_run(session_factory, icp, settings)
            st.session_state["last_run_id"] = run_id
            st.success(f"Queued run {run_id}.")
            st.code(handoff_prompt(run_id), language=None)
            st.caption(f"Job file: {job_path(run_id)}")
        return
    if not settings.llm_api_key:
        st.warning("API mode needs LLM_API_KEY in `.env`. Set RESEARCH_MODE=codex_handoff to use your signed-in Codex agent instead.")
    if st.button("Run public-web research", type="primary", disabled=not bool(settings.llm_api_key)):
        icp = ICPProfile.model_validate(st.session_state["icp"])
        messages = st.status("Starting research", expanded=True)

        def progress(stage: str, message: str) -> None:
            messages.write(f"**{stage.title()}** — {message}")

        orchestrator = ProspectingOrchestrator(session_factory, BrowserUseRunner(settings), settings)
        try:
            result = asyncio.run(orchestrator.run(icp, progress=progress))
        except Exception as exc:  # defensive UI boundary
            messages.update(label="Research failed", state="error")
            st.exception(exc)
            return
        messages.update(
            label=f"Research {result.status}",
            state="complete" if result.status in {"completed", "completed_with_shortfall"} else "error",
        )
        st.session_state["last_run_id"] = result.run_id
        st.json(result.model_dump())


def render_active_runs(session_factory, settings) -> None:
    st.subheader("Research runs")
    with session_scope(session_factory) as session:
        runs = list(session.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc())))
        snapshot = [
            {
                "id": run.id,
                "status": run.status,
                "created": run.created_at.isoformat(timespec="seconds"),
                "metrics": run.metrics,
                "errors": run.errors,
                "cancel_requested": run.cancel_requested,
            }
            for run in runs
        ]
    if not snapshot:
        st.info("No research runs yet.")
        return
    st.dataframe(snapshot, width="stretch")
    active = [
        run
        for run in snapshot
        if run["status"] in {"queued", "queued_for_codex", "running"} and not run["cancel_requested"]
    ]
    if active:
        run_id = st.selectbox("Active run", [run["id"] for run in active], key="cancel_run_id")
        if st.button("Request cancellation"):
            with session_scope(session_factory) as session:
                request_run_cancel(session, run_id)
            st.success("Cancellation requested; running agents stop before their next task.")
    finished = [run for run in snapshot if run["status"] in {"completed", "completed_with_shortfall"}]
    if finished:
        refresh_run_id = st.selectbox("Recalculate completed run", [run["id"] for run in finished], key="rescore_run")
        if st.button("Recalculate alignment and delivery coverage"):
            try:
                result = rescore_run(session_factory, settings, refresh_run_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(
                    f"Rescored {result.run_id}: {result.metrics['qualified_prospects']} qualified prospects "
                    f"from {result.metrics['contacts_discovered']} discovered."
                )
                st.rerun()
    short_runs = [run for run in snapshot if run["status"] == "completed_with_shortfall"]
    if short_runs:
        st.warning("Some completed runs did not meet their delivery targets. You can create a fresh, deduplicated follow-up job.")
        source_run_id = st.selectbox("Run to top up", [run["id"] for run in short_runs], key="gap_fill_run")
        selected = next(run for run in short_runs if run["id"] == source_run_id)
        for reason in selected["metrics"].get("shortfall_reasons", []):
            st.caption(reason)
        if st.button("Queue gap-filling research job", type="primary"):
            try:
                follow_up_id = queue_gap_fill_run(session_factory, settings, source_run_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"Queued follow-up run {follow_up_id}.")
                st.code(handoff_prompt(follow_up_id), language=None)


def render_review_queue(session_factory) -> None:
    st.subheader("Review researched prospects")
    needs_rerun = False
    with session_scope(session_factory) as session:
        runs = list(session.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc())))
        run_options = {"All runs": None, **{f"{run.id[:8]} — {run.status}": run.id for run in runs}}
    selected_label = st.selectbox("Research run", list(run_options), key="review_run")
    run_id = run_options[selected_label]
    review_audience = st.radio(
        "Account audience",
        ["Direct owners/developers", "All pending prospects"],
        horizontal=True,
        key="review_account_audience",
    )
    with session_scope(session_factory) as session:
        queue = list_review_queue(
            session,
            run_id,
            account_type=(
                AccountType.OWNER_DEVELOPER.value
                if review_audience == "Direct owners/developers"
                else None
            ),
        )
        cards = [
            {
                "id": item.id,
                "name": item.full_name,
                "role": item.role,
                "company": item.company.name,
                "confidence": item.confidence_score,
                "status": item.review_status,
            }
            for item in queue
        ]
    if not cards:
        st.info("No pending prospects in this queue.")
        return
    selection = st.selectbox("Prospect", [f"{card['name']} — {card['company']}" for card in cards])
    prospect_id = cards[[f"{card['name']} — {card['company']}" for card in cards].index(selection)]["id"]
    with session_scope(session_factory) as session:
        prospect = session.get(Prospect, prospect_id)
        evidence = list(session.scalars(select(EvidenceRecord).where(EvidenceRecord.prospect_id == prospect_id)))
        st.markdown(f"### {prospect.full_name} · {prospect.role}")
        st.write(f"**Company:** {prospect.company.name}  \\n+**Confidence:** {prospect.confidence_score:.2f}  \\n+**Public contact:** {prospect.email or prospect.phone or prospect.profile_url or 'Not published'}")
        for record in evidence:
            st.markdown(f"- **{record.field_name}**: {record.value} — [{record.source_type}]({record.source_url}) · `{record.freshness_status}`")
            st.caption(record.supporting_excerpt)
        with st.expander("Edit prospect fields"):
            edited_name = st.text_input("Name", value=prospect.full_name, key=f"edit_name_{prospect_id}")
            edited_role = st.text_input("Role", value=prospect.role, key=f"edit_role_{prospect_id}")
            edited_email = st.text_input("Work email", value=prospect.email or "", key=f"edit_email_{prospect_id}")
            edited_phone = st.text_input("Work phone", value=prospect.phone or "", key=f"edit_phone_{prospect_id}")
            edited_profile = st.text_input("Public profile URL", value=prospect.profile_url or "", key=f"edit_profile_{prospect_id}")
            edited_owner = st.text_input("Owner", value=prospect.owner or "", key=f"edit_owner_{prospect_id}")
        notes = st.text_area("Review notes", key=f"notes_{prospect_id}")
        action_columns = st.columns(5)
        actions = [("Approve", "approve"), ("Reject", "reject"), ("Wrong role", "wrong_role"), ("Stale", "stale"), ("Save edit", "edit")]
        for column, (label, action) in zip(action_columns, actions):
            if column.button(label, key=f"{action}_{prospect_id}"):
                edits = None
                if action == "edit":
                    edits = {
                        "full_name": edited_name or prospect.full_name,
                        "role": edited_role or prospect.role,
                        "email": edited_email or None,
                        "phone": edited_phone or None,
                        "profile_url": edited_profile or None,
                        "owner": edited_owner or None,
                    }
                apply_review(session, prospect, action, research_run_id=run_id, notes=notes, edits=edits)
                st.success(f"Saved {action}.")
                needs_rerun = True
        feedback_label = st.selectbox("Fit feedback", ["good_fit", "bad_fit", "wrong_role", "stale"], key=f"feedback_{prospect_id}")
        if st.button("Record fit feedback", key=f"feedback_button_{prospect_id}"):
            record_feedback(session, prospect, feedback_label, research_run_id=run_id, persona_bucket="target", notes=notes)
            st.success("Feedback recorded. It affects ICP alignment only after enough reviews.")
        st.markdown("#### Rapport signals")
        st.json(prospect.rapport_signals)
    if needs_rerun:
        st.rerun()


def render_outreach_workspace(session_factory) -> None:
    """Turn approved prospects into a compact, local SDR follow-up queue."""
    st.subheader("Outreach workspace")
    st.caption(
        "Approve a researched prospect, then use this local pipeline to track LinkedIn lookup, manual outreach, replies, and demos."
    )
    audience = st.radio(
        "Show",
        ["Direct owners/developers", "All approved prospects"],
        horizontal=True,
        key="outreach_audience",
    )
    with session_scope(session_factory) as session:
        statement = (
            select(Prospect)
            .options(joinedload(Prospect.company))
            .join(Company)
            .where(Prospect.review_status == "approved")
            .order_by(Prospect.next_action_at.is_(None), Prospect.next_action_at, Prospect.full_name)
        )
        if audience == "Direct owners/developers":
            statement = statement.where(Company.account_type == AccountType.OWNER_DEVELOPER.value)
        prospects = list(session.scalars(statement))

    if not prospects:
        st.info("Approve a direct-owner prospect in Review queue to begin local outreach tracking.")
        return

    today = datetime.now(timezone.utc).date()
    due_today = sum(
        item.next_action_at is not None and item.next_action_at.date() <= today
        for item in prospects
    )
    metrics = st.columns(5)
    metrics[0].metric("Approved", len(prospects))
    metrics[1].metric("Find on LinkedIn", sum(item.outreach_stage == "find_on_linkedin" for item in prospects))
    metrics[2].metric("Due now", due_today)
    metrics[3].metric("Messages sent", sum(item.outreach_stage == "message_sent" for item in prospects))
    metrics[4].metric("Demos booked", sum(item.outreach_stage == "demo_booked" for item in prospects))

    stages = ["All stages", *[outreach_stage_label(stage) for stage in OUTREACH_STAGES]]
    selected_stage_label = st.selectbox("Filter outreach stage", stages, key="outreach_stage_filter")
    selected_stage = next(
        (stage for stage in OUTREACH_STAGES if outreach_stage_label(stage) == selected_stage_label),
        None,
    )
    visible = [item for item in prospects if selected_stage is None or item.outreach_stage == selected_stage]
    st.dataframe(
        [
            {
                "Next action": item.next_action_at.date().isoformat() if item.next_action_at else "—",
                "Company": item.company.name,
                "Contact": item.full_name,
                "Role": item.role,
                "Stage": outreach_stage_label(item.outreach_stage),
                "LinkedIn": "Saved" if item.linkedin_url else "Not saved",
                "Work contact": "Yes" if item.email or item.phone else "No",
            }
            for item in visible
        ],
        hide_index=True,
        width="stretch",
    )
    if not visible:
        st.info("No approved prospects match that outreach stage.")
        return

    choices = {
        f"{item.full_name} — {item.company.name} ({outreach_stage_label(item.outreach_stage)})": item.id
        for item in visible
    }
    selected = st.selectbox("Update an approved prospect", list(choices), key="outreach_prospect")
    prospect_id = choices[selected]
    rerun = False
    with session_scope(session_factory) as session:
        prospect = session.get(Prospect, prospect_id)
        activities = list(
            session.scalars(
                select(OutreachActivity)
                .where(OutreachActivity.prospect_id == prospect.id)
                .order_by(OutreachActivity.occurred_at.desc())
            )
        )
        st.markdown(f"### {prospect.full_name} · {prospect.role}")
        detail_columns = st.columns(3)
        detail_columns[0].markdown(f"**Company**  \n{prospect.company.name}")
        detail_columns[1].markdown(f"**Public work contact**  \n{prospect.email or prospect.phone or 'Not published'}")
        detail_columns[2].markdown(f"**Current stage**  \n{outreach_stage_label(prospect.outreach_stage)}")
        link_columns = st.columns(3)
        if prospect.company.website_url:
            link_columns[0].link_button("Company website", prospect.company.website_url)
        if prospect.profile_url:
            link_columns[1].link_button("Public profile", prospect.profile_url)
        if prospect.linkedin_url:
            link_columns[2].link_button("Saved LinkedIn", prospect.linkedin_url)

        stage_options = list(OUTREACH_STAGES)
        current_stage = prospect.outreach_stage if prospect.outreach_stage in stage_options else "find_on_linkedin"
        with st.form(f"outreach_update_{prospect.id}"):
            stage = st.selectbox(
                "Outcome / next stage",
                stage_options,
                index=stage_options.index(current_stage),
                format_func=outreach_stage_label,
            )
            linkedin_url = st.text_input("LinkedIn URL (save after you find it)", value=prospect.linkedin_url or "")
            schedule_follow_up = st.checkbox(
                "Schedule a next action",
                value=bool(prospect.next_action_at) and stage not in {"demo_booked", "closed_lost"},
            )
            next_date = st.date_input(
                "Next action date",
                value=(prospect.next_action_at.date() if prospect.next_action_at else today),
                disabled=not schedule_follow_up or stage in {"demo_booked", "closed_lost"},
            )
            notes = st.text_area(
                "Activity note",
                placeholder="Example: Found profile; connection request sent with a land-development reference.",
            )
            saved = st.form_submit_button("Save outreach update", type="primary")
        if saved:
            next_action_at = (
                datetime.combine(next_date, time.min, tzinfo=timezone.utc)
                if schedule_follow_up and stage not in {"demo_booked", "closed_lost"}
                else None
            )
            update_outreach(
                session,
                prospect,
                stage=stage,
                notes=notes or None,
                linkedin_url=linkedin_url or None,
                next_action_at=next_action_at,
            )
            st.success("Outreach activity saved locally.")
            rerun = True

        st.markdown("#### Activity history")
        if activities:
            st.dataframe(
                [
                    {
                        "When": item.occurred_at.isoformat(timespec="minutes"),
                        "Stage": outreach_stage_label(item.stage),
                        "Note": item.notes or "—",
                        "Next action": item.next_action_at.date().isoformat() if item.next_action_at else "—",
                    }
                    for item in activities
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No outreach activity yet. Your first saved update creates the local history.")
    if rerun:
        st.rerun()


def render_account_view(session_factory) -> None:
    st.subheader("Account workspace")
    st.caption("Open an account to see its company profile, all researched people, cited evidence, and current outreach state.")
    with session_scope(session_factory) as session:
        companies = list(session.scalars(select(Company).order_by(Company.name)))
        choices = {f"{company.name} ({company.canonical_domain})": company.id for company in companies}
    if not choices:
        st.info("Accounts will appear after a research run or CSV import.")
        return
    choice = st.selectbox("Account", list(choices), key="account")
    detail_prospect_id: int | None = None
    with session_scope(session_factory) as session:
        company = session.get(Company, choices[choice])
        contacts = list(
            session.scalars(
                select(Prospect)
                .where(Prospect.company_id == company.id)
                .order_by(Prospect.confidence_score.desc(), Prospect.full_name)
            )
        )
        evidence = list(
            session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.company_id == company.id, EvidenceRecord.prospect_id.is_(None))
                .order_by(EvidenceRecord.extracted_at.desc())
            )
        )
        approved = [item for item in contacts if item.review_status == "approved"]
        published_methods = sum(bool(item.email or item.phone or item.profile_url) for item in contacts)
        metrics = st.columns(5)
        metrics[0].metric("Researched contacts", len(contacts))
        metrics[1].metric("Approved", len(approved))
        metrics[2].metric("Published contact methods", published_methods)
        metrics[3].metric("Cited account evidence", len(evidence))
        metrics[4].metric("Account audience", account_type_label(company.account_type))

        profile_tab, people_tab, evidence_tab = st.tabs(["Company profile", "People", "Evidence & signals"])
        with profile_tab:
            left, right = st.columns(2)
            with left:
                st.markdown(f"### {company.name}")
                st.markdown(f"**Industry:** {company.industry or 'Not established'}")
                st.markdown(f"**Geography:** {company.geography or 'Not established'}")
                st.markdown(f"**Company size:** {company.company_size_band or 'Not established'}")
                st.markdown(f"**Domain:** {company.canonical_domain}")
                if company.website_url:
                    st.link_button("Open company website", company.website_url)
            with right:
                st.markdown("#### Research brief")
                st.markdown(account_brief(session, company))
        with people_tab:
            if not contacts:
                st.info("No people have been researched for this account yet.")
            else:
                st.dataframe(
                    [
                        {
                            "Contact": item.full_name,
                            "Role": item.role,
                            "Work email": item.email or "—",
                            "Work phone": item.phone or "—",
                            "Review": item.review_status.replace("_", " ").title(),
                            "Outreach": outreach_stage_label(item.outreach_stage),
                            "Next action": item.next_action_at.date().isoformat() if item.next_action_at else "—",
                            "Confidence": round(item.confidence_score, 2),
                        }
                        for item in contacts
                    ],
                    hide_index=True,
                    width="stretch",
                )
                contact_choices = {
                    f"{item.full_name} — {item.role}": item.id
                    for item in contacts
                }
                selected_contact = st.selectbox("Open a person's full research record", list(contact_choices), key="account_contact")
                detail_prospect_id = contact_choices[selected_contact]
        with evidence_tab:
            if evidence:
                for record in evidence:
                    with st.expander(f"{record.field_name.replace('_', ' ').title()} · {record.source_type}"):
                        st.markdown(f"**{record.value}**")
                        st.caption(record.supporting_excerpt)
                        st.link_button("Open cited source", record.source_url, key=f"account_source_{record.id}")
            else:
                st.caption("No company-level evidence stored yet.")
            if company.account_signals:
                st.markdown("#### Public account signals")
                st.dataframe(
                    [
                        {
                            "Signal": item.kind.replace("_", " ").title(),
                            "Description": item.description,
                            "Source": item.source_type,
                            "Freshness": item.freshness_status,
                        }
                        for item in company.account_signals
                    ],
                    hide_index=True,
                    width="stretch",
                )
    if detail_prospect_id is not None:
        render_saved_contact_detail(
            session_factory,
            detail_prospect_id,
            alignment=None,
            view_key="account_contact_detail",
        )


def render_export(session_factory) -> None:
    st.subheader("Data and exports")
    with session_scope(session_factory) as session:
        runs = list(session.scalars(select(ResearchRun).order_by(ResearchRun.created_at.desc())))
    options = {"All approved prospects": None, **{f"{run.id[:8]} — {run.status}": run.id for run in runs}}
    selection = st.selectbox("Export scope", list(options), key="export_run")
    run_id = options[selection]
    with session_scope(session_factory) as session:
        st.download_button("Download approved CSV", export_csv(session, run_id), "approved_prospects.csv", "text/csv")
        st.download_button("Download approved JSON", export_json(session, run_id), "approved_prospects.json", "application/json")
        approved = [item for item in session.scalars(select(Prospect).where(Prospect.review_status == "approved"))]
        if approved:
            selected = st.selectbox("Generate message angles for", [f"{item.full_name} — {item.company.name}" for item in approved])
            prospect = approved[[f"{item.full_name} — {item.company.name}" for item in approved].index(selected)]
            st.write(message_angles(prospect))
    st.divider()
    st.markdown("#### Local CSV import")
    uploaded = st.file_uploader("Import companies/prospects CSV", type="csv")
    if uploaded and st.button("Import CSV"):
        with session_scope(session_factory) as session:
            result = import_prospects_csv(session, uploaded.getvalue())
        st.success(f"Import complete: {result}")
    st.markdown("#### Suppression list")
    suppress_type = st.selectbox("Suppression field", ["email", "name", "domain"], key="suppression_type")
    suppress_value = st.text_input("Value to suppress", key="suppression_value")
    suppress_reason = st.text_input("Reason (optional)", key="suppression_reason")
    if st.button("Add suppression") and suppress_value:
        with session_scope(session_factory) as session:
            add_suppression(session, suppress_type, suppress_value, suppress_reason or None)
        st.success("Suppression saved; exports will exclude matching prospects.")


def main() -> None:
    settings, session_factory = resources()
    pending_page = st.session_state.pop("pending_workspace_navigation", None)
    if pending_page:
        st.session_state["workspace_nav"] = pending_page
    if "workspace_nav" not in st.session_state:
        st.session_state["workspace_nav"] = "Home"
    st.markdown(
        """
        <div class="app-brand">
          <div class="app-brand__left">
            <div class="app-brand__mark">LD</div>
            <div><div class="app-brand__name">Prospecting HQ</div><div class="app-brand__sub">Land development · local SDR workspace</div></div>
          </div>
          <div class="app-brand__badge">Evidence-backed · human-reviewed</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    page = st.pills(
        "Workspace navigation",
        ["Home", "Research", "Pipeline", "Accounts", "Data"],
        key="workspace_nav",
        label_visibility="collapsed",
    )
    if page == "Home":
        render_home(session_factory)
    elif page == "Research":
        research_intake, research_runs = st.tabs(["New search", "Run history"])
        with research_intake:
            render_intake(session_factory, settings)
        with research_runs:
            render_active_runs(session_factory, settings)
    elif page == "Pipeline":
        pipeline_overview, review_queue, outreach = st.tabs(["All prospects", "Review queue", "Outreach"])
        with pipeline_overview:
            render_pipeline(session_factory)
        with review_queue:
            render_review_queue(session_factory)
        with outreach:
            render_outreach_workspace(session_factory)
    elif page == "Accounts":
        render_account_view(session_factory)
    elif page == "Data":
        render_export(session_factory)


if __name__ == "__main__":
    main()
