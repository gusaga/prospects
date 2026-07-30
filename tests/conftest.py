from __future__ import annotations

import pytest

from prospecting.config import Settings
from prospecting.database import build_session_factory, create_db_engine, initialize_database


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'prospects.db').as_posix()}",
        max_accounts_per_run=10,
        max_prospects_per_account=3,
        max_concurrent_agents=2,
        model_step_budget=100,
        feedback_minimum_reviews=10,
    )


@pytest.fixture
def session_factory(settings):
    engine = create_db_engine(settings)
    initialize_database(engine)
    return build_session_factory(engine)

