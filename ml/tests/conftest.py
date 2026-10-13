"""Train once per session into a temporary folder and serve that."""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

import train  # noqa: E402
from app import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# One repetition keeps the suite fast; the image build uses the full protocol.
TEST_REPEATS = 1


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory: pytest.TempPathFactory):
    out = tmp_path_factory.mktemp("artifacts")
    metrics = train.main(train.DATA_DIR, out, repeats=TEST_REPEATS)
    return out, metrics


@pytest.fixture(scope="session")
def client(artifacts):
    out, _ = artifacts
    with TestClient(create_app(out)) as test_client:
        yield test_client
