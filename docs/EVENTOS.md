# Eventos, canal en tiempo real y Subscription de FHIR

Este documento describe el sistema reactivo de SaludRed: cómo una acción de negocio (o un dato nuevo que llega al servidor FHIR) se convierte en una notificación persistente y en un aviso en vivo, sin que nadie recargue la página ni oprima un botón intermedio (requisitos R17, R18, R19 y R21).

## Piezas

```
 acción de negocio ──► DomainEvent ──► publish() ──► reactores
 (hook FHIR, analizar,                (después del      ├─ notificaciones: una fila en la bandeja por destinatario
  aprobar, remitir, liberar            commit)          │     └─► canal en vivo (WebSocket o SSE) a las conexiones abiertas
  cama, bloquear cuenta,                                ├─ facturación: factura en MongoDB y `factura_creada`
  temporizador de alertas)                              ├─ alertas: `reporte_generado` crítico ──► alerta activa
                                                        └─ contadores: `pendientes_actualizados` a los afectados
```

| Pieza | Archivo | Qué hace |
|---|---|---|
| Evento de dominio | `app/events/bus.py` | `DomainEvent`: `tipo`, `mensaje`, `paciente_id`, `datos`, `fecha`, destinatarios (ids de usuario), la marca `clinical` y la marca `persist` (`false` = solo en vivo, no entra a la bandeja). |
| Publicación | `app/events/bus.py` | `publish(evento, db)` llama a los reactores registrados, de forma síncrona y **después del commit** de quien publica. Un reactor que falla se registra en el log y se aísla: no rompe la acción de negocio ni a los demás reactores. |
| Reactor de notificaciones | `app/events/reactors.py` | Guarda una fila por destinatario en `notifications` y la empuja a sus conexiones abiertas. Con `persist=false` solo empuja. |
| Reactor de facturación | `app/billing/reactors.py` | Reacciona a `reporte_aprobado`, `cama_liberada` y `remision_aceptada`: crea la factura en MongoDB y publica `factura_creada`. |
| Reactor del análisis automático | `app/events/flows.py` | `ai_auto_reactor`: reacciona a `observacion_registrada` y a `fhir_observacion`, y **solo agenda** (hilo aparte, sesión propia) el análisis de IA del encuentro cuando ya tiene los seis signos vitales. Ver `docs/IA.md`, «Análisis automático». |
| Reactor de alertas | `app/events/flows.py` | `alert_reactor`: reacciona a `reporte_generado` y, si el modelo lo marcó crítico, crea la alerta y publica `alerta_critica`. |
| Reactor de contadores | `app/events/flows.py` | `counters_reactor`: tras cualquier evento que mueve un contador, publica `pendientes_actualizados` a los usuarios afectados. |
| Monitor de alertas | `app/services/alert_monitor.py` | Temporizador interno (ver «Escalamiento»): escala alertas sin atender y levanta la alerta de cama sin limpiar. |
| Canal en vivo | `app/events/channel.py` | Registro en memoria `usuario -> conexiones`, con una `asyncio.Queue` por conexión y un filtro opcional por `tipo`. |
| Endpoint WebSocket | `app/api/routes/events.py` | `GET /api/v1/events/ws?token=<jwt>`. Canal público (funciona por el túnel). |
| Endpoint SSE | `app/api/routes/events.py` | `GET /api/v1/events/stream?token=<jwt>`. Canal de la red local. |
| Bandeja | `app/api/routes/notifications.py` | `GET /api/v1/notifications` y `POST /api/v1/notifications/{id}/read`. |
| Remisiones | `app/api/routes/referrals.py` | `POST /api/v1/referrals` y las acciones del especialista. |
| Alertas y contadores | `app/api/routes/alerts.py` | `GET /api/v1/alerts`, `POST /api/v1/alerts/{id}/acknowledge` y `GET /api/v1/pendientes`. |

La lógica de negocio no sabe quién debe enterarse ni qué más debe ocurrir: arma el evento con sus destinatarios y publica. **Ningún flujo está pegado a un endpoint**: remitir, aprobar o bloquear solo publican; notificar, facturar, alertar y contar lo hacen los reactores. Para sumar un flujo nuevo basta con registrar otro reactor (`register_reactor`).

## Catálogo de eventos

