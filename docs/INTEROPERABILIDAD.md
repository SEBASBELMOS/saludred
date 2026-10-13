# Interoperabilidad con HL7 FHIR R4

SaludRed publica y recibe información clínica en HL7 FHIR R4 a través de un servidor HAPI propio. Este documento resume qué hace cada mecanismo y en qué norma colombiana se apoya. La parte reactiva (la `Subscription`) está en [`EVENTOS.md`](EVENTOS.md); el mapeo de las tablas a recursos, en [`modelo-datos-y-fhir.md`](modelo-datos-y-fhir.md).

## R15 · Sincronización automática hacia HAPI

- Al **crear o editar un paciente**, y al crear un encuentro o una observación, la API empuja el recurso a HAPI **después de responder**, en una tarea de fondo (`BackgroundTasks`) con su propia sesión de base de datos. La escritura clínica nunca falla ni se demora por HAPI: si el servidor FHIR no responde, el error queda en `fhir_sync_log` (estado `ERROR`, con el mensaje) y en el log.
- La operación es una actualización condicional por identificador (`PUT [base]/Patient?identifier=…`), así que repetirla no duplica nada. La tarea de fondo reutiliza las dependencias ya sincronizadas (la EPS, el paciente, el encuentro) en lugar de reenviarlas en cada guardado.
- **El paciente se encuentra en HAPI por su identificador.** Lleva tres: `urn:saludred:identifier:documento` (`CC-1032456789`, la clave de sincronización), `urn:saludred:identifier:numero` (el número de documento solo) y `urn:saludred:identifier:patient-id` (el id del paciente en SaludRed). Así `GET [base]/Patient?identifier=1032456789`, `…=CC-1032456789` y `…=<id>` lo encuentran.
- `POST /api/v1/integration/fhir/retry-failed` (admin) vuelve a enviar los pacientes con sincronización fallida, pendiente o que nunca se intentó. Un fallo no detiene el lote. Los endpoints manuales (`POST /api/v1/integration/fhir/patients/{id}` y los demás) siguen disponibles.
- `FHIR_AUTO_SYNC` (por defecto `true`) lo apaga, por ejemplo en las pruebas.
- Todo recurso enviado lleva la etiqueta `meta.tag` `urn:saludred:identifier:origin|saludred`. Es lo que permite al receptor del rest-hook reconocer su propio eco.

## R21 · Reacción a datos nuevos

Ver [`EVENTOS.md`](EVENTOS.md): `Subscription` rest-hook, secreto compartido, idempotencia, supresión del eco y notificación al médico responsable. Resumen operativo: se publica una `Observation` en HAPI (Postman, un laboratorio, un dispositivo) y el médico recibe el aviso sin recargar.

## Exportar la historia clínica (FHIR)

`GET /api/v1/patients/{id}/fhir-bundle` devuelve un `Bundle` FHIR R4 de tipo `collection` (`application/fhir+json`) con:

- el `Patient`, sus `Encounter` y sus `Observation` (LOINC y UCUM), con los mismos identificadores de la sincronización;
- los reportes de IA **aprobados** como `RiskAssessment`.

Reglas de acceso iguales a las de la historia clínica (`GET …/clinical-record`): personal institucional dentro de su IPS, administrador y coordinador en toda la red, y el paciente solo la suya; el contable recibe 403. Cada exportación queda en la auditoría (`CLINICAL_RECORD_EXPORTED`, `format: "fhir-bundle"`).

**Por qué `RiskAssessment` y no `DiagnosticReport`.** Un reporte de IA es una predicción probabilística sobre el paciente, que es exactamente lo que modela `RiskAssessment` (`prediction.probabilityDecimal`, `prediction.qualitativeRisk`, `method`). `DiagnosticReport` transmite resultados de estudios diagnósticos (laboratorio, imágenes) con una conclusión; llamar así a un puntaje de un modelo exageraría su alcance. El clínico que firmó queda en `performer.display` y sus observaciones en `note`.

## Importar un Bundle (FHIR)

`POST /api/v1/integration/fhir/import` (admin y médico) recibe un `Bundle` de tipo `collection` o `document` con **un** `Patient` y al menos una `Observation`:

- Se valida el Bundle completo antes de escribir; cualquier problema responde 422 con la lista de errores y no se guarda nada. El `Patient` necesita el identificador del documento (`CC-123456`, sistema `…:documento`), nombre, sexo y fecha de nacimiento; cada `Observation` necesita código, valor y un `subject` que apunte a ese `Patient`. Los demás recursos (`Encounter`, `RiskAssessment`, …) se omiten y se cuentan en la respuesta.
- El paciente se busca por tipo y número de documento. Si existe se usa tal cual (no se sobrescriben sus datos); si no, se crea, afiliado a la EPS indicada en `eps_organization_id` o, para un médico, a la EPS de su IPS. El médico solo importa hacia su propia IPS y solo sobre pacientes que atiende; el administrador indica `organization_id`.
- Las observaciones se guardan con `origin = 'import'` en el encuentro abierto o más reciente de esa IPS; si no hay, se crea un encuentro ambulatorio finalizado de importación. Importar dos veces el mismo Bundle no duplica: una observación con el mismo paciente, código, momento y valor se cuenta como duplicada.
- Se audita con `FHIR_IMPORT` (origen `import`). Un Bundle exportado por SaludRed puede volver a importarse sin efecto.

## `ServiceRequest` para las remisiones (R08)

Cada remisión (tabla `referrals`) se envía a HAPI como `ServiceRequest` R4 con el mismo mecanismo en segundo plano de R15 (`fhir_autosync`): al crearla y en cada cambio de estado, sin bloquear la respuesta ni fallar si HAPI está caído. El mapeo (`mappers.referral_to_fhir`) es:

| `ServiceRequest` | Origen |
|---|---|
| `identifier` | `urn:saludred:identifier:referrals` \| id de la remisión (la actualización condicional por identificador evita duplicados) |
| `status` | `pendiente` y `aceptada` → `active`; `completada` → `completed`; `rechazada` → `revoked` |
| `intent` | `order` |
| `code` | SNOMED 3457005 «Patient referral», texto «Remision a especialista» |
| `subject` | Referencia FHIR del paciente (`Patient/{id en HAPI}`) |
| `requester` / `performer[0]` | Médico remitente y especialista: `display` con el nombre e `identifier` propio |
| `reasonCode.text` | Motivo de la remisión |
| `authoredOn` | Fecha de creación |
| `meta.tag` | Etiqueta de origen `urn:saludred:identifier:origin\|saludred` (igual que el resto de recursos que SaludRed envía) |

El id asignado por el servidor queda en `referrals.fhir_resource_id` y en `fhir_sync_log` (`entity_type = 'referrals'`). La `Subscription` rest-hook solo observa `Observation?`, así que un `ServiceRequest` nunca dispara el receptor. Ver `docs/EVENTOS.md`.

## Marco normativo

- **Ley 2015 de 2020** (31 de enero de 2020): crea la historia clínica electrónica interoperable (IHCE) en Colombia. Ordena que los prestadores pongan a disposición los datos, documentos y registros de la historia clínica en la plataforma de interoperabilidad que provea el Gobierno, y fija un plazo máximo de implementación de **cinco años** contados desde su vigencia. Fuente: texto de la ley en el Gestor Normativo de la Función Pública y en la Secretaría del Senado.
- **Resolución 1888 de 2025** del Ministerio de Salud y Protección Social (15 de septiembre de 2025): "por la cual se adopta el Resumen Digital de Atención en Salud (RDA), en el marco de la Interoperabilidad de la Historia Clínica Electrónica (IHCE), se establecen los mecanismos para su implementación a nivel nacional y se dictan otras disposiciones". Su artículo 6 da a los actores un plazo máximo de seis meses, contados desde el 15 de octubre de 2025, para integrar e interoperar sus sistemas con el modelo definido. Fuente: Compilación Jurídica de la Superintendencia Nacional de Salud (normograma).
- **Precisión sobre FHIR.** La resolución adopta el **RDA** dentro de la IHCE; el texto de la resolución, tal como lo compila el normograma de la Superintendencia, no menciona "HL7 FHIR" ni "R4". El estándar HL7 FHIR R4 viene de la **guía de implementación técnica del RDA** que publica el Ministerio (portal `vulcano.ihcecol.gov.co`), y así lo describen las fuentes secundarias consultadas. Por eso este documento no atribuye a la resolución un artículo que "adopte FHIR R4": dice que el RDA se implementa sobre HL7 FHIR R4 según su guía técnica. Esta verificación se hizo con fuentes en línea y no reemplaza la lectura del texto oficial.
- Otras normas del mapa de requisitos (Ley 1581 de 2012, Res. 866 de 2021, Res. 1995 de 1999, Res. 3100 de 2019) se tratan en la documentación de seguridad y de IA.
