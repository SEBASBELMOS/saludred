"""Test environment defaults.

Set before any application import so ``Settings`` can be constructed without a
real ``.env``. The database URL points nowhere on purpose: unit tests must not
touch a database, and a test that tries will fail loudly instead of silently
hitting a development instance.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://nobody:nothing@localhost:1/void"
)
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-not-a-real-secret")
os.environ.setdefault("PSEUDONYM_KEY", "unit-test-pseudonym-key-not-real")
os.environ.setdefault("FHIR_HOOK_SECRET", "unit-test-hook-secret-not-real")
# Forced, not defaulted: inside the live api container these are enabled, and
# tests must behave the same there.
# Tests never talk to HAPI: the tests of auto-sync enable it with a fake client.
os.environ["FHIR_AUTO_SYNC"] = "false"
# The escalation timer is started by the app lifespan; tests call its functions
# directly with a frozen clock instead.
os.environ["ALERT_ESCALATION_ENABLED"] = "false"
# The cleaning-evidence gate is off by default; its tests switch it on.
os.environ["REQUIRE_CLEANING_EVIDENCE"] = "false"
# The automatic AI analysis and the mandatory review note are opt-in per test.
os.environ["AI_AUTO_ANALYSIS"] = "false"
os.environ["REQUIRE_REVIEW_NOTE"] = "false"

import mongomock  # noqa: E402
import pytest  # noqa: E402

from app.billing import store  # noqa: E402
from app.services import ml_client  # noqa: E402
from app.services.errors import MlServiceUnavailableError  # noqa: E402


class _DownMlClient:
    """Default for every test: the AI service is unreachable, instantly.

    Tests that need an answer install a fake with ``ml_client.set_client``.
    Nothing in the suite ever opens a socket to the real service.
    """

    def _down(self, *_args: object, **_kwargs: object) -> dict:
        raise MlServiceUnavailableError("test: AI service down")

    predict_criticality = predict_cleaning_delay = models = _down


@pytest.fixture(autouse=True)
def _no_real_ml_service():  # noqa: ANN202
    ml_client.set_client(_DownMlClient())
    yield
    ml_client.set_client(None)


@pytest.fixture(autouse=True)
def mongo_facturas():  # noqa: ANN201
    """Default for every test: an in-memory MongoDB, fresh each time.

    No test ever needs a real Mongo. A test of the "Mongo is down" path installs
    a failing collection with ``store.set_collection``.
    """

    collection = mongomock.MongoClient(tz_aware=True)["saludred_test"]["facturas"]
    store.set_collection(collection)
    yield collection
    store.set_collection(None)
