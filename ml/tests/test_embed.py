"""Contract of /embed. The real model is replaced by a fake: no weights, no download."""

from __future__ import annotations

import pytest


class FakeEmbedder:
    model_name = "fake-embedder"

    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.seen.append(texts)
        return [[1.0] + [0.0] * 383 for _ in texts]


@pytest.fixture
def fake(client):
    original = client.app.state.embedder
    client.app.state.embedder = FakeEmbedder()
    yield client.app.state.embedder
    client.app.state.embedder = original


def test_embed_returns_one_vector_per_text(client, fake) -> None:
    response = client.post("/embed", json={"texts": ["hipoglucemia", "triage"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "fake-embedder" and body["dim"] == 384
    assert len(body["vectors"]) == 2 and len(body["vectors"][0]) == 384
    assert fake.seen == [["hipoglucemia", "triage"]]


@pytest.mark.parametrize(
    "payload",
    [{}, {"texts": []}, {"texts": [""]}, {"texts": ["x"] * 65}, {"texts": ["a"], "extra": 1}],
)
def test_embed_refuses_malformed_requests(client, fake, payload) -> None:
    assert client.post("/embed", json=payload).status_code == 422
    assert fake.seen == []


def test_the_model_is_not_loaded_until_the_first_request() -> None:
    from embeddings import Embedder

    assert Embedder()._model is None
