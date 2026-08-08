import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("CHROMA_PATH", "/tmp/lumalearn-test-chroma")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("SEED_DEMO_DATA", "false")

from app.database import Base  # noqa: E402


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)
