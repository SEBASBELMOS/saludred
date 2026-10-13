"""EPS / IPS creation endpoints, through the real app with the seeded accounts."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from tests.test_bed_rules import client_as, db  # noqa: F401  (fixture reuse)


def _create_eps(client, code: str = "EPS-T1") -> dict:
    response = client.post("/api/v1/eps", json={"code": code, "name": "EPS de prueba"})
    assert response.status_code == 201, response.text
    return response.json()


def _create_ips(client, eps_id: str, code: str = "IPS-T1") -> dict:
    response = client.post(
        f"/api/v1/eps/{eps_id}/ips", json={"code": code, "name": "IPS de prueba"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_creates_an_eps_without_parent(db: Session, client_as) -> None:  # noqa: F811
    eps = _create_eps(client_as("admin"))
    assert eps["organization_type"] == "EPS"
    assert eps["parent_organization_id"] is None


def test_admin_creates_an_ips_under_the_eps_and_lists_it(db: Session, client_as) -> None:  # noqa: F811
    client = client_as("admin")
    eps = _create_eps(client)
    ips = _create_ips(client, eps["id"])
    assert ips["organization_type"] == "IPS"
    assert ips["parent_organization_id"] == eps["id"]

    listing = client.get(f"/api/v1/eps/{eps['id']}/ips")
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()] == [ips["id"]]


def test_duplicate_code_is_a_conflict(db: Session, client_as) -> None:  # noqa: F811
    client = client_as("admin")
    _create_eps(client, "EPS-DUP")
    response = client.post("/api/v1/eps", json={"code": "EPS-DUP", "name": "Otra"})
    assert response.status_code == 409


def test_an_ips_cannot_be_used_as_parent(db: Session, client_as) -> None:  # noqa: F811
    client = client_as("admin")
    eps = _create_eps(client)
    ips = _create_ips(client, eps["id"])
    response = client.post(
        f"/api/v1/eps/{ips['id']}/ips", json={"code": "IPS-T2", "name": "Hija de IPS"}
    )
    assert response.status_code == 409


def test_unknown_eps_is_not_found(db: Session, client_as) -> None:  # noqa: F811
    response = client_as("admin").post(
        f"/api/v1/eps/{uuid.uuid4()}/ips", json={"code": "IPS-X", "name": "Sin EPS"}
    )
    assert response.status_code == 404


def test_only_admin_creates_an_eps(db: Session, client_as) -> None:  # noqa: F811
    response = client_as("coordinador.eps").post(
        "/api/v1/eps", json={"code": "EPS-NO", "name": "No permitido"}
    )
    assert response.status_code == 403


def test_generic_endpoint_rejects_an_eps_with_parent(db: Session, client_as) -> None:  # noqa: F811
    """Reproduces the demo failure: the generic form sent a parent for an EPS."""

    client = client_as("admin")
    eps = _create_eps(client)
    response = client.post(
        "/api/v1/organizations",
        json={
            "code": "EPS-MAL",
            "name": "EPS con padre",
            "organization_type": "EPS",
            "parent_organization_id": eps["id"],
        },
    )
    assert response.status_code == 422


def test_code_longer_than_32_characters_is_rejected(db: Session, client_as) -> None:  # noqa: F811
    response = client_as("admin").post(
        "/api/v1/eps", json={"code": "E" * 33, "name": "Codigo largo"}
    )
    assert response.status_code == 422
