# Modelo de datos, mapeo a FHIR R4 y justificación de roles

## 1. Modelo relacional

Trece tablas relacionadas por llaves foráneas. El modelo no es una tabla plana:
representa el recorrido completo del flujo operativo, desde que un paciente
necesita hospitalización hasta que una cama queda nuevamente disponible.

```
organizations (EPS)
   └── organizations (IPS)              parent_organization_id
          ├── locations                 sede → servicio → habitación → cama
          │      └── bed_status_events  histórico de estados de cada cama
          ├── users
          └── encounters

patients
   └── encounters
          ├── observations
          └── bed_requests
                 └── bed_assignments ── locations (cama)
```

### 1.1 Tablas

| Tabla | Responsabilidad |
|---|---|
| `organizations` | EPS e IPS en una jerarquía autorreferenciada |
| `locations` | Sedes, servicios, habitaciones y camas |
| `roles` | Los cuatro roles del sistema |
| `users` | Cuentas de acceso, con rol y ámbito |
| `patients` | Afiliados de la EPS |
| `encounters` | Episodios clínicos en una IPS |
| `observations` | Mediciones tomadas durante un encuentro |
| `bed_requests` | Solicitudes de cama |
| `bed_assignments` | Camas efectivamente asignadas |
| `bed_status_events` | Línea de tiempo de cada transición de estado de cama |
| `record_versions` | Versiones previas de un registro (soft edit) |
| `audit_log` | Quién hizo qué, sobre qué registro y cuándo |
| `fhir_sync_log` | Vínculo entre un registro local y su recurso en FHIR |

### 1.2 Decisiones de diseño

**Jerarquía autorreferenciada.** EPS e IPS viven en una sola tabla con
`parent_organization_id`, y lo mismo ocurre con `locations`. Agregar una IPS o
una cama es un `INSERT`, nunca un cambio de esquema. Eso es lo que tiene que
significar "escalar horizontalmente" a nivel de base de datos.

**Un solo indicador de borrado.** `deleted_at IS NULL` significa que la fila está
viva. No existe un `is_active` paralelo describiendo el mismo hecho. El único
`is_active` del esquema está en `users`, y ahí significa otra cosa: si la cuenta
está habilitada para iniciar sesión.

**`created_by` en toda entidad con borrado lógico.** El requisito establece que
el rol clínico solo puede eliminar los registros que él mismo creó. Esa regla es
inaplicable sin registrar la autoría, así que la columna es parte del contrato,
no un dato de bitácora.

**Vocabularios almacenados con el valor de FHIR.** `encounters.status` guarda
`in-progress`, no `IN_PROGRESS`. El servicio de integración no traduce
vocabularios y por lo tanto no puede desalinearse del estándar.

**Restricciones en la base, no solo en el código.** El esquema incluye 25
restricciones `CHECK` y un índice único parcial que impide que una cama tenga más
de una asignación activa. Una cama doblemente asignada es justamente el error que
este proyecto existe para evitar; dejar esa garantía únicamente en el código de
aplicación sería confiar en que ninguna ruta futura la olvide.

---

## 2. Mapeo a HL7 FHIR R4

### 2.1 Recursos obligatorios

| Tabla | Recurso FHIR | Notas |
|---|---|---|
| `patients` | `Patient` | Datos demográficos e identificador de negocio |
| `encounters` | `Encounter` | Episodio clínico, con la cama en `Encounter.location` |
| `observations` | `Observation` | Mediciones codificadas en LOINC |

### 2.2 Recursos adicionales y su justificación

| Tabla | Recurso FHIR | Por qué corresponde a este dominio |
|---|---|---|
| `organizations` | `Organization` | La red EPS→IPS se expresa con `Organization.partOf`, que es el elemento estándar para jerarquía institucional |
| `locations` | `Location` | Una cama es una ubicación física, y FHIR define `Location.operationalStatus` precisamente para su estado operativo |

No se incorporan más recursos. `bed_requests` y `bed_assignments` permanecen como
modelo interno de coordinación: FHIR no tiene un recurso que represente
fielmente una solicitud de cama en una red aseguradora, y forzar uno produciría
un recurso no interoperable con la apariencia de serlo.

### 2.3 Elementos requeridos por el estándar

Los siguientes elementos tienen cardinalidad `1..1` en R4. Un recurso que los
omita es rechazado por el servidor.

| Recurso | Elemento | Valores usados |
|---|---|---|
| `Encounter` | `status` | `planned`, `arrived`, `in-progress`, `finished`, `cancelled` |
| `Encounter` | `class` | `IMP`, `AMB`, `EMER` — sistema `http://terminology.hl7.org/CodeSystem/v3-ActCode` |
| `Observation` | `status` | `final` |
| `Observation` | `code` | LOINC (sección 2.5) |
| `Location` | `status` | `active`, `suspended`, `inactive` |

### 2.4 Estado de cama → `Location.operationalStatus`

Sistema: `http://terminology.hl7.org/CodeSystem/v2-0116`.

| `locations.status` | Código | Display |
|---|---|---|
| `AVAILABLE` | `U` | Unoccupied |
| `OCCUPIED` | `O` | Occupied |
| `RESERVED` | `O` | Occupied |
| `CLEANING` | `H` | Housekeeping |
| `BLOCKED` | `C` | Closed |
| `MAINTENANCE` | `C` | Closed |

