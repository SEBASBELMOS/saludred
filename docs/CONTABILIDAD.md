# Contabilidad en MongoDB

Este documento describe cómo SaludRed registra la facturación de servicios (requisito R09), qué estructura tienen los documentos, cómo se generan solos al aprobar un reporte de IA o al liberar una cama (R19, flujo 3), quién puede operarlos y cómo se recuperan si algo falla.

## Por qué MongoDB para la facturación y PostgreSQL para la clínica

| Razón | Qué significa aquí |
|---|---|
| **Documentos flexibles por servicio** | Una factura de análisis de IA lleva `reporte_id`; una de día cama lleva `solicitud_cama_id` y `cama_id`; una de remisión lleva `remision_id`; una de consulta no lleva ninguno. En un modelo relacional serían columnas casi siempre vacías o una tabla por servicio. Un documento lleva solo los campos que le corresponden, y un servicio nuevo no exige migración. |
| **Rastro financiero que solo crece** | Una factura nunca se borra: cambia de estado y cada cambio se agrega a `historial` (`$push`). No hay `DELETE` en la API ni operación de borrado en el repositorio. |
| **Separación de lo clínico y lo financiero** | La historia clínica (PostgreSQL) y el dinero (MongoDB) tienen ciclos de vida, dueños y permisos distintos: el contable no ve nada clínico y el médico no ve nada financiero. Dos bases distintas hacen que esa separación sea física y no solo una regla de la aplicación. |
| **Minimización de datos** | Ver la sección siguiente. |

PostgreSQL sigue siendo la fuente de verdad de pacientes, encuentros, camas y reportes. MongoDB guarda únicamente el hecho económico y los identificadores para volver a ese hecho.

## Minimización de datos (Ley 1581 de 2012, finalidad)

El documento de MongoDB **no contiene nombre, documento, teléfono, correo, dirección ni ningún dato clínico** del paciente. Solo guarda su `paciente_id` (un UUID opaco) y los identificadores de la EPS y la IPS.

Motivo: la Ley 1581 de 2012 exige que los datos personales se traten para una finalidad determinada y con el mínimo necesario (principios de finalidad y de acceso y circulación restringida). La finalidad de esta colección es cobrar y conciliar servicios; para eso basta saber *qué* se cobró, *a quién (id)*, *cuánto* y *en qué estado está*. Copiar el nombre o el documento duplicaría datos personales en una segunda base, ampliaría la superficie de una filtración y dejaría datos desactualizados si el paciente corrige su ficha. Cuando hace falta mostrarlos, el **recibo en PDF resuelve el nombre y el documento desde PostgreSQL en el momento de generarlo**. Tampoco se guarda el resultado del análisis de IA: la descripción de la factura dice "Análisis de IA aprobado por un clínico" y nada más. Una prueba automática revisa que ningún documento guardado tenga nombre ni documento, ni como clave ni como valor.

## La colección `facturas`

Base `saludred_contabilidad` (variable `MONGO_DB`), colección `facturas`.

| Campo | Tipo | Descripción |
|---|---|---|
| `_id` | ObjectId | Se expone en la API como el texto `id`. |
| `paciente_id` | string (UUID) | Paciente en PostgreSQL. |
| `eps_id`, `ips_id` | string (UUID) | EPS del paciente (siempre) e IPS que presta el servicio (cuando se conoce). |
| `servicio` | string | `analisis_ia`, `dia_cama`, `remision`, `consulta` o `medicamento`. |
| `descripcion` | string | Texto corto del concepto. |
| `cantidad` | int | Unidades (días cama, medicamentos, etc.). |
| `valor_unitario`, `valor` | int | Pesos colombianos (COP), enteros. `valor = cantidad × valor_unitario`. |
| `fecha` | datetime UTC | Momento en que se creó la factura. |
| `estado_pago` | string | `pendiente`, `pagada` o `anulada`. |
| `origen` | string | `automatica` (reactor o conciliación) o `manual` (un contable o el admin). |
| `creado_por` | string | Id del usuario, o `sistema` en las automáticas. |
| `reporte_id` | string (UUID), opcional | Reporte de IA aprobado que se factura. |
| `solicitud_cama_id`, `cama_id` | string (UUID), opcionales | Solicitud de cama y cama de una estancia facturada. |
| `remision_id` | string (UUID), opcional | Remisión facturada (Epic 7). |
| `historial` | array | `{estado, fecha, usuario, nota}` por cada cambio de estado, incluida la creación. |

