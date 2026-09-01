# SaludRed — Coordinación de camas hospitalarias en red EPS/IPS

Sistema de gestión y asignación de camas para una EPS que coordina múltiples IPS,
con base de datos relacional PostgreSQL, API REST y exposición de la información
clínica como recursos HL7 FHIR R4.

## El problema

La información sobre ocupación, altas, limpieza, bloqueos y disponibilidad de
camas está fragmentada y se actualiza tarde. La consecuencia no es
necesariamente falta de camas: puede existir capacidad física suficiente, pero
si el estado de una cama no se conoce a tiempo, esa cama no se puede usar.

Eso produce esperas para pacientes que requieren hospitalización, llamadas y
verificaciones repetidas entre áreas, información duplicada y ausencia de
trazabilidad sobre quién cambió un dato y cuándo.

El sistema ataca cuatro puntos concretos:

| Principio | Qué significa en el sistema |
|---|---|
| Visibilidad | todos los actores autorizados consultan el mismo estado |
| Oportunidad | el cambio se registra cuando ocurre, no cuando alguien pregunta |
| Coordinación | solicitudes, asignaciones y estados se comparten entre IPS |
| Trazabilidad | cada cambio deja registro de qué cambió, cuándo y quién lo hizo |

## Alcance

Una EPS coordina una red de IPS. Cada IPS opera sedes, servicios, habitaciones y
camas. El modelo es jerárquico y autorreferenciado: incorporar una IPS nueva o
una cama nueva es una inserción, nunca una migración de esquema.

```
EPS
 ├── IPS Norte ── sede ── servicio ── habitación ── cama
 ├── IPS Sur   ── sede ── servicio ── habitación ── cama
 └── IPS Centro ─ sede ── servicio ── habitación ── cama
```

## Arquitectura

```
   PostgreSQL (aplicación)      ← fuente de verdad operativa
            │
        FastAPI                 ← autenticación, RBAC, soft operations
            │
   Servicio de integración      ← mapeo relacional → FHIR R4
            │
      HAPI FHIR R4              ← representación interoperable
            │
   PostgreSQL (HAPI)
```

El servidor FHIR mantiene su propia base de datos, separada de la base de la
aplicación. Son dos sistemas con ciclos de vida distintos: la aplicación es
dueña del modelo operativo, HAPI es dueño de la representación interoperable.
Compartir una sola base acoplaría nuestras migraciones al esquema interno de
HAPI.

## Stack

| Componente | Tecnología |
|---|---|
| Base de datos | PostgreSQL 16 (Neon en despliegue) |
| API | Python 3.12 · FastAPI · SQLAlchemy 2 · Pydantic v2 |
| Migraciones | Alembic |
| Autenticación | JWT (HS256) · hashing bcrypt |
| Interoperabilidad | HAPI FHIR R4 |
| Documentación de API | OpenAPI/Swagger generado por FastAPI |

## Modelo de datos

Trece tablas relacionadas por llaves foráneas, agrupadas en cuatro bloques:

**Red y ubicaciones** — `organizations`, `locations`
**Identidad** — `roles`, `users`
**Clínico** — `patients`, `encounters`, `observations`
**Coordinación de camas** — `bed_requests`, `bed_assignments`, `bed_status_events`
**Trazabilidad** — `record_versions`, `audit_log`, `fhir_sync_log`

El detalle completo, incluido el mapeo hacia FHIR, está en
[`docs/modelo-datos-y-fhir.md`](docs/modelo-datos-y-fhir.md).

Dos decisiones que conviene conocer antes de leer el esquema:

- **`deleted_at IS NULL` es la única fuente de verdad del borrado lógico.** No
  existe un `is_active` paralelo describiendo el mismo hecho, porque dos columnas
  que describen un mismo estado terminan contradiciéndose.
- **Los códigos que cruzan hacia FHIR se almacenan literales** (`in-progress`,
  `final`, `IMP`). El mapper no traduce vocabularios, y por lo tanto no puede
  desalinearse del estándar.

## Roles

| Rol | Puede |
|---|---|
| `ADMIN` | todo; **único** rol que puede restaurar un registro eliminado |
| `EPS_COORDINATOR` | lectura transversal de la red y gestión de solicitudes entre IPS |
| `IPS_CLINICAL_OPERATOR` | crear y editar registros de su IPS; eliminar solo los que él creó |
| `PATIENT` | consultar únicamente su propia información |

La justificación de cada rol está en
[`docs/modelo-datos-y-fhir.md`](docs/modelo-datos-y-fhir.md).

## Puesta en marcha

Requisitos: Python 3.12, Docker y Docker Compose.

```bash
# 1. Dependencias
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configuración
cp .env.example .env
# Editar .env con la URL de la base de datos y un JWT_SECRET_KEY propio.
# Generar el secreto con:
python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Infraestructura local (PostgreSQL de desarrollo + HAPI FHIR)
docker compose up -d

# 4. Esquema y datos
alembic upgrade head
python -m scripts.seed

# 5. API
uvicorn app.main:app --reload
```

| Servicio | URL local |
|---|---|
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| HAPI FHIR | http://localhost:8080/fhir |

Las cuentas de demostración se crean con la contraseña definida en
`SEED_DEFAULT_PASSWORD`. Son cuentas sintéticas de desarrollo y no deben
reutilizarse en un despliegue real.

## Datos

Todos los datos son **sintéticos**. El sistema no procesa información clínica
real de ninguna persona. El generador usa una semilla fija, de modo que dos
ejecuciones producen exactamente el mismo conjunto de datos y una demostración
preparada no cambia entre ensayos.

## Seguridad

- Las credenciales se leen de variables de entorno; `.env` está excluido del
  control de versiones y `.env.example` documenta la forma de la configuración
  sin contener ningún valor real.
- Las contraseñas se almacenan con bcrypt, nunca en texto plano.
- La autorización se resuelve en el backend por rol y por pertenencia del
  registro. Un endpoint no se protege ocultándolo.

## Estado

Prototipo académico funcional. No implementa OAuth2/SMART on FHIR, analítica
predictiva ni optimización de asignación; esas quedan como extensiones
posteriores.

## Licencia

Uso académico.