| `tipo` | Quién lo dispara | A quién llega | Reacción |
|---|---|---|---|
| `fhir_observacion` | El receptor del rest-hook (`POST/PUT /api/v1/fhir/hooks/...`) cuando HAPI entrega una `Observation` nueva de un paciente conocido | Al **médico responsable** del paciente; si no hay o su cuenta no está activa, a los médicos y especialistas de la IPS del encuentro más reciente; si no hay ninguno, a los administradores | Fila en la bandeja, aviso en vivo, observación guardada con origen `fhir` y auditoría `FHIR_EVENT_RECEIVED` con origen FHIR |
| `observacion_registrada` | `observations.create_observation`, después del commit (un signo vital guardado por la API) | A nadie; solo en vivo, sin fila en la bandeja (`persist=false`, sin destinatarios) | `ai_auto_reactor`: agenda el análisis automático del encuentro. `datos`: `observacion_id`, `encounter_id`, `codigo` |
| `reporte_generado` | `ai_reports.announce_generated`, después del commit del reporte `pendiente`, ya sea el que crea el análisis automático (`ai_auto`) o el de la ruta manual `request_analysis`. `datos.origen` dice cuál | A nadie por la bandeja | `alert_reactor`: si el reporte es crítico, crea o actualiza la alerta del paciente y publica `alerta_critica`. `counters_reactor`: sube `reportes_por_revisar` de los clínicos del alcance |
| `analisis_ia_pendiente` | `ai_auto`, justo antes de `reporte_generado` (el médico se entera primero; la alerta y los contadores hacen varias consultas), solo para reportes **automáticos** | Al **médico responsable** del paciente; si no hay o su cuenta no está activa, a los médicos y especialistas de la IPS del encuentro más reciente; si no hay ninguno, a los administradores. Nunca al paciente ni al contable (evento clínico) | Fila en la bandeja y aviso en vivo: «Nuevo análisis de IA pendiente de firma: <nombre del paciente>». El front refresca el reporte en la ficha sin recargar. `datos`: `reporte_id`, `encounter_id`, `critico`, `origen`, `paciente_nombre` |
| `reporte_aprobado` | `ai_reports.approve`, después del commit, cuando un médico o especialista aprueba un reporte de IA | A nadie por la bandeja (sin destinatarios): es un hecho de dominio | El **reactor de facturación** crea la factura `analisis_ia` en MongoDB, ligada con `reporte_id`. Si es nueva, publica `factura_creada`. Reprocesar el evento no duplica la factura ni avisa de nuevo. Si Mongo está caído se registra el fallo y la aprobación sigue (se recupera con la conciliación). Baja `reportes_por_revisar` |
| `reporte_rechazado` | `ai_reports.reject`, después del commit | A nadie por la bandeja | Baja `reportes_por_revisar` (el reporte queda como evidencia, no se factura) |
| `cama_liberada` | `beds.release_bed`, después del commit, al liberar una asignación | A nadie por la bandeja | El reactor de facturación crea la factura `dia_cama` (días redondeados hacia arriba, mínimo 1, tarifa según el servicio de la cama), ligada a `solicitud_cama_id` y `cama_id`, y publica `factura_creada` |
| `factura_creada` | El reactor de facturación al crear una factura automática nueva (reporte, cama o remisión) | A los **contables de la EPS del paciente** y a los **administradores**. Nunca al médico, al especialista ni al paciente | Fila en la bandeja y aviso en vivo. No es un evento clínico (`clinical=false`), por eso el contable lo recibe. `datos`: `factura_id`, `reporte_id`, `servicio`, `valor`, `estado_pago` |
| `facturas_conciliadas` | `POST /api/v1/facturas/reconciliar`, una vez al terminar | A cada **contable de la EPS** (cuántas facturas de su EPS se crearon) y a los **administradores** (el total). No se envía si no se creó ninguna | **Un solo aviso por destinatario**: «Conciliación: se crearon N facturas». La conciliación no usa `factura_creada`, para que un historial de cientos de facturas no entierre la bandeja. `datos`: `creadas`, `por_servicio` |
| `remision_creada` | `referrals.create` (`POST /api/v1/referrals`), después del commit | Al **especialista destinatario**. El paciente, el contable y el remitente no reciben nada | Fila en la bandeja y aviso en vivo. Sube `remisiones_recibidas` del especialista. El especialista puede leer la historia del paciente (ver «Acceso por remisión») |
| `remision_aceptada` | `referrals.accept`, después del commit | Al **médico remitente** | Fila y aviso en vivo. El reactor de facturación llama a `bill_referral`: factura `remision` ligada con `remision_id` (idempotente; si Mongo está caído la aceptación igual se completa y la conciliación la recupera) y publica `factura_creada`. Baja `remisiones_recibidas` |
| `remision_rechazada` | `referrals.reject` (con motivo), solo desde `pendiente` | Al médico remitente, con el motivo | Fila y aviso en vivo. Baja `remisiones_recibidas`. No se factura y deja de dar acceso a la historia |
| `remision_completada` | `referrals.complete` (`POST /api/v1/referrals/{id}/report`), de `aceptada` a `completada` | Al médico remitente | Fila y aviso en vivo: el informe del especialista está disponible |
| `alerta_critica` | `alert_reactor`, cuando un análisis crea un reporte crítico y no hay ya una alerta activa del paciente | Al **médico responsable**; si no hay o su cuenta no está activa, a los médicos y especialistas de la IPS; si no hay ninguno, a los administradores. Nunca al paciente ni al contable | Fila y aviso en vivo. La alerta queda `activa` hasta que un clínico la reconoce con una nota. `datos`: `alerta_id`, `reporte_id`, `nivel`, `probabilidad` |
| `alerta_reconocida` | `alerts.acknowledge`, o el monitor cuando una alerta de cama se resuelve sola | A todos los que ven la alerta (administradores, clínicos del alcance, coordinación si es de cama) | **Solo en vivo** (`persist=false`): las demás pantallas abiertas retiran el aviso. La bandeja conserva el aviso original y la alerta guarda quién la reconoció |
| `alerta_escalada` | `alerts.escalate_overdue`, desde el monitor, cuando una alerta lleva más de `ALERT_ESCALATION_MINUTES` activa | A los **administradores** y a los **demás** médicos y especialistas de la IPS (no a quienes ya la recibieron) | Fila y aviso en vivo, una sola vez por alerta, con auditoría `ALERT_ESCALATED` |
| `alerta_cama_sin_limpiar` | `alerts.sync_bed_alerts`, desde el monitor, para una cama en limpieza sin evidencia aprobada tras `BED_CLEANING_ALERT_MINUTES` | A la **coordinación de red** y a los **médicos de la IPS de la cama** | Fila y aviso en vivo (un evento por IPS aunque sean varias camas). No cuenta para especialistas, paciente ni contable |
| `cuenta_bloqueada` | `auth.authenticate`, después de confirmar el bloqueo (3 intentos fallidos) | A **todos los administradores**, con el usuario afectado en `mensaje` y en `datos.usuario` | Fila y aviso en vivo. El admin la desbloquea con `POST /api/v1/admin/users/{id}/unlock`. No se publica para usuarios que no existen |
| `pendientes_actualizados` | `counters_reactor`, después de cualquier evento que mueva un contador | A los usuarios afectados que tengan una conexión abierta | **Solo en vivo**. `datos` trae los tres contadores ya calculados, para que el front los pinte sin consultar |

Mensaje de ejemplo: `Nuevo resultado de laboratorio desde FHIR: Glucosa 182 mg/dL`. El evento lleva en `datos` el recurso (`Observation/{id}`), la versión, el código LOINC, el valor y si la observación se pudo guardar.

La facturación (Epic 6) cuelga de este bus; ver `docs/CONTABILIDAD.md`. La remisión la factura el gancho `app.billing.service.bill_referral`, que el reactor de facturación llama al aceptar. El bloqueo manual de una cuenta por un administrador (`POST /admin/users/{id}/lock`) queda auditado pero no publica `cuenta_bloqueada`: el requisito habla del bloqueo por intentos fallidos, y quien bloquea a mano ya es un admin.

### Quién no recibe nada

