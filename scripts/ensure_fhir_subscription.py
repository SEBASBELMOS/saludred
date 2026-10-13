"""Create or refresh the HAPI Subscription of the rest-hook (R21).

Idempotent: it looks the Subscription up by its fixed tag and updates it, so it
can run after every deployment or whenever ``FHIR_HOOK_SECRET`` /
``FHIR_HOOK_ENDPOINT`` change.

    docker compose exec api python -m scripts.ensure_fhir_subscription
"""

from __future__ import annotations

import json
import sys

from app.services import fhir_subscription
from app.services.errors import FhirGatewayError
from app.services.fhir_sync import FhirClient, new_fhir_client


def main() -> int:
    http = new_fhir_client()
    try:
        summary = fhir_subscription.ensure_subscription(FhirClient(http))
    except FhirGatewayError as exc:
        print(f"No se pudo configurar la Subscription: {exc}", file=sys.stderr)
        return 1
    finally:
        http.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
