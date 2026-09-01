"""End-to-end smoke test for the SaludRed API.

Runs against a seeded database and exercises every Phase 2 endpoint:
health, CRUD, pagination, filters, validation, 404/409 error paths and
the EPS -> IPS network query. Exits non-zero on the first failure.
"""

import sys
import uuid

import httpx

BASE = "http://localhost:8000"
client = httpx.Client(base_url=BASE, timeout=10)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def expect_status(resp: httpx.Response, expected: int, name: str) -> httpx.Response:
    check(
        name,
        resp.status_code == expected,
        f"expected {expected}, got {resp.status_code}: {resp.text[:300]}",
    )
    return resp


# ---------------------------------------------------------------- health
expect_status(client.get("/health"), 200, "GET /health")
expect_status(client.get("/health/db"), 200, "GET /health/db")

# ------------------------------------------------------------- organizations
resp = client.get("/api/v1/organizations")
orgs = expect_status(resp, 200, "GET /organizations").json()
check("organizations total == 4", orgs["total"] == 4, f"got {orgs['total']}")
eps = next(o for o in orgs["items"] if o["organization_type"] == "EPS")
ips = next(o for o in orgs["items"] if o["organization_type"] == "IPS")

resp = client.get("/api/v1/organizations", params={"organization_type": "IPS"})
check("GET /organizations?organization_type=IPS -> 3", resp.json()["total"] == 3)

resp = client.get(f"/api/v1/eps/{eps['id']}/ips")
check("GET /eps/{id}/ips -> 3", len(resp.json()) == 3, resp.text[:200])

new_ips = {
    "code": "IPS-TEST",
    "name": "IPS de Prueba",
    "organization_type": "IPS",
    "parent_organization_id": eps["id"],
}
resp = client.post("/api/v1/organizations", json=new_ips)
created_ips = expect_status(resp, 201, "POST /organizations (IPS bajo EPS)").json()

resp = client.post(
    "/api/v1/organizations",
    json={"code": "EPS-MALA", "name": "EPS con padre", "organization_type": "EPS", "parent_organization_id": eps["id"]},
)
expect_status(resp, 422, "POST EPS con padre -> 422")

resp = client.put(
    f"/api/v1/organizations/{created_ips['id']}",
    json={"code": "IPS-TEST-2", "name": "IPS de Prueba (editada)"},
)
check("PUT /organizations/{id} -> 200", resp.status_code == 200 and resp.json()["code"] == "IPS-TEST-2")

resp = client.delete(f"/api/v1/organizations/{eps['id']}")
expect_status(resp, 409, "DELETE EPS con hijas -> 409")

resp = client.put(f"/api/v1/organizations/{eps['id']}", json={"organization_type": "IPS"})
expect_status(resp, 409, "PUT EPS->IPS con hijas -> 409")

# ---------------------------------------------------------------- patients
resp = client.get("/api/v1/patients", params={"page_size": 5})
page = expect_status(resp, 200, "GET /patients (page_size=5)").json()
check("patients pagination total==16", page["total"] == 16, f"got {page['total']}")
check("patients page size==5", len(page["items"]) == 5)

resp = client.get("/api/v1/patients", params={"page": 2, "page_size": 5})
check("patients page 2 size==5", len(resp.json()["items"]) == 5)

patient = page["items"][0]
resp = client.get("/api/v1/patients", params={"document_number": patient["document_number"]})
check("filter by document_number", resp.json()["total"] == 1)

resp = client.get("/api/v1/patients", params={"name": patient["first_name"][:3]})
check("filter by name (LIKE)", resp.json()["total"] >= 1)

expect_status(client.get(f"/api/v1/patients/{patient['id']}"), 200, "GET /patients/{id}")

new_patient = {
    "document_type": "CC",
    "document_number": "9999999999",
    "first_name": "Paciente",
    "last_name": "Smoke",
    "birth_date": "1990-01-01",
    "gender": "female",
    "eps_organization_id": eps["id"],
}
resp = client.post("/api/v1/patients", json=new_patient)
created_patient = expect_status(resp, 201, "POST /patients").json()

resp = client.post("/api/v1/patients", json=new_patient)
expect_status(resp, 409, "POST paciente duplicado -> 409")

