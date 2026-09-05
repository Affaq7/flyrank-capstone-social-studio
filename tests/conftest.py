import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.db import Base, engine, get_db
from app.main import app

Base.metadata.create_all(bind=engine)


@pytest.fixture()
def db_session():
    """One connection per test, wrapped in an outer transaction that's
    rolled back at teardown — a nested SAVEPOINT is restarted after every
    commit the app code issues, so no row from a test run is ever
    permanently committed. Repeated `pytest` runs never leave junk behind."""
    connection = engine.connect()
    outer_transaction = connection.begin()

    TestingSessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = TestingSessionLocal()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