Los campos opcionales **no existen** cuando no aplican (no se guardan como `null`): los índices de idempotencia se apoyan en esa ausencia.

Ejemplo (factura automática de un análisis de IA, ya pagada):

```json
{
  "_id": { "$oid": "6700a1f2c3d4e5f6a7b8c9d0" },
  "paciente_id": "0b6f2a9e-5a41-4c5e-9d3b-0d7c1c0f8a11",
  "eps_id": "4d1e7c42-8f3b-4a65-b2a0-31c9f0a6e7d2",
  "ips_id": "9a3c5d18-26b4-4f0e-8c71-5e2d4b6a1f03",
  "servicio": "analisis_ia",
  "descripcion": "Análisis de IA aprobado por un clínico",
  "cantidad": 1,
  "valor_unitario": 45000,
  "valor": 45000,
  "fecha": { "$date": "2026-10-03T15:31:30Z" },
  "estado_pago": "pagada",
  "origen": "automatica",
  "creado_por": "sistema",
  "reporte_id": "c2f0a1d4-7b3e-4a98-9d65-1e8b7a4c2f90",
  "historial": [
    { "estado": "pendiente", "fecha": { "$date": "2026-10-03T15:31:30Z" }, "usuario": "sistema", "nota": "Factura creada" },
    { "estado": "pagada", "fecha": { "$date": "2026-10-04T09:12:05Z" }, "usuario": "7e4a…", "nota": "Transferencia 123" }
  ]
}
```

### Índices

Se crean al arrancar la API y, si Mongo no respondía en ese momento, en la primera petición que lo use. Crearlos de nuevo no cambia nada.

| Nombre | Campos | Tipo |
|---|---|---|
| `uq_servicio_reporte` | `(servicio, reporte_id)` | único, parcial: solo si existe `reporte_id` |
| `uq_servicio_solicitud_cama` | `(servicio, solicitud_cama_id)` | único, parcial: solo si existe `solicitud_cama_id` |
| `uq_servicio_remision` | `(servicio, remision_id)` | único, parcial: solo si existe `remision_id` |
| `ix_eps_fecha` | `(eps_id, fecha desc)` | listado por EPS, lo más reciente primero |
| `ix_paciente_fecha` | `(paciente_id, fecha desc)` | filtro por paciente |
| `ix_estado_servicio` | `(estado_pago, servicio)` | filtros y resumen |

### Idempotencia

Una factura automática no puede duplicarse porque lo impide la base, no solo el código: los índices únicos parciales rechazan un segundo documento con el mismo `(servicio, reporte_id)` o `(servicio, solicitud_cama_id)`. El repositorio trata ese rechazo como **"ya facturado"**: devuelve la factura existente, no lanza un error y no vuelve a avisar al contable. Por eso reprocesar un evento, correr la conciliación varias veces o reintentar tras un fallo es seguro. Las facturas manuales no llevan ninguno de esos campos, así que nunca chocan con los índices.

## Flujos automáticos (R19, flujo 3)

La facturación **no está pegada a los endpoints**: es un reactor del bus de eventos (`app/billing/reactors.py`, registrado en `app/events/__init__.py`). Quien aprueba o libera solo publica el hecho, ver `docs/EVENTOS.md`.

### Aprobar un reporte de IA

1. El clínico aprueba el reporte. El servicio hace commit y publica `reporte_aprobado`.
2. El reactor de facturación crea la factura `analisis_ia` con `reporte_id` y valor de la tarifa.
3. Si la factura es nueva, publica `factura_creada` a los **contables de la EPS del paciente** y a los **administradores**: queda en su bandeja y llega por el canal en vivo (WebSocket o SSE) sin recargar.
4. El médico, el especialista y el paciente **no reciben nada** de esto.
5. La creación queda en la auditoría (`INVOICE_CREATED`) con `paciente_id`, `factura_id` y `reporte_id`.

### Liberar una cama tras una ocupación