`location_type` mapea a `Location.physicalType`
(`http://terminology.hl7.org/CodeSystem/location-physical-type`):
`FACILITY`→`si`, `WARD`→`wa`, `ROOM`→`ro`, `BED`→`bd`.

`Organization.type`
(`http://terminology.hl7.org/CodeSystem/organization-type`): la EPS es `pay`
(pagador) y cada IPS es `prov` (prestador).

### 2.5 Terminología

Solo se usa **LOINC**, y únicamente en `Observation.code`. No se introducen
CIE-10 ni SNOMED CT: los diagnósticos no forman parte de este alcance, y añadir
un vocabulario que no se usa no aporta interoperabilidad.

| LOINC | Descripción | Unidad UCUM |
|---|---|---|
| `8867-4` | Frecuencia cardiaca | `/min` |
| `9279-1` | Frecuencia respiratoria | `/min` |
| `8310-5` | Temperatura corporal | `Cel` |
| `8480-6` | Presión arterial sistólica | `mm[Hg]` |
| `8462-4` | Presión arterial diastólica | `mm[Hg]` |
| `59408-5` | Saturación de oxígeno por pulsioximetría | `%` |

Las unidades siguen UCUM (`http://unitsofmeasure.org`). Ningún código se inventa:
los seis fueron verificados contra loinc.org y corresponden al perfil de signos
vitales de FHIR.

Los identificadores propios usan el sistema `urn:saludred:identifier`. No se
utiliza un OID de una autoridad real, porque no nos pertenece.

### 2.6 Idempotencia de la sincronización

Un `POST` repetido crea recursos duplicados. Para evitarlo, la sincronización usa
el identificador de negocio del registro y una actualización condicional:

```http
PUT [base]/Patient?identifier=urn:saludred:identifier|CC-1032456789
```

FHIR resuelve esa petición como "crear si no existe, actualizar si existe".
Adicionalmente, `fhir_sync_log` tiene una restricción `UNIQUE (entity_type,
entity_id)`: un registro local solo puede corresponder a un recurso FHIR.

El criterio de aceptación es verificable: ejecutar la sincronización dos veces
seguidas no debe cambiar el número de recursos en el servidor.

---

## 3. Justificación de los roles

El sistema define cuatro roles. Tres corresponden a los arquetipos exigidos y el
cuarto es propio del dominio.

| Arquetipo requerido | Rol implementado | Permisos |
|---|---|---|
| Administrador | `ADMIN` | Acceso completo; soft edit y soft delete sobre cualquier registro; **único** rol autorizado para restaurar |
| Rol clínico | `IPS_CLINICAL_OPERATOR` | Crea y edita registros clínicos de su IPS; soft delete **solo sobre los registros que él creó**; no puede restaurar |
| Paciente | `PATIENT` | Lectura exclusiva de su propia información |
| — | `EPS_COORDINATOR` | Lectura transversal de la red; gestiona solicitudes de cama entre IPS; no puede restaurar |

### Por qué estos cuatro

**`EPS_COORDINATOR` es el rol que sostiene el alcance del proyecto.** El sistema
coordina una red de instituciones, no una sola. Sin un actor que vea la capacidad
agregada de todas las IPS, la coordinación entre instituciones no tiene quién la
ejerza y el alcance multiinstitucional sería solo una afirmación del documento.

**`PATIENT` se conserva porque el paciente es un actor natural de este dominio.**
Es el afiliado que espera una cama, y la información sobre el estado de su
solicitud le concierne directamente. Se implementa como lectura estricta de su
propio registro.

**La restauración es exclusiva del administrador, sin excepción.** Un operador
clínico puede eliminar lógicamente un registro propio para corregir un error de
captura, pero no puede deshacer esa eliminación: si pudiera, el borrado lógico
dejaría de ser un punto de control y pasaría a ser una operación reversible por
la misma persona que la ejecutó, lo que anula su valor como evidencia. Esto
aplica incluso cuando el error fue del propio operador.

### Ámbito de autorización

El rol no es lo único que se evalúa. La autorización combina tres condiciones:

1. **Rol** — qué tipo de operación puede ejecutar.
2. **Institución** — `users.organization_id` restringe al operador clínico a su
   propia IPS.
3. **Pertenencia** — `created_by` determina sobre qué registros concretos puede
   actuar dentro de esa IPS.

Un operador de la IPS Norte con permiso de edición no puede modificar un registro
de la IPS Sur, ni un registro de la IPS Norte creado por otro operador.

---

## 4. Trazabilidad

| Operación | Qué queda registrado |
|---|---|
| Soft edit (`PUT`) | Fila previa completa en `record_versions` y entrada `SOFT_EDIT` en `audit_log` |
| Soft delete (`DELETE`) | `deleted_at` y `deleted_by` en la fila, entrada `SOFT_DELETE` en `audit_log` |
| Restauración (`POST .../restore`) | `restored_at` y `restored_by`, entrada `RESTORE` en `audit_log` |

Las marcas de restauración son columnas independientes de las de eliminación: una
restauración nunca borra la evidencia de quién eliminó el registro primero.

Tanto el historial como la bitácora son consultables por endpoint
(`GET /patients/{id}/history` y `GET /audit-logs`). Un registro de auditoría que
solo existe en la base de datos no es verificable por quien usa el sistema.
