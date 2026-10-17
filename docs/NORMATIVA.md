# Normativa: qué norma responde a qué control técnico

Este documento relaciona cada norma colombiana relevante con el control técnico de SaludRed que la atiende y con la evidencia verificable (archivo, endpoint y prueba). Responde al requisito R22.

Las citas indican a qué se orienta cada control y no constituyen una opinión jurídica; la verificación de cumplimiento le corresponde al responsable del tratamiento y al prestador.

## Cómo se verificaron las citas

- **Ley 1581 de 2012:** se contrastaron los artículos 4 (literales b, f, g y h), 5, 6, 8, 9, 17 y 18 con el texto publicado en el Sistema Único de Información Jurídica de la Alcaldía de Bogotá (`alcaldiabogota.gov.co/sisjur`, norma 49981).
- **Ley 2015 de 2020:** el artículo 1 (objeto) y el artículo 3, parágrafo 1 (plazo máximo de implementación de cinco años) se contrastaron con las fuentes oficiales consultadas (Gestor Normativo de la Función Pública y Secretaría del Senado).
- **Resolución 1888 de 2025:** se usa la redacción ya verificada en [`INTEROPERABILIDAD.md`](INTEROPERABILIDAD.md): adopta el Resumen Digital de Atención (RDA) dentro de la IHCE; el estándar HL7 FHIR R4 proviene de la guía de implementación técnica del RDA, no del texto de la resolución.
- **Resolución 1995 de 1999:** solo se cita el artículo 3 (características de la historia clínica). La numeración de los artículos sobre custodia y conservación no se pudo verificar en el texto oficial, así que esas obligaciones se citan sin número de artículo.
- **Resolución 866 de 2021, Resolución 3100 de 2019, Decreto 1377 de 2013 y Ley 23 de 1981:** se citan sin número de artículo, por no haberlo verificado en el texto oficial.

## Tabla de correspondencia

Las rutas de archivo son relativas a la raíz del repositorio. Las pruebas están en `tests/` y se nombran como `archivo::prueba`.

### Ley 1581 de 2012 (protección de datos personales)

| Artículo o principio | Control técnico en SaludRed | Evidencia |
|---|---|---|
| Art. 4, literal b (finalidad) | La capa de seudonimización arma el payload de la IA campo por campo desde una lista de permitidos: solo variables clínicas, edad en años enteros, sexo y un seudónimo HMAC. El servicio `ml` rechaza con 422 cualquier campo fuera de su contrato (`extra="forbid"`). MongoDB guarda solo `paciente_id`, nunca nombre ni documento. | `app/services/pseudonymization.py`; `ml/service/`; `GET /api/v1/ai-reports/{id}/payload`; `tests/test_ai_reports.py::test_no_canary_value_reaches_the_stored_payload`, `::test_payload_content_and_pseudonym`, `::test_pseudonym_is_stable_opaque_and_keyed`; `tests/test_billing.py::test_mongo_stores_no_personal_data`. Detalle en [`IA.md`](IA.md) y [`CONTABILIDAD.md`](CONTABILIDAD.md). |
| Art. 4, literal f (acceso y circulación restringida) | Autorización en el servidor por rol, por institución y por pertenencia del registro. Lo ajeno responde 404 o 403. Los eventos clínicos nunca llegan a `paciente` ni a `contable`, por dos controles independientes. El contable no ve nada clínico. | `app/core/authz.py`; `app/services/patient_access.py`; `app/events/reactors.py`, `app/events/channel.py`; `tests/test_authz.py` (todas); `tests/test_roles_contract.py::test_accountant_cannot_reach_clinical_or_network_data`; `tests/test_events_ws.py::test_ws_never_delivers_clinical_events_to_patient_or_accountant`; `tests/test_notifications.py::test_only_the_owner_can_mark_as_read` |
| Art. 4, literal g (seguridad) | Contraseñas con bcrypt, JWT firmado con expiración, bloqueo de cuenta tras 3 intentos, cabeceras de seguridad, CORS cerrado por defecto, límite de tasa en nginx, `ml`, `mongo` y Orthanc sin puertos públicos, sin secretos en el repositorio. Detalle en las filas de la sección «Controles transversales». | `app/core/security.py`; `app/services/auth.py`; `app/main.py` (`configure_http_security`); `frontend/nginx.conf`; `docker-compose.yml`; `tests/test_security.py`; `tests/test_lockout.py`; `tests/test_http_security.py` |
| Art. 4, literal h (confidencialidad) | Las rutas de datos exigen token; una cuenta bloqueada o deshabilitada se rechaza en cada petición, aun con un token emitido antes del bloqueo. | `tests/test_http_security.py::test_every_api_route_requires_a_token`; `tests/test_reactive_flows.py::test_a_locked_or_disabled_account_is_rejected_on_every_request`; `tests/test_events_ws.py::test_ws_refuses_a_locked_account` |
| Art. 5 y 6 (datos sensibles; los datos de salud lo son) | Los datos de salud solo se exponen a roles clínicos del alcance correspondiente; hacia la IA y hacia Mongo se minimizan. El aviso de privacidad aparece en la pantalla de inicio de sesión. **Limitación:** aún no se registra la autorización expresa del titular con fecha y versión del aviso (ver «Limitaciones conocidas»). | `frontend/src/features/auth/LoginPage.tsx` (aviso de la Ley 1581); controles de las filas anteriores |
| Art. 8 (derechos del titular: acceso) | El `paciente` consulta solo su propia información y descarga su historia clínica; una petición sobre datos de otro paciente responde 403 o 404. | `GET /api/v1/me/patient`, `GET /api/v1/me/encounters`, `GET /api/v1/me/observations`, `GET /api/v1/patients/{id}/clinical-record.pdf`; `tests/test_ai_reports.py::test_a_patient_downloads_only_their_own_record` |
| Art. 17 y 18 (deberes del responsable y del encargado: seguridad, trazabilidad) | Auditoría de las acciones relevantes con usuario, acción, recurso y fecha; la contraseña nunca llega a la auditoría. | `app/services/trail.py`; `GET /api/v1/audit-logs` (solo admin); `tests/test_patient_workflows.py::test_the_password_never_reaches_the_audit_log`; `tests/test_lockout.py::test_every_outcome_leaves_an_audit_entry` |