resp = client.put(f"/api/v1/patients/{created_patient['id']}", json={"last_name": "Smoke Editado"})
check("PUT /patients/{id}", resp.status_code == 200 and resp.json()["last_name"] == "Smoke Editado")

resp = client.delete(f"/api/v1/patients/{created_patient['id']}")
expect_status(resp, 204, "DELETE /patients/{id} -> 204")

expect_status(client.get(f"/api/v1/patients/{created_patient['id']}"), 404, "GET paciente eliminado -> 404")

resp = client.post(
    "/api/v1/patients",
    json={**new_patient, "document_number": "9999999998", "birth_date": "no-es-fecha"},
)
expect_status(resp, 422, "POST paciente con fecha invalida -> 422")

# --------------------------------------------------------------- encounters
resp = client.get("/api/v1/encounters", params={"page_size": 100})
encounters = expect_status(resp, 200, "GET /encounters").json()
check("encounters total == 16", encounters["total"] == 16, f"got {encounters['total']}")

resp = client.get(f"/api/v1/patients/{patient['id']}/encounters")
patient_encounters = expect_status(resp, 200, "GET /patients/{id}/encounters").json()
check("patient has encounters", len(patient_encounters) >= 1)

encounter = patient_encounters[0]
expect_status(client.get(f"/api/v1/encounters/{encounter['id']}"), 200, "GET /encounters/{id}")

new_encounter = {
    "patient_id": patient["id"],
    "organization_id": ips["id"],
    "encounter_class": "AMB",
    "status": "planned",
    "started_at": "2026-09-01T10:00:00Z",
}
resp = client.post("/api/v1/encounters", json=new_encounter)
created_encounter = expect_status(resp, 201, "POST /encounters").json()

resp = client.put(f"/api/v1/encounters/{created_encounter['id']}", json={"status": "in-progress"})
check("PUT /encounters/{id}", resp.status_code == 200 and resp.json()["status"] == "in-progress")

resp = client.put(
    f"/api/v1/encounters/{created_encounter['id']}",
    json={"ended_at": "2026-08-01T10:00:00Z"},
)
expect_status(resp, 409, "PUT encounter ended<started -> 409")

resp = client.delete(f"/api/v1/encounters/{created_encounter['id']}")
expect_status(resp, 204, "DELETE /encounters/{id} -> 204")

# -------------------------------------------------------------- observations
resp = client.get("/api/v1/observations", params={"page_size": 100})
obs = expect_status(resp, 200, "GET /observations").json()
check("observations total == 48", obs["total"] == 48, f"got {obs['total']}")

resp = client.get(f"/api/v1/encounters/{encounter['id']}/observations")
encounter_obs = expect_status(resp, 200, "GET /encounters/{id}/observations").json()
check("encounter has observations", len(encounter_obs) >= 1, resp.text[:200])

new_observation = {
    "patient_id": patient["id"],
    "encounter_id": encounter["id"],
    "code": "8867-4",
    "display": "Heart rate",
    "value_numeric": 72.5,
    "unit": "/min",
    "observed_at": "2026-09-01T10:05:00Z",
}
resp = client.post("/api/v1/observations", json=new_observation)
created_obs = expect_status(resp, 201, "POST /observations (numeric)").json()

resp = client.post(
    "/api/v1/observations",
    json={k: v for k, v in new_observation.items() if k not in ("value_numeric", "unit")},
)
expect_status(resp, 422, "POST observation sin valor -> 422")

resp = client.put(f"/api/v1/observations/{created_obs['id']}", json={"value_numeric": 75, "unit": "/min"})
check("PUT /observations/{id}", resp.status_code == 200 and resp.json()["value_numeric"] == 75.0)

resp = client.put(f"/api/v1/observations/{created_obs['id']}", json={"value_text": "estable"})
expect_status(resp, 409, "PUT observation valor mixto -> 409")

resp = client.delete(f"/api/v1/observations/{created_obs['id']}")
expect_status(resp, 204, "DELETE /observations/{id} -> 204")

expect_status(client.get(f"/api/v1/patients/{uuid.uuid4()}"), 404, "GET paciente inexistente -> 404")

print(f"\n{len(failures)} failure(s)")
if failures:
    print("\n".join(f" - {f}" for f in failures))
    sys.exit(1)
print("SMOKE OK")
