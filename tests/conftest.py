"""Test setup.

* Uses TEST_DATABASE_URL (a disposable database). The schema is rebuilt from the Alembic migrations on every run,
  so migrations are exercised from an empty database.
* Reference + synthetic demo data are seeded once per session.
* Each test runs inside a transaction that is rolled back afterwards (app commits become savepoint releases).
"""

import os
import tempfile
from collections.abc import Iterator

import pytest

from app.core.config import get_settings

_settings = get_settings()
if not _settings.test_database_url:
    pytest.exit("TEST_DATABASE_URL is not configured; refusing to run tests against the main database.", returncode=2)
os.environ["DATABASE_URL"] = _settings.test_database_url
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="mrv-test-storage-")
os.environ["APP_ENV"] = "test"
get_settings.cache_clear()

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed.demo import seed_demo  # noqa: E402
from app.seed.reference import seed_reference  # noqa: E402

TEST_PASSWORD = "TestPass-12345!"
engine = create_engine(get_settings().database_url)


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("IF OBJECT_ID('alembic_version') IS NOT NULL DROP TABLE alembic_version"))
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")  # every migration must also roll back cleanly
    command.upgrade(alembic_cfg, "head")
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        seed_reference(db)
        seed_demo(db, TEST_PASSWORD)
        db.commit()
    yield


@pytest.fixture()
def db() -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    def request_session() -> Iterator[Session]:
        # Like closing a per-request session in production: a failed request discards its uncommitted work
        try:
            yield db
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = request_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class Api:
    """Small helper: logs in as a demo user and calls the API with that user's token."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self._tokens: dict[str, str] = {}

    def token(self, email: str) -> str:
        if email not in self._tokens:
            r = self.client.post("/api/v1/auth/login", data={"username": email, "password": TEST_PASSWORD})
            assert r.status_code == 200, r.text
            self._tokens[email] = r.json()["access_token"]
        return self._tokens[email]

    def as_(self, who: str) -> "UserClient":
        return UserClient(self.client, self.token(EMAILS[who]))


class UserClient:
    def __init__(self, client: TestClient, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    def get(self, url: str, **kw):
        return self.client.get(f"/api/v1{url}", headers=self.headers, **kw)

    def post(self, url: str, **kw):
        return self.client.post(f"/api/v1{url}", headers=self.headers, **kw)

    def patch(self, url: str, **kw):
        return self.client.patch(f"/api/v1{url}", headers=self.headers, **kw)

    def put(self, url: str, **kw):
        return self.client.put(f"/api/v1{url}", headers=self.headers, **kw)

    def delete(self, url: str, **kw):
        return self.client.delete(f"/api/v1{url}", headers=self.headers, **kw)


EMAILS = {
    "admin": "admin@demo.local",
    "programme_admin": "programme.admin@demo.local",
    "coordinator": "coordinator.ap@demo.local",
    "fpo_admin": "fpo.admin@demo.local",
    "field": "field.officer@demo.local",
    "analyst": "mrv.analyst@demo.local",
    "reviewer": "internal.reviewer@demo.local",
    "verifier": "verifier@demo.local",
    "farmer": "farmer@demo.local",
    "researcher": "researcher@demo.local",
}


@pytest.fixture()
def api(client: TestClient) -> Api:
    return Api(client)


@pytest.fixture()
def programme_id(db: Session):
    from sqlalchemy import select

    from app.models import Programme
    return db.scalar(select(Programme.id).where(Programme.code == "CCRL-INCLUSIVE"))