### Decreto 1377 de 2013

Reglamenta parcialmente la Ley 1581 (autorización y aviso de privacidad) y hoy está compilado en el Decreto 1074 de 2015. Aplica en lo siguiente:

| Principio | Control técnico | Evidencia |
|---|---|---|
| Aviso de privacidad al recolectar datos | Aviso en la pantalla de inicio de sesión. | `frontend/src/features/auth/LoginPage.tsx` |
| Prueba de la autorización del titular | **No implementado:** no se guarda la autorización con fecha y versión del aviso. Queda como siguiente paso. | Sección «Limitaciones conocidas» |

### Ley 2015 de 2020 (historia clínica electrónica interoperable)

| Artículo | Control técnico | Evidencia |
|---|---|---|
| Art. 1 (objeto: interoperabilidad de la historia clínica electrónica, IHCE) y art. 3, parágrafo 1 (plazo máximo de implementación de cinco años) | La información clínica se representa en HL7 FHIR R4 sobre un servidor HAPI propio. Sincronización automática e idempotente (actualización condicional por identificador), `Subscription` rest-hook para datos que llegan desde fuera, exportación e importación de la historia como `Bundle`, y `ServiceRequest` para las remisiones. | `app/fhir/mappers.py`, `app/fhir/bundle.py`; `app/services/fhir_autosync.py`, `app/services/fhir_exchange.py`, `app/services/fhir_inbound.py`, `app/services/fhir_subscription.py`; `GET /api/v1/patients/{id}/fhir-bundle`; `POST /api/v1/integration/fhir/import`; `POST /api/v1/integration/fhir/subscription`; `PUT /api/v1/fhir/hooks/Observation/{id}`; `scripts/ensure_fhir_subscription.py`; `tests/test_fhir_autosync.py`, `tests/test_fhir_hook.py`, `tests/test_fhir_exchange.py`, `tests/test_mappers.py`, `tests/test_referrals.py::test_every_referral_state_is_synced_as_a_servicerequest`. Detalle en [`INTEROPERABILIDAD.md`](INTEROPERABILIDAD.md). |

### Resolución 866 de 2021 (elementos de datos clínicos relevantes para la interoperabilidad)