Los eventos clínicos (todos menos `factura_creada`, `facturas_conciliadas` y `cuenta_bloqueada`) llevan `clinical=true`: **nunca** llegan a los roles `paciente` y `contable`, aunque alguien los liste como destinatarios. Lo impiden dos controles independientes (el reactor de notificaciones filtra por rol y el registro vuelve a filtrar al empujar). Las pruebas lo verifican flujo por flujo: ante una remisión, un análisis crítico o una alerta, la bandeja del paciente y la del contable quedan vacías.

## Flujo remitir → aceptar

```
médico (IPS A)      API / referrals           bus + reactores                       especialista (IPS B)   contable
     │                    │                         │                                       │                 │
     │ POST /referrals    │                         │                                       │                 │
     │───────────────────►│ valida: rol medico,     │                                       │                 │
     │                    │ paciente de su IPS,     │                                       │                 │
     │                    │ destino = especialista  │                                       │                 │
     │                    │ activo; INSERT; audita  │                                       │                 │
     │                    │ REFERRAL_CREATED; COMMIT│                                       │                 │
     │                    │ publish(remision_creada)│                                       │                 │
     │                    │────────────────────────►│ notification_reactor: fila + push ───►│ campana + aviso │
     │                    │                         │ counters_reactor: pendientes_actualizados ─► remisiones_recibidas 1
     │◄── 201 {"id": …} ──│ (en segundo plano: ServiceRequest `active` a FHIR)               │                 │
     │                    │                         │                                       │                 │
     │                    │◄───────────────── POST /referrals/{id}/accept ──────────────────│                 │
     │                    │ solo el destinatario;   │                                       │                 │
     │                    │ pendiente → aceptada;   │                                       │                 │
     │                    │ audita; COMMIT          │                                       │                 │
     │                    │ publish(remision_aceptada)                                      │                 │
     │                    │────────────────────────►│ notification_reactor ──► aviso al médico remitente    │
     │ campana + aviso ◄──│─────────────────────────│ billing_reactor: bill_referral ─► factura en MongoDB  │
     │                    │                         │      └─ publish(factura_creada) ────────────────────────►│ aviso en vivo
     │                    │                         │ counters_reactor ───────────────────►│ remisiones_recibidas 0
     │                    │◄─── 200 {"estado":"aceptada"} ──────────────────────────────────│                 │
```

Después de aceptar, el especialista puede subir imágenes por los endpoints de imagen de siempre (el estudio queda registrado en su IPS) y entregar su informe con `POST /api/v1/referrals/{id}/report`, que pasa la remisión a `completada` y avisa al médico remitente.

## Remisiones (R08)

| Endpoint | Rol | Respuesta |
|---|---|---|
| `POST /api/v1/referrals` | Solo `medico`. `paciente`, `contable`, `coordinador`, `admin` y `especialista`: 403 | 201 `{"id", "paciente_id", "estado": "pendiente", …}` |
| `GET /api/v1/referrals?rol=recibidas\|enviadas&estado=` | `especialista` (recibidas por defecto), `medico` (enviadas por defecto), `admin` (todas) | Lista con `id`, `paciente_id`, `paciente_nombre`, `motivo`, `estado`, remitente y especialista (id y nombre), `especialista_organizacion`, `motivo_rechazo`, `informe` y las fechas |
| `GET /api/v1/referrals/{id}` | El remitente, el destinatario y el admin; otros: 404 | Una remisión |
| `GET /api/v1/referrals/specialists` | `medico`, `admin` | `[{id, nombre, organizacion}]` de los especialistas activos de toda la red |
| `POST /api/v1/referrals/{id}/accept` | Solo el especialista destinatario | 2xx, `estado: "aceptada"`. Desde otro estado: 409 |
| `POST /api/v1/referrals/{id}/reject` `{motivo}` | Solo el destinatario, solo desde `pendiente` | `estado: "rechazada"`. Desde otro estado: 409 |
| `POST /api/v1/referrals/{id}/report` `{informe}` | Solo el destinatario, solo desde `aceptada` | `estado: "completada"`. Desde otro estado: 409 |

Reglas: el destino de una remisión es un **especialista activo de cualquier IPS** de la red (en una red de EPS, remitir a otra IPS es lo normal); el médico necesita tener al paciente en su IPS; repetir una remisión al mismo especialista crea otra solicitud (no se bloquea). Cada paso queda en la auditoría (`REFERRAL_CREATED`, `REFERRAL_ACCEPTED`, `REFERRAL_REJECTED`, `REFERRAL_COMPLETED`). La tabla `referrals` no tiene borrado lógico: una remisión es parte de la trazabilidad de la atención y no se borra, como un reporte de IA.

### Acceso por remisión

Un especialista con una remisión `pendiente`, `aceptada` o `completada` para un paciente **puede leer su historia fuera de su IPS**: el paciente, sus encuentros, sus observaciones, sus imágenes (y la vista previa), los reportes de IA (los aprobados en la historia, y los pendientes en la bandeja de revisión) y la historia clínica en JSON, PDF o Bundle FHIR. Una remisión **rechazada** no da acceso, ni la de otro especialista, ni la del mismo especialista para otro paciente. La regla vive en un solo sitio, `app/services/patient_access.py` (`referred_patient_ids`, `scope_clause`, `ensure_scope_or_referral`), y todos los puntos de lectura la usan. El acceso es de lectura y de análisis: el especialista puede registrar estudios en su propia IPS para el paciente, pero no edita registros de otra IPS. Limitación conocida: el médico remitente ve el informe en la remisión, pero las imágenes que el especialista registre en su IPS no aparecen en la ficha del remitente (la regla inversa no existe).

### FHIR

Cada remisión se sincroniza como `ServiceRequest` R4, en segundo plano y sin bloquear la respuesta (el mismo mecanismo que `fhir_autosync`: si HAPI está caído, la remisión no falla), al crearla y en cada cambio de estado. `status`: `active` (pendiente y aceptada), `completed` o `revoked` (rechazada); `intent: order`; `subject` es la referencia FHIR del paciente; `requester` y `performer` llevan nombre e identificador; `reasonCode.text` es el motivo. Lleva la etiqueta de origen `meta.tag` (`urn:saludred:identifier:origin|saludred`). Se actualiza por identificador, así que un cambio de estado actualiza el mismo recurso. El id del servidor queda en `referrals.fhir_resource_id`.

## Bloqueo de cuenta → administradores