1. `release_bed` hace commit y publica `cama_liberada`.
2. El reactor crea la factura `dia_cama`: `cantidad` = días entre `assigned_at` y `released_at`, **redondeados hacia arriba, mínimo 1**; `valor_unitario` según el servicio de la cama (tarifario de abajo); enlazada con `solicitud_cama_id` y `cama_id`; `ips_id` es la IPS de la cama.
3. Publica `factura_creada` igual que en el caso anterior.

### Remisión

**Remisiones (Epic 7).** Cuando el especialista **acepta** una remisión, `referrals.accept` hace el commit y publica `remision_aceptada`; el reactor de facturación llama a `app.billing.service.bill_referral(db, patient_id=…, remision_id=…, ips_id=…)`, que crea la factura `remision` (idempotente por `remision_id`, facturada a nombre de la IPS del especialista) y avisa a los contables y a los administradores con `factura_creada`. Si Mongo está caído, la aceptación se completa igual y la conciliación recupera la factura. Una remisión rechazada no se factura.

### Si MongoDB no responde

La aprobación y la liberación **siguen funcionando**: el reactor registra el fallo en el log y termina, y el bus aísla cualquier error de un reactor. La factura queda sin crear hasta que la conciliación la recupere. La API arranca igual aunque Mongo esté caído.

## Conciliación

`POST /api/v1/facturas/reconciliar` (admin y contable) es la red de seguridad de los reactores. Recorre los **reportes de IA aprobados**, las **estancias de cama terminadas** (asignaciones liberadas) y las **remisiones aceptadas o completadas**, y crea la factura que falte. Un contable solo concilia lo de su EPS; el admin, todo. Es idempotente: lo ya facturado se omite. Respuesta:

```json
{
  "analisis_ia": { "revisados": 12, "creadas": 2, "ya_facturados": 10 },
  "dia_cama":    { "revisados": 40, "creadas": 1, "ya_facturados": 39 },
  "remision":    { "revisados": 3,  "creadas": 1, "ya_facturados": 2 },
  "total_creadas": 4
}
```

**Un solo aviso por destinatario.** La conciliación **no** publica un `factura_creada` por cada factura (la primera conciliación de 522 facturas dejó más de 99 avisos sin leer en la bandeja del contable). Publica un único `facturas_conciliadas` por destinatario, con el mensaje «Conciliación: se crearon N facturas»: cada contable recibe el conteo de su EPS y los administradores, el total. Si no se creó ninguna, no avisa. El reactor en vivo de una aprobación individual sí sigue avisando una factura por notificación.

Para limpiar la bandeja de un contable que ya recibió el aluvión anterior, ver el procedimiento de despliegue de `docs/EVENTOS.md` (marcar como leídas las notificaciones `factura_creada` con origen de conciliación).

Úsela después de una caída de Mongo, y una vez al desplegar por primera vez (para facturar lo anterior a la contabilidad).

## Estados de pago

```
            pagar                 anular (motivo obligatorio)
pendiente ─────────► pagada       pendiente ─────────► anulada
```

| Desde | Hacia `pagada` | Hacia `anulada` |
|---|---|---|
| `pendiente` | permitido | permitido |
| `pagada` | 409 | 409 |
| `anulada` | 409 | 409 |

`pagada` y `anulada` son estados finales. Decisión: una factura pagada no se anula; si hubo un error, la corrección es contable (una nota o una factura nueva), no reescribir el pago. La transición es atómica: el estado esperado va en el filtro de la actualización, así que dos peticiones simultáneas no pueden ganar las dos. Cada cambio agrega una entrada a `historial` y deja `INVOICE_PAID` o `INVOICE_VOIDED` en la auditoría.

## Endpoints

Todos exigen token. Solo `admin` y `contable`; medicos, especialistas, pacientes y coordinadores reciben **403** (el rol se comprueba antes de tocar Mongo).

