#!/usr/bin/env bash
# Imprime las URLs publicas de los tuneles de Cloudflare.
#
# Uso:
#   docker compose --profile public up -d
#   bash deploy/urls-publicas.sh
#
# Las URLs cambian cada vez que los contenedores de tunel se reinician.
set -euo pipefail

extraer() {
    local servicio="$1" etiqueta="$2"
    local url
    url=$(docker compose logs "$servicio" 2>/dev/null \
          | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' \
          | tail -1 || true)
    if [ -n "$url" ]; then
        printf '%-12s %s\n' "$etiqueta" "$url"
    else
        printf '%-12s (aun no disponible; reintente en unos segundos)\n' "$etiqueta"
    fi
}

echo "URLs publicas"
echo "-------------"
extraer tunnel-api  "API:"
extraer tunnel-fhir "FHIR:"
echo
echo "Verificar desde fuera de la red (datos moviles):"
echo "  <URL de API>/docs        -> Swagger"
echo "  <URL de FHIR>/metadata   -> CapabilityStatement del servidor FHIR"
