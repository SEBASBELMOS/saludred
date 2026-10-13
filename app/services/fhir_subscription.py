"""The HAPI Subscription that makes the system reactive (R21).

One R4 ``Subscription`` with a ``rest-hook`` channel: when an Observation is
stored in HAPI, HAPI itself calls ``FHIR_HOOK_ENDPOINT``. There is no polling.

Criteria: ``Observation?`` (every Observation). A narrower criterion was
considered and discarded:

* ``Observation?code=http://loinc.org|2339-0`` would cover the glucose of the
  course tester but miss any other lab result or device reading, which is the
  point of the requirement ("un dato nuevo ... de un laboratorio, un dispositivo
  o de otro sistema").
* ``Observation?_tag:not=<our origin tag>`` would filter our own echo in HAPI,
  but how well the subscription matcher handles ``:not`` varies between HAPI
  versions and cannot be verified offline. If it were wrong, deliveries would
  silently stop, which is far worse than the cheap application-side filter.

So the criterion is broad and the echo is dropped in the receiver.

The Subscription is found by a fixed ``meta.tag`` (R4 ``Subscription`` has no
``identifier`` element), so running the bootstrap again updates it instead of
duplicating it. The header carries the shared secret in plain text inside
HAPI's database: anyone with access to HAPI can read it, which is why the
secret protects a single receiver endpoint and nothing else.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.services.fhir_sync import FhirClient

TAG_CODE = "observation-resthook"
CRITERIA = "Observation?"
REASON = "SaludRed: avisar al medico responsable cuando llega un resultado nuevo"


def tag_system() -> str:
    return f"{get_settings().fhir_identifier_system}:subscriptions"


def build_subscription() -> dict[str, Any]:
    settings = get_settings()
    return {
        "resourceType": "Subscription",
        "meta": {"tag": [{"system": tag_system(), "code": TAG_CODE}]},
        "status": "requested",
        "reason": REASON,
        "criteria": CRITERIA,
        "channel": {
            "type": "rest-hook",
            "endpoint": settings.fhir_hook_endpoint,
            "payload": "application/fhir+json",
            "header": [f"Authorization: Bearer {settings.fhir_hook_secret}"],
        },
    }


def ensure_subscription(client: FhirClient) -> dict[str, Any]:
    """Create the Subscription, or refresh it if it already exists.

    Returns a summary WITHOUT the secret.
    """

    payload = build_subscription()
    existing = client.search("Subscription", {"_tag": f"{tag_system()}|{TAG_CODE}"})
    if existing:
        # More than one would mean someone created it by hand: refresh the
        # first and leave the rest alone (never delete what we did not make).
        stored = client.update("Subscription", existing[0]["id"], payload)
        action = "actualizada"
    else:
        stored = client.create("Subscription", payload)
        action = "creada"
    return {
        "id": stored.get("id"),
        "accion": action,
        "estado": stored.get("status"),
        "criteria": CRITERIA,
        "endpoint": payload["channel"]["endpoint"],
    }
