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

Único requisito: **Docker y Docker Compose**. No hace falta instalar Python ni
dependencias en la máquina: todo vive dentro de los contenedores.

```bash
# 1. Configuración
cp .env.example .env
# Editar .env y poner un JWT_SECRET_KEY propio. Para generarlo:
#   docker run --rm python:3.12-slim python -c "import secrets; print(secrets.token_urlsafe(48))"

# 2. Levantar todo
docker compose up -d --build
```

Eso construye la imagen de la API y arranca cuatro servicios. Al iniciar, la API
espera a que PostgreSQL acepte conexiones, aplica las migraciones y carga los
datos sintéticos. Si la base ya tiene datos, el seed se omite solo.

| Servicio | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| HAPI FHIR | http://localhost:8080/fhir |
| PostgreSQL | `localhost:5433` |

El servidor FHIR tarda entre 30 y 60 segundos en el primer arranque, mientras
construye su esquema. Está listo cuando responde:

```bash
curl http://localhost:8080/fhir/metadata
```

Comandos útiles:

```bash
docker compose logs -f api        # seguir el arranque
docker compose down               # detener
docker compose down -v            # detener y borrar los datos
```

## Autenticación y uso

Todos los endpoints de `/api/v1` (salvo el login) exigen un token Bearer:

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<SEED_DEFAULT_PASSWORD>"}'
```

En Swagger, el botón **Authorize** acepta el `access_token` devuelto. Cuentas
creadas por el seed: `admin`, `coordinador.eps`, `operador.norte`,
`operador.sur`, `operador.centro` y `paciente.demo`, todas con la contraseña
definida en `SEED_DEFAULT_PASSWORD`. Son cuentas sintéticas de desarrollo y no
deben reutilizarse en un despliegue real.

Operaciones de trazabilidad expuestas por entidad: `PUT` conserva la versión
anterior (consultable en `GET /{id}/history`), `DELETE` es lógico, y
`POST /{id}/restore` (solo Admin) revierte la eliminación. La bitácora completa
se consulta en `GET /api/v1/audit-logs` (solo Admin).

La sincronización hacia FHIR se opera en `/api/v1/integration/fhir/*`: `POST`
empuja el registro (y sus dependencias) mediante actualización condicional por
identificador — reejecutarla no duplica recursos — y `GET` lee el recurso
directamente desde el servidor FHIR.

### Verificación

Los tres comandos se ejecutan dentro del contenedor de la API, donde ya están
todas las dependencias instaladas:

```bash
docker compose exec api pytest                       # pruebas unitarias (sin base de datos)
docker compose exec api python -m scripts.demo_join  # consulta JOIN de validación del modelo
docker compose exec api python -m scripts.smoke_api  # prueba end-to-end de la API

# Incluye además la integración FHIR (requiere HAPI ya disponible):
docker compose exec -e SMOKE_FHIR=1 api python -m scripts.smoke_api
```

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