`auth.authenticate` cuenta los intentos fallidos. Al tercero fija `locked_at`, audita `ACCOUNT_LOCKED`, hace el commit y **recién entonces** publica `cuenta_bloqueada` (un reactor nunca ve un bloqueo que se revirtió). La respuesta al que intenta entrar no cambia: es la misma 423 de siempre, y un usuario que no existe responde igual que uno con clave errónea y no genera ningún aviso (no se filtra qué cuentas existen). Un cuarto intento, aun con la clave correcta, se rechaza. Además, `get_current_user` y los canales en vivo rechazan (401) cualquier token de una cuenta bloqueada o deshabilitada, con la misma comprobación: un token emitido antes del bloqueo deja de servir en la siguiente petición, sin esperar a que venza.

## Alertas críticas (R20)

**Disparo.** Cuando un análisis de IA **crea** un reporte clasificado crítico (`critico = true`, probabilidad a partir del umbral del modelo), no al aprobarlo: la demostración necesita la alerta justo después de «ejecuta el análisis». Nivel de la alerta: `critico` si la probabilidad es de 0,80 o más; `alto` entre el umbral del modelo y 0,80. **Una sola alerta activa por paciente:** si ya hay una activa, el nuevo reporte crítico se adjunta a ella (`reporte_id` y mensaje pasan al último) y nadie recibe un segundo aviso; un índice único parcial en la base lo garantiza aunque dos análisis se crucen. Cuando la alerta se reconoce, el siguiente análisis crítico crea una nueva. Con el modelo actual el 82 % de los pacientes queda marcado como crítico (ver `docs/IA.md`): esta regla es la que evita una avalancha de avisos.

**Quién ve qué.** `medico` y `especialista` ven las alertas dirigidas a ellos o sobre pacientes de su alcance (su IPS, o una remisión en el caso del especialista); el `admin` ve todas; la `coordinación` ve las de cama; `paciente` y `contable` reciben 403: ni las ven ni las reconocen.

**Reconocer.** `POST /api/v1/alerts/{id}/acknowledge` con `{"nota": "..."}`. La nota es obligatoria (422 si falta o está en blanco). Solo `medico` o `especialista` con acceso al paciente reconocen una alerta clínica; **el `admin` la lee pero no la reconoce** (403), igual que no firma reportes de IA: quien responde por el paciente es quien la reconoce. Una alerta ya reconocida responde 409. Quien no la ve recibe 404. Al reconocerla se audita `ALERT_ACKNOWLEDGED` (con paciente, reporte y nota) y se publica `alerta_reconocida` para que las otras pantallas abiertas retiren el aviso.

### Escalamiento (R20.4)

El monitor (`app/services/alert_monitor.py`) corre dentro del ciclo de vida de FastAPI (`lifespan`): un bucle `asyncio` que cada `ALERT_MONITOR_INTERVAL_SECONDS` (30 s) abre una **sesión nueva**, ejecuta un `tick` y la cierra. Cada uno de los dos trabajos del `tick` está aislado de errores y el bucle sobrevive a cualquier excepción; solo se detiene al cancelarse en el apagado.

Si una alerta sigue `activa` más de `ALERT_ESCALATION_MINUTES` (**5 por defecto**), el monitor fija `escalated_at` y `escalated_to`, audita `ALERT_ESCALATED` y publica `alerta_escalada` a los administradores y a los demás médicos y especialistas de la IPS del paciente (no a quienes ya la recibieron). **Se escala una sola vez**: `escalated_at` saca la alerta de la consulta. Una alerta escalada se puede reconocer igual.

> **Para la demostración:** poner `ALERT_ESCALATION_MINUTES=1` en el `.env` y reiniciar la API. La alerta se escala entre 1 y 1,5 minutos después de crearse (un minuto de espera más hasta 30 s de intervalo del monitor).

**Por qué esto no es sondeo ciego.** R21 acepta cualquier mecanismo «mientras no sea un sondeo periódico ciego» de un servidor externo. Este temporizador no le pregunta nada a nadie: aplica una regla de tiempo («lleva N minutos sin reconocerse») sobre **filas que este mismo sistema escribió**, igual que un trabajo programado de la base de datos. Es la única forma de que ocurra algo cuando nadie hace nada, que es justo la definición de escalar. Los datos externos (HAPI) siguen llegando por la `Subscription` rest-hook, sin sondeo.

**Por qué es válido en el proceso de la API.** El registro de conexiones ya obliga a un solo worker (ver «Restricción: un solo worker»), y `tests/test_deploy_constraints.py` lo hace cumplir. Con varios workers cada uno correría su propio monitor y escalaría dos veces; con uno, no hay carrera. Si la API escalara, el monitor pasaría a un proceso aparte o a un bloqueo en la base, junto con el broker del canal. Se desactiva con `ALERT_ESCALATION_ENABLED=false` (las pruebas lo hacen y llaman a las funciones con un reloj congelado).

### Alerta de cama libre sin limpiar

La frase de la clase: *«está libre, pero no ha sido limpiada: esta cama está perdiendo tiempo»*. El mismo monitor aplica la lógica de `GET /beds/cleaning/overdue` (`cleaning.beds_cleaning_overdue`): una cama en `CLEANING` sin evidencia de limpieza **aprobada** posterior a su ingreso a limpieza.

- **Cuándo:** cuando la cama lleva `BED_CLEANING_ALERT_MINUTES` (60 por defecto) en ese estado. Se crea una alerta `cama_sin_limpiar` (nivel `alto`, `cama_id`, `organizacion_id`, sin paciente), con auditoría `ALERT_CREATED`.
- **Sin duplicados:** una sola alerta activa por cama (índice único parcial). Si se acumulan varias camas de una IPS, el aviso es **un solo evento por IPS** («N camas están libres, pero no han sido limpiadas…»), para que un atraso no inunde la bandeja.
- **A quién va:** la coordinación de red y los médicos de la IPS de la cama (`tipo`: `alerta_cama_sin_limpiar`). El administrador la ve en la lista. Para un especialista, el paciente o el contable no existe y no cuenta en sus contadores.
- **Cómo se cierra:** la reconoce con nota la **coordinación, el admin o un médico de esa IPS**, o **se resuelve sola** cuando la limpieza se aprueba o la cama sale de `CLEANING`: el monitor lo detecta en el siguiente tick, la pasa a `reconocida` sin autor, con la nota «Resuelta automáticamente…», audita `ALERT_ACKNOWLEDGED` con `auto: true` y publica `alerta_reconocida`. Si la cama vuelve a quedar sin limpiar, nace una alerta nueva.
- **Escalamiento:** como cualquier alerta, a los 5 minutos activa se escala a los administradores y a los especialistas de la IPS (la coordinación y los médicos ya la recibieron).