| Principio | Control técnico | Evidencia |
|---|---|---|
| Conjunto de datos clínicos relevantes: identificación del paciente, atención, mediciones y resultados | `Patient` (con identificadores de documento), `Encounter`, `Observation` con códigos LOINC y unidades UCUM validadas contra un catálogo con rangos fisiológicos plausibles, `Organization` y `Location`. **Pendiente:** mapear campo por campo contra el anexo técnico; los estudios de imagen aún no se envían a FHIR. | `app/models/loinc.py`; `app/fhir/mappers.py`; `tests/test_mappers.py`; `tests/test_patient_workflows.py::test_the_server_sets_name_and_unit_from_the_catalogue`, `::test_an_implausible_vital_sign_is_rejected`. Ver [`modelo-datos-y-fhir.md`](modelo-datos-y-fhir.md). |

### Resolución 1888 de 2025 (Resumen Digital de Atención dentro de la IHCE)

| Principio | Control técnico | Evidencia |
|---|---|---|
| Adopta el RDA en el marco de la IHCE; la implementación sobre HL7 FHIR R4 se define en la guía técnica del RDA | FHIR R4 en uso; la historia clínica se exporta como `Bundle` de tipo `collection` con `Patient`, `Encounter`, `Observation` y `RiskAssessment` (reportes de IA aprobados). **No** se afirma conformidad con el perfil del RDA: es un prototipo académico. | `GET /api/v1/patients/{id}/fhir-bundle`; `tests/test_fhir_exchange.py::test_bundle_structure`, `::test_import_round_trip_of_our_own_export_is_idempotent`. Citado con la redacción de [`INTEROPERABILIDAD.md`](INTEROPERABILIDAD.md). |

### Resolución 1995 de 1999 (historia clínica)

| Principio | Control técnico | Evidencia |
|---|---|---|
| Art. 3 (características: integralidad y secuencialidad, entre otras) | Las atenciones y mediciones quedan en orden cronológico y ligadas al paciente; el reporte de IA solo entra a la historia tras la firma de un clínico. | `app/services/clinical_record.py`; `tests/test_ai_reports.py::test_full_flow_one_approved_one_rejected` |
| Carácter reservado de la historia clínica | Acceso por rol e institución; cada consulta de la historia en JSON y cada descarga del PDF o del `Bundle` quedan auditadas (`RECORD_VIEWED`, `CLINICAL_RECORD_EXPORTED`). | `GET /api/v1/patients/{id}/clinical-record`; `tests/test_ai_reports.py::test_every_step_is_audited`; `tests/test_fhir_exchange.py::test_export_access_rules_and_audit` |
| Conservación y no destrucción del registro (sin número de artículo verificado) | Borrado lógico: `deleted_at` es la única fuente de verdad, los datos no se destruyen. La edición conserva la versión anterior (`record_versions`). Solo el `admin` restaura. Un reporte de IA rechazado no se borra y no existe `DELETE`. Las facturas nunca se borran: cambian de estado con historial. | `app/services/soft_ops.py`; `DELETE`/`POST .../restore`/`GET .../history` por entidad; `scripts/smoke_api.py` (edición con historial, borrado lógico y restauración); `tests/test_authz.py::test_author_may_touch_own_record`, `::test_non_author_is_denied`, `::test_orphan_record_is_admin_only`; `tests/test_ai_reports.py::test_report_evidence_cannot_be_rewritten` |
| Firma del profesional | El reporte de IA queda `pendiente` hasta que un `medico` o `especialista` lo aprueba o rechaza; su identidad y la fecha quedan en el reporte y en el PDF. | `POST /api/v1/ai-reports/{id}/approve`, `.../reject`; `tests/test_ai_reports.py::test_full_flow_one_approved_one_rejected`, `::test_patient_and_accountant_get_403_everywhere`, `::test_admin_and_coordinator_read_but_never_sign`, `::test_rejecting_requires_a_reason` |

### Resolución 3100 de 2019 (habilitación de servicios de salud)