| Método y ruta | Descripción |
|---|---|
| `GET /api/v1/facturas?estado_pago=&servicio=&paciente_id=&desde=&hasta=&limit=` | Lista, la más reciente primero. `desde` y `hasta` son días (`AAAA-MM-DD`, UTC, ambos inclusive). |
| `POST /api/v1/facturas` | Factura manual (201). `servicio` solo `consulta` o `medicamento`. Cuerpo: `paciente_id`, `servicio`, `descripcion`, `cantidad` (1 por defecto), `valor_unitario` (opcional en consulta, obligatorio en medicamento), `ips_id` (opcional). |
| `GET /api/v1/facturas/{id}` | Detalle con `historial`. |
| `POST /api/v1/facturas/{id}/pagar` | Cuerpo opcional `{nota}`. |
| `POST /api/v1/facturas/{id}/anular` | Cuerpo `{motivo}` obligatorio. |
| `GET /api/v1/facturas/{id}/recibo.pdf` | Recibo: paciente (nombre y documento desde PostgreSQL), EPS, línea de detalle, total y estado. |
| `GET /api/v1/facturas/resumen` | Totales por estado de pago y por servicio. Las anuladas cuentan en `por_estado` pero no en `por_servicio` ni en `total_facturado`. |
| `POST /api/v1/facturas/reconciliar` | Conciliación (ver arriba). |

Cada elemento de la lista trae `id`, `paciente_id`, `servicio`, `valor`, `fecha`, `estado_pago` y, en las automáticas de análisis de IA, `reporte_id` (contrato de la sección 7 de `REQUISITOS.md`).

### Alcance del contable

Un `contable` pertenece a una EPS (`users.organization_id`, obligatorio al crear la cuenta). Solo ve, crea, paga, anula y concilia facturas con `eps_id` igual a esa EPS, y solo puede facturar pacientes de ella. Una factura o un paciente de otra EPS responde **404**, igual que uno inexistente, para no revelar que existe. El `admin` no tiene filtro.

Si MongoDB no está configurado o no responde, las rutas de contabilidad responden **503** con un mensaje en español y el resto del sistema sigue funcionando.

## Auditoría (R13)

| Acción | Cuándo | Metadatos |
|---|---|---|
| `INVOICE_CREATED` | Se crea una factura (automática, manual o por conciliación) | `paciente_id`, `factura_id`, `reporte_id` (si hay), `servicio`, `valor`, `origen`, `via` |
| `INVOICE_PAID` | Se registra el pago | los mismos, más `desde` y `nota` |
| `INVOICE_VOIDED` | Se anula | los mismos, más `desde` y la `nota` con el motivo |

`INVOICE_CREATED` ya existía; `INVOICE_PAID` e `INVOICE_VOIDED` los agrega la migración `e5a9c2d4b681`. Las automáticas se auditan sin usuario (las crea el sistema).

## Tarifas (ilustrativas)

Los valores están en `app/billing/tariffs.py` y son **inventados pero verosímiles**, para la demostración del proyecto. No corresponden a un manual tarifario oficial (SOAT, ISS 2001) ni a un contrato entre una EPS y una IPS. Cambiarlos solo exige editar ese archivo.

| Servicio | Valor (COP) |
|---|---|
| Análisis de IA | 45.000 |
| Consulta | 85.000 |
| Remisión | 60.000 |
| Día cama, hospitalización | 380.000 |
| Día cama, urgencias | 260.000 |
| Día cama, UCI | 1.850.000 |

El servicio de la cama decide la tarifa: un nombre que contiene "UCI" o "intensivo" se factura como UCI; "urgencias", como urgencias; cualquier otro, como hospitalización. El medicamento no tiene tarifa: quien factura indica el valor unitario.

## Infraestructura

- Servicio `mongo` en `docker-compose.yml`: imagen `mongo:7`, volumen con nombre `saludred-mongo-data`, healthcheck con `mongosh`, **sin puertos publicados** (solo la API lo alcanza, por la red de Docker).
- Variables: `MONGO_ROOT_USER` (por defecto `saludred`), `MONGO_ROOT_PASSWORD` (**obligatoria**, sin valor por defecto: sin ella `docker compose` no arranca) y `MONGO_DB` (por defecto `saludred_contabilidad`). La API recibe `MONGO_URL` armada con esas variables. La clave debe ser alfanumérica, porque va dentro de la URL sin codificar.
- Dependencia: `pymongo` (versión fija en `requirements.txt`). Las pruebas usan `mongomock` y nunca necesitan un Mongo real.