### Contrato `paciente_critico`

Valores clínicos que **garantizan** el disparo con el modelo desplegado (regresión logística de criticidad, ver `docs/IA.md`): saturación de oxígeno 86 %, frecuencia cardíaca 118, presión sistólica 92, diastólica 58, frecuencia respiratoria 28, temperatura 38,9 °C y 78 años. Con ellos el modelo da una probabilidad de ≈ 0,999 (Epic 4), muy por encima del umbral (que **no está escrito aquí**: se lee del modelo, `GET /api/v1/ai/models` → `criticality.threshold`, hoy 0,415, y cada reporte lo trae en `threshold`), y por encima de 0,80, así que la alerta nace con `nivel: "critico"`. Un paciente de 40 años con signos normales (FC 75, FR 14, SpO2 99 %, PAS 120, PAD 78, 36,6 °C, ESI 4) da ≈ 0,272 y **no** dispara.

Secuencia que usaría un probador (token de un `medico`; `<EPS>` es el id de la EPS de la red; la edad se calcula con la fecha de nacimiento, así que hay que usar una que dé 78 años o más):

```json
POST /api/v1/patients
{ "document_type": "CC", "document_number": "1048000001", "first_name": "Alerta", "last_name": "Critica",
  "birth_date": "1948-01-15", "gender": "male", "eps_organization_id": "<EPS>" }

POST /api/v1/encounters
{ "patient_id": "<ID>", "organization_id": "<IPS del medico>", "encounter_class": "EMER",
  "status": "in-progress", "priority": "URGENT", "reason_text": "Dificultad respiratoria",
  "started_at": "2026-10-03T08:00:00Z" }

POST /api/v1/observations   (una por cada fila; encounter_id = el del paso anterior)
{ "patient_id": "<ID>", "encounter_id": "<ENC>", "code": "59408-5", "display": "Oxygen saturation",
  "value_numeric": 86,   "unit": "%",      "observed_at": "2026-10-03T08:05:00Z" }
  "8867-4"  Heart rate                118   "/min"
  "8480-6"  Systolic blood pressure    92   "mm[Hg]"
  "8462-4"  Diastolic blood pressure   58   "mm[Hg]"
  "9279-1"  Respiratory rate           28   "/min"
  "8310-5"  Body temperature         38.9   "Cel"

POST /api/v1/patients/<ID>/ai-analyses        (cuerpo vacío, o {"triage_level": 2})
```

El análisis responde 201 con `estado: "pendiente"` y `critico: true`. En ese mismo instante: la alerta `activa` aparece en `GET /api/v1/alerts`, el médico responsable recibe `alerta_critica` por el canal en vivo y `pendientes` sube en `reportes_por_revisar` y `alertas_activas`. Las unidades deben ser exactamente las de la tabla: una observación en otra unidad no se envía al modelo.

## Contadores de trabajo pendiente (R18)

`GET /api/v1/pendientes` responde `{"reportes_por_revisar", "remisiones_recibidas", "alertas_activas"}` para quien llama; los cinco roles reciben 200, con ceros donde no aplica.

| Contador | Qué cuenta | Quién tiene valores |
|---|---|---|
| `reportes_por_revisar` | Reportes de IA `pendiente` de pacientes vivos en el alcance del usuario (su IPS; para el especialista, también los pacientes que le remitieron) | `medico`, `especialista` |
| `remisiones_recibidas` | Remisiones `pendiente` dirigidas al usuario | `especialista` |
| `alertas_activas` | Alertas `activa` dirigidas al usuario o visibles para él (ver «Quién ve qué»). Una alerta de cama solo cuenta para la coordinación, el admin y los médicos de esa IPS | `medico`, `especialista`, `coordinador`, `admin` |

Los pacientes con borrado lógico no cuentan en ningún contador ni aparecen en la lista. Los contadores **no se consultan por sondeo**: después de cada evento que mueve uno (`reporte_generado`, `reporte_aprobado`, `reporte_rechazado`, las cuatro `remision_*` y las cuatro `alerta_*`), el `counters_reactor` publica `pendientes_actualizados` a los usuarios afectados, con los tres contadores ya calculados en `datos`:

```json
{ "tipo": "pendientes_actualizados", "mensaje": "Trabajo pendiente actualizado", "leida": true,
  "fecha": "2026-10-03T15:31:30Z", "paciente_id": null,
  "datos": { "reportes_por_revisar": 1, "remisiones_recibidas": 0, "alertas_activas": 1, "motivo": "alerta_critica" } }
```

Es un evento **solo en vivo**: no entra a la bandeja ni se envía a quien no tiene una conexión abierta. Al reconectar, el cliente lee `GET /api/v1/pendientes` y la bandeja para ponerse al día.

## Formato de una notificación y de un evento en vivo

La bandeja y el canal en vivo entregan la misma estructura:

```json
{
  "id": "…", "tipo": "fhir_observacion",
  "mensaje": "Nuevo resultado de laboratorio desde FHIR: Glucosa 182 mg/dL",
  "leida": false, "fecha": "2026-10-03T15:31:30Z",
  "paciente_id": "…", "datos": { "origen": "FHIR", "fhir_resource": "Observation/123" }
}
```

## Canal en vivo (R17): WebSocket por internet, SSE en la red local

Hay **dos endpoints sobre el mismo registro en memoria** y con el mismo JSON de evento (`id`, `tipo`, `mensaje`, `fecha`, `leida`, `paciente_id`, `datos`). Un evento publicado llega por los dos a la vez, según qué conexiones tenga abierto el destinatario.

