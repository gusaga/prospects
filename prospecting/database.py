"""Database engine/session setup."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .models import Base, Company


def create_db_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    settings.ensure_local_directories()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _migrate_company_account_type(engine)
    _migrate_prospect_outreach_fields(engine)
    _backfill_company_account_types(engine)
    _backfill_prospect_outreach_stages(engine)


def _migrate_company_account_type(engine: Engine) -> None:
    """Add the account-type column to existing local SQLite databases safely."""
    inspector = inspect(engine)
    if "companies" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("companies")}
    with engine.begin() as connection:
        if "account_type" not in columns:
            connection.execute(
                text("ALTER TABLE companies ADD COLUMN account_type VARCHAR(32) NOT NULL DEFAULT 'unknown'")
            )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_companies_account_type ON companies (account_type)"))


def _migrate_prospect_outreach_fields(engine: Engine) -> None:
    """Add the local SDR follow-up fields without requiring a destructive reset."""
    inspector = inspect(engine)
    if "prospects" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("prospects")}
    definitions = {
        "outreach_stage": "VARCHAR(32) NOT NULL DEFAULT 'awaiting_review'",
        "linkedin_url": "VARCHAR(2048)",
        "next_action_at": "DATETIME",
        "last_activity_at": "DATETIME",
        "outreach_notes": "TEXT",
    }
    with engine.begin() as connection:
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE prospects ADD COLUMN {name} {definition}"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_prospects_outreach_stage ON prospects (outreach_stage)"))


def _backfill_company_account_types(engine: Engine) -> None:
    """Classify legacy companies so service firms remain available as partners, not direct leads."""
    from .account_types import infer_account_type
    from .schemas import AccountType

    with Session(engine) as session:
        companies = list(session.scalars(select(Company)))
        for company in companies:
            inferred = infer_account_type(
                name=company.name,
                industry=company.industry,
                evidence=company.evidence_records,
            )
            if inferred != AccountType.UNKNOWN and company.account_type != inferred.value:
                company.account_type = inferred.value
        session.commit()


def _backfill_prospect_outreach_stages(engine: Engine) -> None:
    """Give legacy approvals an actionable first SDR step while leaving tracked work intact."""
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE prospects
                SET outreach_stage = CASE
                    WHEN review_status = 'approved' THEN 'find_on_linkedin'
                    ELSE 'awaiting_review'
                END
                WHERE outreach_stage IS NULL OR outreach_stage = 'awaiting_review'
                """
            )
        )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
