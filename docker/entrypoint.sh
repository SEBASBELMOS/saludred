#!/usr/bin/env bash
# Arranque de la API: esperar la base, aplicar migraciones, sembrar si se pide,
# y solo entonces ceder el proceso al servidor.
set -euo pipefail

# La espera usa el propio motor de SQLAlchemy en vez de un cliente de linea de
# comandos. Evita una dependencia extra en la imagen y, sobre todo, comprueba
# exactamente la misma cadena de conexion que usara la aplicacion.
python - <<'PYCHECK'
import sys
import time

from sqlalchemy import create_engine, text

from app.core.config import get_settings

url = get_settings().database_url
engine = create_engine(url, pool_pre_ping=True)

for attempt in range(60):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        print("Base de datos disponible.")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        if attempt == 0:
            print(f"Esperando la base de datos... ({exc.__class__.__name__})")
        time.sleep(2)

print("La base de datos no respondio tras 120 segundos.", file=sys.stderr)
sys.exit(1)
PYCHECK

echo "Aplicando migraciones..."
alembic upgrade head

# El seed solo corre si se pide explicitamente. Si la base ya tiene datos, el
# script aborta por su cuenta y el arranque continua: reiniciar el contenedor
# nunca debe duplicar ni borrar informacion.
if [ "${SEED_ON_START:-false}" = "true" ]; then
    echo "Cargando datos sinteticos..."
    python -m scripts.seed || echo "Seed omitido: la base ya contiene datos."
fi

exec "$@"