| | WebSocket | SSE |
|---|---|---|
| URL | `/api/v1/events/ws?token=<jwt>` | `/api/v1/events/stream?token=<jwt>` |
| Dónde sirve | Red local **y** túneles `trycloudflare.com` | Solo red local |
| Rechazo de credenciales | Cierre con código **4401** (se acepta el apretón y se cierra enseguida, para que el navegador reciba el código) | HTTP 401 |
| Latido | Mensaje `{"tipo":"ping"}` cada 15 s | Comentario `: heartbeat` cada 15 s |
| Filtro por tipo (opcional) | `?tipos=a,b` | `?tipos=a,b` |

### Filtro por tipo de evento

Los dos canales aceptan `?tipos=` con una lista de `tipo` separados por comas, por ejemplo `/api/v1/events/ws?token=<jwt>&tipos=alerta_critica,alerta_reconocida`. La conexión recibe **solo** esos tipos; sin el parámetro (o vacío) recibe todos. El filtro se aplica en el registro, por conexión: otra pestaña del mismo usuario sin filtro sigue recibiendo todo. No afecta al latido (`{"tipo":"ping"}` o `: heartbeat`), que no es un evento. El filtro reduce lo que se entrega, nunca amplía el alcance: el aislamiento por rol se aplica antes.

### Cuentas bloqueadas y deshabilitadas

Los canales y el resto de la API usan **la misma comprobación** (`user_from_token`): un token de una cuenta bloqueada o deshabilitada se rechaza (401, o cierre 4401 en el WebSocket) aunque haya sido emitido antes del bloqueo. Cada petición relee la cuenta en la base; no hay que esperar a que el token venza.

### Por qué hay dos: los túneles rápidos no entregan SSE

Verificado el 03/10/2026: por la red local el SSE entrega `: connected` al instante, el latido cada 15 s y los eventos sin retraso. Por los túneles rápidos de Cloudflare (`trycloudflare.com`, web y API) la respuesta llega con estado 200 y `content-type: text/event-stream`, pero **el cuerpo no se entrega mientras la conexión siga abierta** (130 s sin recibir ni `: connected` ni latidos). Se repitió con un servidor SSE mínimo, con relleno inicial de hasta 128 KB, con HTTP/1.1 y con `cloudflared --protocol http2`, con el mismo resultado. Es una limitación documentada por Cloudflare para los túneles rápidos, y ninguna cabecera la corrige. Los túneles rápidos **sí** admiten WebSocket, y R17 permite SSE **o** WebSocket. Por eso el canal público es el WebSocket y el SSE se conserva para la red local (donde funciona y es más simple de depurar con `curl -N`).

### WebSocket: reglas

- **Autenticación.** Igual que el SSE: el token va en `?token=` (el `WebSocket` del navegador tampoco envía cabeceras) y se relee la cuenta en la base. Sin token, con token inválido o vencido, o con una cuenta deshabilitada o bloqueada, el servidor acepta el apretón y cierra con código **4401**. El cliente no debe reintentar con ese token.
- **Alcance y formato.** Los mismos del SSE: cada usuario recibe solo lo dirigido a él, y un evento clínico no llega al `paciente` ni al `contable`. Cada evento es un mensaje de texto con un JSON.
- **Latido.** Si pasan 15 s sin eventos, el servidor envía `{"tipo":"ping"}`. Cloudflare cierra los sockets inactivos tras unos 100 s; cualquier trama reinicia ese plazo. **Los clientes deben ignorar los mensajes con `tipo: "ping"`**; no son notificaciones.
- **Mensajes del cliente.** Se leen y se ignoran; sirven para detectar que el cliente se fue.
- **Limpieza.** Al cerrarse el socket (por cualquiera de los dos lados, o al cancelarse el manejador) la conexión sale del registro **antes** de cualquier otra espera.
- **Hilos.** Igual que el SSE: `publish` corre en el pool de hilos y el registro empuja con `loop.call_soon_threadsafe` usando el bucle capturado al suscribirse dentro del manejador.
- **Reconexión.** El navegador no reconecta solo. El front lo hace con espera creciente (1, 2, 4… hasta 15 s), relee la bandeja al reconectar y, si tras 3 intentos no logra conectar, consulta `GET /api/v1/notifications?leida=false` cada 20 s y lo indica en pantalla.

### SSE (red local)

- **Autenticación.** El `EventSource` del navegador no envía cabeceras, por eso el token va en `?token=`. Es el mismo JWT del resto de la API, validado igual: se relee la cuenta en la base. Sin token, con un token inválido o vencido, o con una cuenta deshabilitada o bloqueada, la respuesta es 401.
- **Alcance.** Cada usuario recibe únicamente los eventos dirigidos a él. Un evento clínico de un paciente nunca llega al rol `paciente` ni al `contable`, aunque aparezca como destinatario: lo impiden dos controles independientes (el reactor filtra por rol y el registro vuelve a filtrar al empujar).
- **Latido.** Cada 15 s se envía un comentario SSE (`: heartbeat`).
- **Limpieza.** Al desconectarse el cliente (o cerrarse el servidor) la conexión se elimina del registro.
- **Reconexión.** El navegador reconecta solo (el servidor sugiere 3 s). El canal es de **mejor esfuerzo**: la fuente de verdad es la bandeja. Al abrir o reabrir el canal, el cliente debe leer `GET /api/v1/notifications?leida=false` para ponerse al día.
- **Hilos y cola.** Igual que el WebSocket. Si una cola se llena (cliente lento) se descarta el evento más antiguo, no la conexión.

### Qué declarar en `tiempo_real` del contrato (`tester.yaml`)

- Canal: **websocket**, ruta `/api/v1/events/ws`, token por parámetro `token`. Así funciona por la URL pública (túnel).
- Aclarar que el servidor envía `{"tipo":"ping"}` cada 15 s y que debe ignorarse.
- `espera_segundos`: la entrega de una `Observation` por la Subscription de HAPI depende de su planificador de 5 s (ver más abajo); se recomienda declarar unos **15 s** para R21. Para eventos de negocio internos (remisión, etc.) el evento llega en milisegundos.
- Opcional: `?tipos=a,b` limita la conexión a esos tipos de evento (ver «Filtro por tipo de evento»).
- La ruta SSE `/api/v1/events/stream` puede mencionarse como alternativa **solo para la red local**.

### Restricción: un solo worker

El registro de conexiones vive en la memoria del proceso. Con varios workers de uvicorn o varias réplicas de la API, un evento publicado en uno **no llega** a las conexiones abiertas en otro. Por eso:

- el `Dockerfile` arranca un único proceso `uvicorn` (sin `--workers`) y `docker-compose.yml` no define réplicas de `api`;
- una prueba (`tests/test_deploy_constraints.py`) falla si alguien agrega `--workers`, `WEB_CONCURRENCY`, `replicas` o `gunicorn`.

Si la API tuviera que escalar, el cambio es llevar `publish` a un Redis pub/sub y que cada worker empuje a sus propias conexiones. La bandeja en base de datos ya funciona con varias réplicas; solo el canal en vivo necesita el broker.

### nginx

El servicio `web` tiene dos ubicaciones exactas en `frontend/nginx.conf`, que ganan sobre la expresión regular general de `/api/`:

- `/api/v1/events/ws` (WebSocket): `proxy_http_version 1.1`, `proxy_set_header Upgrade $http_upgrade`, `proxy_set_header Connection "upgrade"`, `proxy_buffering off` y `proxy_read_timeout 1h`. `limit_req` aplica una sola vez, en el apretón (un socket abierto es una solicitud), así que no corta sockets abiertos; una tormenta de reconexiones recibiría 429 y el cliente espera más. Las cabeceras de seguridad no afectan al `101`.
- `/api/v1/events/stream` (SSE): `proxy_buffering off`, `proxy_cache off`, `proxy_http_version 1.1`, `proxy_read_timeout 1h` y `X-Accel-Buffering: no`.

Sin estas directivas nginx retiene el tráfico o rechaza el `Upgrade`. La zona `limit_req` general (20 solicitudes por segundo por cliente) no interfiere con ninguno de los dos.

## R21: cómo avisa HAPI a la API (y por qué no es sondeo)

El servidor FHIR avisa; la API no pregunta. Se crea una `Subscription` de FHIR R4 con canal `rest-hook`: cuando HAPI guarda una `Observation`, **HAPI mismo** hace una llamada HTTP a la API. No hay ningún proceso que consulte periódicamente al servidor FHIR.

```
Postman ──POST Observation──► HAPI ──guarda──► coincide con la Subscription
                                                   │  (criteria: Observation?)
                                                   ▼
                              PUT http://api:8000/api/v1/fhir/hooks/Observation/{id}
                              Authorization: Bearer <FHIR_HOOK_SECRET>
                                                   │
                                                   ▼
                                  API: autentica la clave, ignora ecos, aplica
                                  idempotencia, ubica al paciente, guarda la
                                  observación (origen fhir) y audita
                                                   │  publish(fhir_observacion)
                                                   ▼
                        notifications (bandeja)  +  conexiones en vivo del médico responsable
                                                   │
                                                   ▼
                                   navegador del médico (WebSocket): el aviso
                                   aparece sin recargar
```

### La Subscription

- Una sola, creada o refrescada de forma idempotente por `python -m scripts.ensure_fhir_subscription` (dentro del contenedor `api`) o por `POST /api/v1/integration/fhir/subscription` (solo admin). Se ubica por una etiqueta fija (`meta.tag` `urn:saludred:identifier:subscriptions|observation-resthook`), porque el recurso `Subscription` de R4 no tiene `identifier`; ejecutarla de nuevo actualiza en vez de duplicar.
- `status: requested`, `criteria: Observation?`, `channel.type: rest-hook`, `channel.endpoint: FHIR_HOOK_ENDPOINT`, `channel.payload: application/fhir+json` y `channel.header: ["Authorization: Bearer <FHIR_HOOK_SECRET>"]`. HAPI la pasa a `active` al validarla.
- **Por qué `Observation?` y no un criterio más estrecho.** `Observation?code=http://loinc.org|2339-0` cubriría la glucosa de la prueba, pero perdería cualquier otro resultado de laboratorio o de dispositivo, que es justo lo que pide el requisito. `Observation?_tag:not=…` filtraría el eco en HAPI, pero el soporte del modificador `:not` en el emparejador de suscripciones cambia entre versiones y no se puede verificar sin el servidor; si fallara, las entregas se detendrían en silencio. El criterio es amplio y el filtro del eco vive en la API.
- La clave queda guardada en la base de HAPI, en claro, dentro de la `Subscription`. Protege un único endpoint receptor. Si `FHIR_HOOK_SECRET` cambia, hay que ejecutar de nuevo el script.

### Configuración de HAPI

HAPI 7.4 trae las suscripciones rest-hook **apagadas**: acepta el recurso pero no entrega nada. En `docker-compose.yml`, servicio `hapi`:

```
HAPI_FHIR_SUBSCRIPTION_RESTHOOK_ENABLED: "true"
```

Verificación en la ASUS (03/10/2026, HAPI 7.4.0): la forma punteada (`hapi.fhir.subscription.resthook_enabled`) y la de mayúsculas funcionan **cada una por separado**; sin ninguna, el log no dice `REST-hook subscriptions enabled` y HAPI no entrega nada a la API. Se conserva solo la forma en mayúsculas, igual que el resto de las variables `HAPI_FHIR_*` del archivo.

**Latencia.** HAPI 7.4 procesa los cambios de recursos para las suscripciones con una tarea programada que corre cada 5 s (intervalo fijo en el código, no configurable por variable de entorno). El aviso llega, por tanto, entre 1 y 5 s después de que HAPI responde al `POST`, más lo que tarde HAPI en guardar el recurso (cerca de 2 s con su base remota). Medido en la ASUS: entre 5 y 9 s desde que se envía la observación hasta que el evento llega al navegador (5 a 7 s desde la respuesta de HAPI).

Fuente: `application.yaml` y `README.md` de `hapi-fhir-jpaserver-starter` en la etiqueta `image/v7.4.0` (propiedad `hapi.fhir.subscription.resthook_enabled`, `false` por defecto). Si la propiedad estuviera apagada, HAPI rechaza crear la `Subscription` con canal rest-hook, y el script de arranque lo muestra como error.

### Forma de la entrega