| Principio | Control técnico | Evidencia |
|---|---|---|
| Procesos prioritarios, entre ellos la limpieza y desinfección, y el registro de su ejecución (sin número de numeral verificado) | Módulo de limpieza de camas: lista de verificación de 11 superficies (basada en las guías del CDC), aislamiento, desinfectante, cortinas, foto de evidencia y **revisión humana**: quien registra no aprueba. Con `REQUIRE_CLEANING_EVIDENCE=true`, una cama no vuelve a estar disponible sin una limpieza aprobada. Alerta de cama libre sin limpiar. | `app/services/cleaning.py`; `POST /api/v1/beds/{bed_id}/cleaning`, `PUT /api/v1/cleaning/{id}/photo`, `POST /api/v1/cleaning/{id}/review`, `GET /api/v1/beds/cleaning/overdue`; `tests/test_bed_cleaning.py` (por ejemplo `::test_an_incomplete_checklist_is_rejected`, `::test_the_author_cannot_approve_their_own_record`, `::test_the_gate_blocks_availability_until_a_record_is_approved`); `tests/test_alerts.py::test_a_bed_alert_is_raised_once_the_cleaning_is_overdue` |
| Inventario y estado de camas | Vista SQL `v_inventario_camas` y una sola asignación activa por cama, garantizada por la base de datos (índice único parcial). | migración `migrations/versions/c7d2a91e5b48_bed_inventory_view_and_criticality.py`; `tests/test_queue_criticality.py`; `tests/test_bed_rules.py` |

### Ley 23 de 1981 (ética médica)

| Principio | Control técnico | Evidencia |
|---|---|---|
| Secreto profesional | Se materializa con los controles de acceso y reserva descritos arriba: un clínico solo accede a los pacientes de su alcance (su IPS, o una remisión vigente), y el acceso por remisión se pierde si la remisión es rechazada. | `app/services/patient_access.py`; `tests/test_referrals.py::test_a_rejected_referral_grants_nothing`, `::test_a_referral_opens_the_whole_record_to_the_specialist` |

## Controles transversales

| Control | Qué hace | Evidencia |
|---|---|---|
| Autenticación JWT y roles | Seis roles: `admin`, `coordinador`, `medico`, `especialista`, `contable`, `paciente`. Solo el admin crea cuentas. `GET /api/v1/auth/me` devuelve `rol`. | `app/core/security.py`, `app/core/authz.py`; `POST /api/v1/auth/login`, `GET /api/v1/auth/me`; `tests/test_security.py`; `tests/test_roles_contract.py` |
| Bloqueo por intentos fallidos | Tres fallos consecutivos bloquean la cuenta (423); un cuarto intento con la clave correcta se rechaza; solo el admin desbloquea, con motivo; el token anterior deja de servir; el bloqueo avisa al admin en vivo. | `app/services/auth.py`, `app/api/routes/accounts.py` (`POST /api/v1/admin/users/{id}/unlock`); `tests/test_lockout.py::test_the_third_failure_locks_the_account`, `::test_a_locked_account_is_rejected_even_with_the_right_password`, `::test_only_an_unlock_brings_the_account_back`; `tests/test_reactive_flows.py::test_a_locked_account_alerts_the_admins_with_the_username`, `::test_the_admin_unlock_restores_access_for_the_old_token` |
| Auditoría | Registro de usuario, acción, recurso y fecha para login fallido, bloqueo, desbloqueo, análisis de IA, aprobación, rechazo, remisión, factura, alerta, evento FHIR, evidencia de limpieza, borrado lógico y restauración. Solo el admin la consulta. | `GET /api/v1/audit-logs`; `app/services/trail.py`; `tests/test_roles_contract.py::test_the_operator_cannot_read_audit_logs`, `::test_audit_action_has_the_new_values`; `tests/test_billing_sql.py::test_the_latest_migration_covers_every_audit_action` |
| Borrado lógico con restauración solo por admin | `DELETE` marca `deleted_at`; `POST .../restore` es exclusivo del admin. | `app/services/soft_ops.py`; `tests/test_authz.py` (autoría y admin); `scripts/smoke_api.py` |
| Seudonimización hacia la IA (R12) | Ver Ley 1581, literal b. El payload exacto enviado queda guardado y consultable. | `app/services/pseudonymization.py`; `GET /api/v1/ai-reports/{id}/payload`; `tests/test_ai_reports.py::test_no_canary_value_reaches_the_stored_payload` |
| Reporte de IA con firma clínica (R10) | La IA propone; el reporte nace `pendiente` y solo un `medico` o `especialista` lo aprueba o rechaza. | `app/services/ai_reports.py`; `tests/test_ai_reports.py::test_full_flow_one_approved_one_rejected`, `::test_a_report_is_reviewed_once` |
| Historia clínica en PDF con solo reportes aprobados (R11) | El PDF y el JSON de la historia incluyen únicamente reportes aprobados. | `GET /api/v1/patients/{id}/clinical-record.pdf`; `tests/test_ai_reports.py::test_full_flow_one_approved_one_rejected` |
| FHIR R4: sincronización, `Subscription`, `Bundle`, `ServiceRequest` | Ver Ley 2015. Eco suprimido e idempotencia por `(id, versionId)`. | `tests/test_fhir_hook.py::test_echo_of_our_own_push_is_ignored`, `::test_same_id_and_version_twice_is_processed_once`, `::test_hook_rejects_missing_or_wrong_secret` |
| Facturación en MongoDB sin datos personales | Documentos con identificadores opacos; idempotencia por índices únicos parciales; recibo en PDF resuelve el nombre desde PostgreSQL al generarlo. | `app/billing/`; `tests/test_billing.py::test_mongo_stores_no_personal_data`, `::test_indexes_are_idempotent_unique_and_partial`, `::test_other_roles_get_403_everywhere` |
| Evidencia de limpieza (lista de verificación del CDC, foto y revisión humana) | Ver Resolución 3100. | `tests/test_bed_cleaning.py` |
| Límite de tasa | nginx: 20 peticiones por segundo por cliente (ráfaga de 40) en la API y 10 por minuto (ráfaga de 5) en el login; el exceso recibe 429. El cliente se identifica con `CF-Connecting-IP` detrás del túnel. | `frontend/nginx.conf` (`limit_req_zone`); verificado en vivo por el túnel web; no hay prueba unitaria porque nginx no corre en `pytest` |
| Cabeceras de seguridad y CORS cerrado | La API envía `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` y `Referrer-Policy`; nginx añade una `Content-Security-Policy`. CORS solo se activa con orígenes explícitos en `CORS_ALLOWED_ORIGINS`. | `app/main.py`; `frontend/nginx.conf`; `tests/test_http_security.py::test_security_headers_on_api_responses`, `::test_cors_is_off_by_default`, `::test_the_real_app_does_not_reflect_an_unknown_origin` |
| Sin `.env` en el repositorio | `.env` y `.env.*` están excluidos; solo se versiona `.env.example`, sin valores reales. Los secretos obligatorios (`JWT_SECRET_KEY`, `PSEUDONYM_KEY`, `FHIR_HOOK_SECRET`, `MONGO_ROOT_PASSWORD`) no tienen valor por defecto: sin ellos la API o `docker compose` no arrancan. | `.gitignore`; `.env.example`; `app/core/config.py`; `tests/test_deploy_constraints.py::test_hook_secret_is_required_and_long_enough`; `tests/test_ml_client.py::test_the_pseudonym_key_is_required_and_long_enough` |

