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
# RAG tests assert the classic RRF math (weights 1/1) and k=3; production runs the
# tuned values from rag/eval/SWEEP.md (dense weight 3, k=5).
os.environ["RAG_RRF_DENSE_WEIGHT"] = "1.0"
os.environ["RAG_RRF_LEXICAL_WEIGHT"] = "1.0"
os.environ["RAG_DEFAULT_K"] = "3"
# The escalation timer is started by the app lifespan; tests call its functions
# directly with a frozen clock instead.
os.environ["ALERT_ESCALATION_ENABLED"] = "false"
# The cleaning-evidence gate is off by default; its tests switch it on.
os.environ["REQUIRE_CLEANING_EVIDENCE"] = "false"
# The automatic AI analysis and the mandatory review note are opt-in per test.
os.environ["AI_AUTO_ANALYSIS"] = "false"
os.environ["REQUIRE_REVIEW_NOTE"] = "false"
# The rate limit counts per client IP and every TestClient is the same address: it is
# off by default and its own tests switch it on. Field encryption is off too (the
# application must work unchanged without keys); its tests set the keys explicitly.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["FIELD_ENCRYPTION_KEYS"] = ""
os.environ["FIELD_ENCRYPTION_ACTIVE_KID"] = ""
os.environ["BLIND_INDEX_KEY"] = ""

import base64  # noqa: E402

import mongomock  # noqa: E402
import pytest  # noqa: E402

from app.billing import store  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services import ml_client  # noqa: E402
from app.services.errors import MlServiceUnavailableError  # noqa: E402
from app.services.rag import llm as rag_llm  # noqa: E402
from app.services.rag import store as rag_store  # noqa: E402


class _DownMlClient:
    """Default for every test: the AI service is unreachable, instantly.

    Tests that need an answer install a fake with ``ml_client.set_client``.
    Nothing in the suite ever opens a socket to the real service.
    """

    def _down(self, *_args: object, **_kwargs: object) -> dict:
        raise MlServiceUnavailableError("test: AI service down")

    predict_criticality = predict_cleaning_delay = models = embed = _down


@pytest.fixture(autouse=True)
def _no_real_ml_service():  # noqa: ANN202
    ml_client.set_client(_DownMlClient())
    yield
    ml_client.set_client(None)


@pytest.fixture(autouse=True)
def _fresh_unauthenticated_audit_window():  # noqa: ANN202
    """The "one INTEROP_REJECTED row per (ip, reason) per 60 s" memory is process state."""

    from app.services.interop import receptor

    receptor.reset_unauthenticated_audit()
    yield
    receptor.reset_unauthenticated_audit()


@pytest.fixture(autouse=True)
def _no_real_llm_or_stale_rag_index():  # noqa: ANN202
    """No test reaches Groq (even if a real key sits in .env) or reuses another test's index."""

    rag_llm.set_client(None)
    rag_llm.set_judge_client()
    rag_store.invalidate_cache()
    yield
    rag_llm.set_client()
    rag_llm.set_judge_client()
    rag_store.invalidate_cache()


# Test-only key material (32 bytes each, base64): not secrets.
TEST_KEY_1 = base64.b64encode(bytes(range(32))).decode()
TEST_KEY_2 = base64.b64encode(bytes(range(32, 64))).decode()
TEST_BLIND_KEY = "unit-test-blind-index-key-not-real"


@pytest.fixture
def field_keys(monkeypatch):  # noqa: ANN001, ANN201
    """Switch field encryption and the blind index ON for one test (key ``k1`` active)."""

    settings = get_settings()
    monkeypatch.setattr(settings, "field_encryption_keys", f"k1:{TEST_KEY_1},k2:{TEST_KEY_2}")
    monkeypatch.setattr(settings, "field_encryption_active_kid", "k1")
    monkeypatch.setattr(settings, "blind_index_key", TEST_BLIND_KEY)
    return settings


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