Con `channel.payload` definido, HAPI 7.4 entrega un recurso creado o actualizado con una operación `update` de su cliente FHIR contra el endpoint tomado como base: **`PUT {endpoint}/Observation/{id}`** con el recurso en el cuerpo (y `DELETE {endpoint}/Observation/{id}` si se borra). No es un `POST` al endpoint (clase `SubscriptionDeliveringRestHookSubscriber`, método `createDeliveryRequestNormal`). La API acepta ambas formas con el mismo manejador: `PUT /api/v1/fhir/hooks/Observation/{id}` y `POST /api/v1/fhir/hooks/observation` (también `/Observation`). Un `DELETE` se acepta y se ignora: borrar en HAPI no borra historia clínica aquí.

La respuesta es 200 con un `OperationOutcome` mínimo (`application/fhir+json`) en todos los casos tratados, incluidos el eco, el duplicado y el paciente desconocido, para que HAPI no reintente una entrega ya resuelta. Un cuerpo que no es JSON o una `Observation` sin código responden 422.

### Qué hace el receptor

1. **Autentica** la clave compartida con comparación en tiempo constante. Sin clave o con una incorrecta: 401. No es un endpoint de usuario: un JWT no sirve aquí.
2. **Descarta el eco.** Las observaciones que SaludRed envió a HAPI (R15) también disparan la suscripción. Todo recurso que SaludRed envía lleva la etiqueta `meta.tag` `urn:saludred:identifier:origin|saludred` (o un identificador con alguno de sus sistemas); si la entrega la trae, se ignora sin guardar ni notificar. Sin esto cada observación local avisaría dos veces.
3. **Idempotencia.** El par `(id de la Observation, versionId)` se registra una vez en `fhir_inbound_events` (restricción `UNIQUE`). HAPI reintenta las entregas que cree fallidas; la segunda llegada no guarda ni notifica de nuevo. Una versión nueva del mismo recurso es un evento nuevo. Si el recurso no trae `versionId`, se usa un hash del contenido.
4. **Paciente.** Se resuelve por `subject.reference` (`Patient/{id}` en HAPI) a través de `fhir_sync_log`, o por `subject.identifier` con los sistemas del proyecto (`…:documento`, `…:numero`, `…:patient-id`). Un paciente desconocido se audita (origen FHIR) y devuelve 200; no se crea ningún dato.
5. **Guarda** la observación en `observations` con `origin = 'fhir'` y el id del recurso, asociada al encuentro **abierto** del paciente o, si no hay, al más reciente. Si el paciente no tiene ningún encuentro no se guarda la fila: `observations.encounter_id` es obligatorio y el alcance institucional de una observación se hereda de su encuentro, así que una fila sin encuentro no tendría a qué IPS pertenecer. En ese caso el evento igual se audita, el recurso original queda en `fhir_inbound_events` y el médico es notificado.
6. **Notifica y audita.** Publica `fhir_observacion` y registra `FHIR_EVENT_RECEIVED` con `origin: "FHIR"`, el id del paciente y el recurso en los metadatos.

Una observación con origen `fhir` no se vuelve a enviar a HAPI (la sincronización la rechaza): sería un duplicado.

## Médico responsable

`patients.responsible_clinician_id` (nullable). Se asigna solo al crear al paciente cuando quien lo crea es médico o especialista. Un administrador, o un médico o especialista de la IPS del paciente, lo cambia con `PUT /api/v1/patients/{id}/responsible-clinician` (el nuevo responsable debe ser un médico o especialista activo; si quien cambia no es admin, un colega de su misma IPS). Cada cambio guarda la versión anterior y deja `RESPONSIBLE_CHANGED` en la auditoría. La ficha del paciente expone `responsible_clinician_id` y `responsible_clinician_name`. Para armar el selector, `GET /api/v1/patients/{id}/eligible-clinicians` devuelve `[{id, nombre, rol, organizacion}]` con los médicos y especialistas activos de la IPS donde se atiende al paciente (mismos roles que el cambio: admin, o médico o especialista de esa IPS, que solo ve colegas de la suya).

## Pacientes eliminados y la bandeja

Las notificaciones de un paciente con borrado lógico **siguen en la tabla** (son historia) pero **salen de la bandeja** y del conteo de no leídas. Si el paciente se restaura, vuelven. Las notificaciones sin paciente no se ven afectadas.

Destinatarios cuando no hay responsable (o su cuenta está inactiva o bloqueada): los médicos y especialistas activos de la IPS del encuentro más reciente; si no hay ninguno, los administradores.

## Operación

### Variables de entorno del sistema reactivo

| Variable | Valor por defecto | Para qué sirve |
|---|---|---|
| `ALERT_ESCALATION_ENABLED` | `true` | Enciende el monitor de alertas. Las pruebas lo apagan. |
| `ALERT_ESCALATION_MINUTES` | `5` | Minutos que una alerta puede seguir `activa` antes de escalarse. Para una demostración, `1`. |
| `ALERT_MONITOR_INTERVAL_SECONDS` | `30` | Cada cuántos segundos corre el monitor. |
| `BED_CLEANING_ALERT_MINUTES` | `60` | Minutos en limpieza sin evidencia aprobada antes de la alerta de cama (mismo valor por defecto que `/beds/cleaning/overdue`). |
| `AI_AUTO_ANALYSIS` | `true` | Enciende el análisis de IA automático (reactor y respaldo del monitor). Las pruebas lo apagan. |
| `AI_AUTO_GRACE_SECONDS` | `120` | Segundos sin variables nuevas tras los cuales el monitor analiza un encuentro con signos vitales incompletos. |
| `AI_AUTO_LOOKBACK_MINUTES` | `60` | Ventana que mira el respaldo del monitor: no analiza encuentros con actividad más vieja. |
| `REQUIRE_REVIEW_NOTE` | `false` | Con `true`, aprobar o rechazar un reporte sin concepto clínico de al menos 10 caracteres responde 422. Ver `docs/IA.md`. |
| `LOG_LEVEL` | `INFO` | Nivel de los registros `app.*` en los logs del contenedor. El log de acceso de uvicorn no cambia. |

### Limpiar la bandeja de un contable tras una conciliación antigua

Las conciliaciones anteriores a este cambio avisaron cada factura por separado. Para marcar esos avisos como leídos (no se borran):

```sql
UPDATE notifications SET is_read = true
WHERE kind = 'factura_creada' AND is_read = false
  AND user_id IN (SELECT id FROM users WHERE username = 'contable.eps');
```

También se puede hacer desde la campana con «Marcar todas como leídas».