## Limitaciones conocidas

- **Autorización del titular:** no se registra la autorización expresa del paciente con fecha y versión del aviso de privacidad (Ley 1581 y Decreto 1377). Es el siguiente paso en cumplimiento.
- **Cifrado a nivel de campo:** queda fuera del alcance del Corte 2 (`REQUISITOS.md`, sección 9). La protección descansa en control de acceso, TLS del túnel, bases gestionadas y minimización de datos.
- **Modelo de criticidad modesto:** AUC de 0,623 con 192 visitas reales de un solo hospital de Estados Unidos; en el subgrupo de 45 a 64 años el AUC baja a 0,523. Por eso es un apoyo que un clínico firma, no un diagnóstico. Detalle en [`IA.md`](IA.md).
- **Modelo de limpieza entrenado con datos simulados:** sus métricas describen la simulación, no hospitales reales.
- **Túneles rápidos de Cloudflare:** no entregan Server-Sent Events; el canal público es WebSocket y el SSE queda para la red local. Las URLs cambian en cada reinicio. Ver [`EVENTOS.md`](EVENTOS.md).
- **Latencia de HAPI:** HAPI 7.4 entrega las suscripciones con una tarea de 5 s, así que un dato externo tarda entre 5 y 9 s en llegar al médico.
- **Interoperabilidad parcial:** no hay mapeo campo por campo contra la Resolución 866 ni perfil del RDA de la Resolución 1888; los estudios de imagen no se envían a FHIR.
- **Plan de seguridad escrito:** el prototipo implementa controles técnicos, pero no incluye un plan de seguridad y privacidad formal.
- **Fuera de alcance por diseño:** la Ley 1266 de 2008 (hábeas data financiero) no aplica. El Decreto 4725 de 2005 (dispositivos médicos) podría tocar a un software de apoyo a decisiones; la mitigación es que la IA solo recomienda y un humano aprueba y firma.
