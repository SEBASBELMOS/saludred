"""End-to-end smoke test for the SaludRed API (Phases 2, 3 and 4).

Runs against a live API with the seed loaded and walks the whole demo:
authentication, the RBAC matrix (role, institution, ownership), CRUD, soft
edit with history, soft delete, restore, audit log and the patient portal.
Exits non-zero if any check fails.

Environment:
    SMOKE_BASE_URL       API base (default http://localhost:8000)
    SMOKE_PASSWORD       seed password (default Demo2026!)
    SMOKE_FHIR=1         also exercise the FHIR integration endpoints
                         (requires the HAPI FHIR server to be running)
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx

BASE = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000")
PASSWORD = os.environ.get("SMOKE_PASSWORD", "Demo2026!")
RUN_FHIR = os.environ.get("SMOKE_FHIR") == "1"

client = httpx.Client(base_url=BASE, timeout=30)
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


def as_json(resp: httpx.Response) -> dict:
    """Parse a body defensively.

    A failed check already got reported by ``expect_status``; a non-JSON error
    page must not crash the run and hide every check that comes after it.
    """

    try:
        return resp.json()
    except ValueError:
        return {}


def login(username: str) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    expect_status(resp, 200, f"login {username}")
    token = resp.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ health
expect_status(client.get("/health"), 200, "GET /health")
expect_status(client.get("/health/db"), 200, "GET /health/db")

# ------------------------------------------------------- authentication
expect_status(
    client.get("/api/v1/patients"), 401, "sin token -> 401"
)
expect_status(
    client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "incorrecta"}
    ),
    401,
    "password incorrecta -> 401",
)
expect_status(
    client.get("/api/v1/patients", headers={"Authorization": "Bearer basura"}),
    401,
    "token invalido -> 401",
)

admin = login("admin")
coordinator = login("coordinador.eps")
operator = login("operador.norte")
patient_account = login("paciente.demo")

me = expect_status(
    client.get("/api/v1/auth/me", headers=operator), 200, "GET /auth/me operador"
).json()
check("operador tiene rol IPS_CLINICAL_OPERATOR", me.get("role") == "IPS_CLINICAL_OPERATOR")
operator_org = me.get("organization_id")
check("operador tiene organization_id", operator_org is not None)

# ------------------------------------------------ RBAC: rol e institucion
eps_id = expect_status(
    client.get("/api/v1/organizations?organization_type=EPS", headers=admin),
    200,
    "obtener EPS",
).json()["items"][0]["id"]

# The body must be VALID: FastAPI validates the payload before the endpoint
# runs, so an empty body would prove a 422, not the role rejection.
expect_status(
    client.post(
        "/api/v1/patients",
        headers=coordinator,
        json={
            "document_type": "CC",
            "document_number": "55555555",
            "first_name": "No",
            "last_name": "Autorizado",
            "birth_date": "1970-01-01",
            "gender": "male",
            "eps_organization_id": eps_id,
        },
    ),
    403,
    "coordinador crea paciente -> 403 (rol)",
)
expect_status(
    client.get("/api/v1/patients?include_deleted=true", headers=operator),
    403,
    "operador pide include_deleted -> 403",
)
expect_status(
    client.get("/api/v1/audit-logs", headers=coordinator),
    403,
    "coordinador lee audit-logs -> 403",
)

# The institutional filter is applied inside the query: every encounter the
# operator can list belongs to its own IPS, no matter what filters it sends.
resp = expect_status(
    client.get("/api/v1/encounters?page_size=100", headers=operator),
    200,
    "operador lista encuentros",
)
rows = resp.json()["items"]
check(
    "operador solo ve encuentros de su IPS",
    len(rows) > 0 and all(r["organization_id"] == operator_org for r in rows),
)

orgs = expect_status(
    client.get(
        "/api/v1/organizations?organization_type=IPS&page_size=50", headers=operator
    ),
    200,
    "operador lista organizaciones",
).json()["items"]
other_org = next((o["id"] for o in orgs if o["id"] != operator_org), None)
check("existe otra IPS en la red", other_org is not None)

patients_page = expect_status(
    client.get("/api/v1/patients?page_size=1", headers=operator),
    200,
    "operador lista pacientes",
).json()
any_patient_id = patients_page["items"][0]["id"]

expect_status(
    client.post(
        "/api/v1/encounters",
        headers=operator,
        json={
            "patient_id": any_patient_id,
            "organization_id": other_org,
            "encounter_class": "IMP",
            "status": "in-progress",
            "priority": "ROUTINE",
            "started_at": "2026-09-03T08:00:00Z",
        },
    ),
    403,
    "operador crea encuentro en OTRA IPS -> 403 (institucion)",
)

# ------------------------------------------------ RBAC: pertenencia
# The seeded patients were created by the admin account, so the operator may
# not delete them: permission to delete never crosses authorship.
expect_status(
    client.delete(f"/api/v1/patients/{any_patient_id}", headers=operator),
    403,
    "operador elimina paciente ajeno -> 403 (pertenencia)",
)

own_encounter = expect_status(
    client.post(
        "/api/v1/encounters",
        headers=operator,
        json={
            "patient_id": any_patient_id,
            "organization_id": operator_org,
            "encounter_class": "AMB",
            "status": "in-progress",
            "priority": "ROUTINE",
            "reason_text": "Control por smoke test",
            "started_at": "2026-09-03T08:00:00Z",
        },
    ),
    201,
    "operador crea encuentro en SU IPS",
).json()

own_observation = expect_status(
    client.post(
        "/api/v1/observations",
        headers=operator,
        json={
            "patient_id": any_patient_id,
            "encounter_id": own_encounter["id"],
            "status": "final",
            "code": "8867-4",
            "display": "Heart rate",
            "value_numeric": 88,
            "unit": "/min",
            "observed_at": "2026-09-03T08:15:00Z",
        },
    ),
    201,
    "operador crea observacion propia",
).json()

expect_status(
    client.put(
        f"/api/v1/observations/{own_observation['id']}",
        headers=operator,
        json={"value_numeric": 91},
    ),
    200,
    "operador edita SU observacion",
)
expect_status(
    client.delete(f"/api/v1/observations/{own_observation['id']}", headers=operator),
    204,
    "operador elimina SU observacion",
)
expect_status(
    client.post(
        f"/api/v1/observations/{own_observation['id']}/restore", headers=operator
    ),
    403,
    "operador restaura -> 403 (restore es solo Admin)",
)
expect_status(
    client.post(
        f"/api/v1/observations/{own_observation['id']}/restore", headers=admin
    ),
    200,
    "admin restaura la observacion",
)

# ------------------------------------------- soft ops completas (paciente)
suffix = uuid.uuid4().hex[:8]
created = expect_status(
    client.post(
        "/api/v1/patients",
        headers=admin,
        json={
            "document_type": "CC",
            "document_number": f"99{suffix[:6]}",
            "first_name": "Prueba",
            "last_name": f"Soft-{suffix}",
            "birth_date": "1980-01-15",
            "gender": "male",
            "phone": "3000000000",
            "eps_organization_id": eps_id,
        },
    ),
    201,
    "admin crea paciente de prueba",
).json()
pid = created["id"]

expect_status(
    client.put(
        f"/api/v1/patients/{pid}", headers=admin, json={"phone": "3119999999"}
    ),
    200,
    "soft edit del paciente",
)
history = expect_status(
    client.get(f"/api/v1/patients/{pid}/history", headers=admin),
    200,
    "GET historial del paciente",
).json()
check(
    "el historial conserva el valor anterior",
    len(history) == 1
    and history[0]["snapshot_json"].get("phone") == "3000000000"
    and history[0]["changed_fields"] == ["phone"],
)
expect_status(
    client.get(f"/api/v1/patients/{pid}/history", headers=coordinator),
    200,
    "coordinador tambien lee historial",
)

expect_status(
    client.delete(f"/api/v1/patients/{pid}", headers=admin), 204, "soft delete"
)
expect_status(
    client.get(f"/api/v1/patients/{pid}", headers=admin),
    404,
    "el paciente eliminado ya no aparece",
)
listado = expect_status(
    client.get(
        f"/api/v1/patients?include_deleted=true&document_number=99{suffix[:6]}",
        headers=admin,
    ),
    200,
    "listado con include_deleted",
).json()
check(
    "la fila sigue en la base, marcada",
    listado["total"] == 1 and listado["items"][0]["id"] == pid,
)

expect_status(
    client.post(f"/api/v1/patients/{pid}/restore", headers=coordinator),
    403,
    "coordinador intenta restore -> 403",
)
expect_status(
    client.post(f"/api/v1/patients/{pid}/restore", headers=admin),
    200,
    "admin restaura al paciente",
)
expect_status(
    client.get(f"/api/v1/patients/{pid}", headers=admin),
    200,
    "el paciente restaurado vuelve a estar activo",
)
expect_status(
    client.post(f"/api/v1/patients/{pid}/restore", headers=admin),
    409,
    "restaurar un registro vivo -> 409",
)

audit = expect_status(
    client.get(
        f"/api/v1/audit-logs?entity_type=patients&entity_id={pid}&page_size=50",
        headers=admin,
    ),
    200,
    "GET audit-logs del paciente",
).json()
actions = sorted(e["action"] for e in audit["items"])
check(
    "auditoria registra CREATE, SOFT_EDIT, SOFT_DELETE y RESTORE",
    actions == ["CREATE", "RESTORE", "SOFT_DELETE", "SOFT_EDIT"],
    f"acciones: {actions}",
)
check(
    "cada entrada de auditoria tiene usuario",
    all(e["username"] == "admin" for e in audit["items"]),
)

# ------------------------------------------------------- portal del paciente
mine = expect_status(
    client.get("/api/v1/me/patient", headers=patient_account),
    200,
    "paciente consulta SU ficha",
).json()
expect_status(
    client.get("/api/v1/me/encounters", headers=patient_account),
    200,
    "paciente consulta SUS encuentros",
)
expect_status(
    client.get("/api/v1/patients", headers=patient_account),
    403,
    "paciente en endpoint administrativo -> 403",
)
expect_status(
    client.get(f"/api/v1/patients/{any_patient_id}", headers=patient_account),
    403,
    "paciente lee ficha ajena -> 403",
)
check("la ficha propia corresponde a la cuenta", mine.get("id") is not None)

# ------------------------------------------------------- integracion FHIR
if RUN_FHIR:
    first = as_json(expect_status(
        client.post(f"/api/v1/integration/fhir/patients/{pid}", headers=admin),
        200,
        "sync paciente -> FHIR",
    ))
    check("sync quedo SYNCED", first.get("sync_status") == "SYNCED")

    second = as_json(expect_status(
        client.post(f"/api/v1/integration/fhir/patients/{pid}", headers=admin),
        200,
        "segundo sync del mismo paciente",
    ))
    check(
        "idempotencia: mismo recurso FHIR en ambas corridas",
        first.get("fhir_resource_id") is not None
        and first.get("fhir_resource_id") == second.get("fhir_resource_id")
        and second.get("attempt_count") == first.get("attempt_count", 0) + 1,
    )

    remote = as_json(expect_status(
        client.get(f"/api/v1/integration/fhir/patients/{pid}", headers=admin),
        200,
        "leer Patient desde el servidor FHIR",
    ))
    check("el servidor FHIR devuelve un Patient", remote.get("resourceType") == "Patient")

    obs_sync = as_json(expect_status(
        client.post(
            f"/api/v1/integration/fhir/observations/{own_observation['id']}",
            headers=admin,
        ),
        200,
        "sync observacion (arrastra encuentro y paciente)",
    ))
    check("observacion SYNCED", obs_sync.get("sync_status") == "SYNCED")

    remote_obs = as_json(expect_status(
        client.get(
            f"/api/v1/integration/fhir/observations/{own_observation['id']}",
            headers=admin,
        ),
        200,
        "leer Observation desde FHIR",
    ))
    check(
        "Observation con codigo LOINC en FHIR",
        remote_obs.get("code", {}).get("coding", [{}])[0].get("code") == "8867-4",
    )

    expect_status(
        client.post(f"/api/v1/integration/fhir/patients/{pid}", headers=operator),
        403,
        "operador sincroniza -> 403",
    )
else:
    print("[SKIP] seccion FHIR (exportar SMOKE_FHIR=1 con HAPI arriba)")

# ------------------------------------------------------------------ result
print()
print(f"Fallos: {len(failures)}")
if failures:
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("Smoke test completo: todo en verde.")
