# Interoperabilidad entre equipos (Parte B)

SaludRed intercambia dos mensajes FHIR R4 con otros equipos, **API a API**: un resultado de laboratorio (`Observation`) y una remisión (`ServiceRequest`). Es emisor y receptor. Este documento reúne rutas, alcances, códigos, cómo registrar un socio, cómo correr el tester, el identificador federado, los códigos del perfil y el mapa normativo. Lo que HAPI sincroniza hacia el servidor propio (`Bundle`, `Subscription` local) está en [`INTEROPERABILIDAD.md`](INTEROPERABILIDAD.md); la matriz de brechas y las decisiones, en [`ENTREGA_FINAL.md`](ENTREGA_FINAL.md) (filas 3.1 a 3.15).

Fuentes: diapositivas 23 a 34 de la semana 14 y la clase del 10/11 (líneas 792 a 1418 de la transcripción). Lo que las fuentes no dan va marcado **por confirmar**.

## Un mensaje, cinco controles

El orden importa: no tiene sentido validar el contenido de un mensaje cuya procedencia no se comprobó. El cuerpo se lee solo después del token y se interpreta solo después de la firma.

```
Equipo 1                                             Equipo 3 (SaludRed)
   | POST /oauth/token  client_id, client_secret, scope  |
   |---------------------------------------------------->|  1. quién es (cliente registrado)
   |<------------- JWT de 10 min (scope concedido) ------|
   | POST /interop/Observation  Bearer + firma HMAC      |
   |---------------------------------------------------->|  1. token   -> 401 / 403
   |                                                     |  2. firma   -> 401 / 409
   |                                                     |  3. perfil  -> 422 + OperationOutcome
   |                                                     |  4. paciente-> 201 / 202
   |<------------------- 201 / 202 / 4xx ----------------|  5. reacción
```

## Rutas

Las rutas de los socios están en la **raíz**, como en la diapositiva 28 (decisión D5), no bajo `/api/v1`. Entran por `tunnel-api` (directo a `api:8000`); el `nginx` del contenedor `web` no las reenvía. No usan el JWT de usuario.

| Ruta | Quién | Para qué |
|---|---|---|
| `POST /oauth/token` | socio | Credenciales de cliente (`client_id`, `client_secret`, `scope`) → JWT de 10 minutos |
| `POST /interop/Observation` | socio | Recibir un resultado de laboratorio. Alcance `system/Observation.write` |
| `POST /interop/ServiceRequest` | socio | Recibir una remisión. Alcance `system/ServiceRequest.write` |
| `GET /api/v1/interop/pendientes` | admin | Cola de conciliación (los 202) |
| `POST /api/v1/interop/pendientes/{id}/conciliar` | admin | Asociar un pendiente a un paciente (y, si falta, a un especialista): el 202 pasa a 201 |
| `GET /api/v1/interop/clientes` | admin | Socios registrados (nunca muestra secretos) |

Límite de tasa: 10 peticiones por 10 s por IP, con un contador propio para `/oauth/token` y otro para `/interop/*` y `/api/v1/interop/*` (Fase 2, D4). Al pasarse, 429 con `Retry-After`; el tester espera y reintenta.

## Autenticación de sistema (OAuth 2.0, client credentials)

El JWT de la parte A identifica personas; este identifica **sistemas**. SaludRed registra al socio y le entrega, por un canal privado, un `client_id`, un `client_secret` y, aparte, un **secreto de firma**.

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded     (también JSON, o HTTP Basic para el cliente)

grant_type=client_credentials&client_id=equipo1&client_secret=...&scope=system/Observation.write system/ServiceRequest.write
```

Respuesta: `{"access_token": "...", "token_type": "Bearer", "expires_in": 600, "scope": "..."}`.

- **Solo se concede lo registrado.** `scope` = lo pedido ∩ lo registrado (sin `scope`, todo lo registrado). Si lo pedido no tiene ninguna intersección: `400 invalid_scope`. Un cliente registrado solo con `Observation.write` que pide también `ServiceRequest.write` recibe un token solo con el primero, y al enviar una remisión obtiene 403.
- El token es un JWT HS256 con `sub` = `client_id`, `scope`, `iss` = `JWT_ISSUER` y **`aud` = `saludred-interop`**. El token de usuario lleva `aud` = `saludred-api`: cada decodificador rechaza el del otro (se prueba en los dos sentidos).
- Se vuelve a leer el cliente de la base en cada petición: desactivar un cliente o quitarle un alcance surte efecto al instante, aunque el token no haya vencido.
- Notación SMART on FHIR v1 (`system/Observation.write`; en v2 sería `.c`). En producción, SMART Backend Services reemplazaría el secreto por una firma con llave privada.
- Errores del endpoint: `401 invalid_client` (cliente desconocido y secreto errado son idénticos y tardan lo mismo), `400 unsupported_grant_type`, `400 invalid_scope`. Todo fallo queda auditado.

## Firma por mensaje (HMAC-SHA256)

```
firma = HMAC-SHA256(secreto_de_firma, fecha + "." + nonce + "." + cuerpo)
```

| Cabecera | Contenido |
|---|---|
| `X-Interop-Fecha` | segundos Unix |
| `X-Interop-Nonce` | valor aleatorio de un solo uso (8 a 128 caracteres `A-Za-z0-9._-`) |
| `X-Interop-Firma` | `v1=<64 hexadecimales>` |

- Se firman los **bytes crudos** del cuerpo, no un JSON re-serializado.
- El **cuerpo** dentro de la firma impide cambiar un 182 por un 82; la **fecha** (tolerancia `INTEROP_SIGNATURE_TOLERANCE_SECONDS`, 300 s) impide reenviar un mensaje viejo; el **nonce** impide repetir uno reciente dentro de esa ventana (la fecha sola no basta). Se compara con `hmac.compare_digest`.
- El nonce es único por cliente (tabla `interop_nonces`, `UNIQUE (client_id, nonce)`, atómico) y se purga lo anterior a dos ventanas. Un nonce se consume aunque el mensaje falle después en el perfil: para corregir y reenviar hay que usar uno nuevo.
- El secreto de firma es **distinto** del `client_secret` (diap. 33): que se filtre uno no entrega el otro.
- Cuando avisa HAPI por `Subscription` no hay firma por mensaje: token fijo y el receptor vuelve a consultar el recurso (diap. 29). Ver «Aviso sobre `Subscription`».

## Respuestas del receptor

| Código | Significa | Quién lo da |
|---|---|---|
| 201 | Aceptado y asociado a un paciente | paciente resuelto |
| 202 | Aceptado; falta conciliar al paciente (o, en una remisión, no hay especialista que la reciba). **No se pierde**: queda en la cola y se avisa al admin | paciente / especialista |
| 401 | Sin token, token inválido o vencido; firma inválida; fecha fuera de la ventana; cabeceras de firma ausentes | token / firma |
| 403 | Token válido, sin el alcance de la ruta | token |
| 409 | Mensaje repetido (nonce ya visto) o recurso ya recibido (mismo `id` y versión del mismo cliente) | firma / paciente |
| 413 | Cuerpo de más de 262 144 bytes (tras el token) | token |
| 422 | No cumple el perfil: llega un `OperationOutcome` con `severity: error`, `code`, `diagnostics` y `expression` | perfil |
| 503 | `FEDERATED_SECRET` no está configurado: el receptor no puede asociar el paciente (o el secreto de firma del cliente no se puede leer) | paciente |

Todas las respuestas de `/interop/*` (también las de éxito) son un `OperationOutcome` (`application/fhir+json`). Un 202 es una decisión clínica: un resultado de un paciente que no encontramos no se descarta, porque perderlo es peor que revisarlo (diap. 30).

## Identificador federado del paciente

`Patient/17` solo existe en una base; enviar el documento pone un identificador directo en cada mensaje. En su lugar, todos los equipos del acuerdo derivan el mismo valor opaco:

```
mensaje      = "<TIPO>|<número normalizado>"      TIPO en {CC, TI, CE, PA}, en mayúsculas
normalizado  = NFKC(número) sin espacios en los extremos, sin ".", "-" ni " "
id_federado  = HMAC-SHA256(FEDERATED_SECRET, mensaje).hexdigest()[:32]
```

`1.144.123.456` y `1144123456` dan el mismo valor. Viaja en `subject.identifier` con el URI de sistema compartido `FEDERATED_SYSTEM`:

```json
"subject": {"identifier": {"system": "<FEDERATED_SYSTEM>", "value": "fa0d2121aa10d0c2bb7a5acd8b596f2f"}}
```

- **Valor de referencia verificado.** Con el secreto de demostración de la diapositiva 26 (`secreto-federado-del-curso-solo-demo`, público en el HTML) y `CC|1144123456` se obtiene exactamente `fa0d2121aa10d0c2bb7a5acd8b596f2f`, el valor que las notas atribuyen al secreto del cuaderno (comprobado en la ASUS; lo fija `tests/test_interop_federado_perfil.py`). El secreto real del acuerdo lo entrega el docente (diap. 33) y **no** se versiona.
- **Los secretos vienen del entorno.** `FEDERATED_SECRET` es un secreto. Sin él, el receptor responde 503 y el emisor se niega a enviar (no hay forma de calcular el identificador). `FEDERATED_SYSTEM` es un URI **provisional** (`urn:uao:salud-digital:paciente-federado`): las fuentes no lo dan (la diapositiva 27 solo muestra `SID_FEDERADO`); hay que reemplazarlo por el que anuncie el docente, igual en todos los equipos.
- **Cómo resuelve el receptor al paciente.** `patients.id_federado` (nulo permitido, indexado) se calcula al crear o editar un paciente mientras `FEDERATED_SECRET` esté definido (con el documento en claro del modelo, aunque en la base esté cifrado). Las filas que ya existían tienen `NULL`; para esas, el receptor calcula el identificador en Python sobre los pacientes con `id_federado` nulo (como máximo 5 000; con unos 400 pacientes es instantáneo) y compara en tiempo constante. Es un recorrido de la tabla por cada mensaje cuyo paciente no está en la columna: **el relleno de las filas existentes** (extender `scripts/cifrar_pacientes.py` o un script hermano, con ensayo en seco por defecto) **necesita la aprobación de D1** y no se ha hecho. Dos pacientes con el mismo identificador se consideran ambiguos y no se adivina.
- **Rotar `FEDERATED_SECRET`.** El identificador federado depende del secreto, y `patients.id_federado` guarda el valor calculado con el secreto anterior: si se cambia el secreto sin más, las filas ya rellenadas dejan de coincidir y el receptor responde 202 a pacientes que sí existen. Orden correcto, con respaldo previo: (1) poner `id_federado = NULL` en todos los pacientes (`UPDATE patients SET id_federado = NULL`; es una reescritura de datos, por lo que va con la aprobación de D1); (2) cambiar `FEDERATED_SECRET` en el `.env` de la ASUS y reiniciar solo `api` (mientras `id_federado` es nulo el receptor calcula el identificador en Python, así que no se pierde ningún mensaje); (3) rellenar de nuevo con el script de relleno (D1). Los socios deben recibir el secreto nuevo **antes** de que se enciendan los envíos, o sus mensajes irán a la cola de conciliación.
- **Límites y cuerpos (revisión independiente).** El límite de 10 peticiones por 10 s ya no es por dirección sola: `/oauth/token` cuenta por dirección + `client_id`, `/interop/*` por el `sub` del token de sistema (verificado; sin token válido, por dirección) y el inicio de sesión por dirección + usuario, con un tope de 10 veces por dirección contra la rotación de identidades. `/oauth/token` rechaza con 413 un cuerpo de más de 16 KB (por `Content-Length` antes de leerlo y por conteo si no lo hay); `/interop/*` corta la lectura en 256 KB. Los rechazos sin autenticar (sin token, token inválido, `invalid_client`) dejan **una** fila `INTEROP_REJECTED` por (IP, motivo) cada 60 s; el resto solo va al log `app.security`. El `client_id` se recorta a 80 caracteres en la auditoría. Una Observation `cancelled` o `entered-in-error` se guarda y se audita, pero no genera alerta crítica ni notificación.
- Límite (diap. 26): quien tenga el secreto y una lista de documentos puede recalcular los identificadores. El secreto se guarda como cualquier otro y se rota si se filtra.

## Perfil común (códigos que todos validan)

Conectar no es entenderse: el perfil fija el vocabulario. Todo fallo produce un 422 con un `OperationOutcome` (`app/services/interop/perfil.py`).

| Sistema | Código | Qué es | Unidad UCUM y rango plausible |
|---|---|---|---|
| LOINC `http://loinc.org` | `2339-0` | Glucosa | `mg/dL`, 20 a 800 (rango del catálogo clínico) |
| LOINC | `4548-4` | HbA1c | `%`, 3 a 20 (**por confirmar**, no está en las fuentes) |
| LOINC | `39156-5` | IMC | `kg/m2`, 10 a 80 (**por confirmar**) |
| LOINC | `8480-6` | Presión sistólica | `mm[Hg]`, 50 a 260 (catálogo) |
| LOINC | `8462-4` | Presión diastólica | `mm[Hg]`, 30 a 150 (catálogo) |
| SNOMED CT `http://snomed.info/sct` | `44054006` | Diabetes tipo 2 | (motivo de remisión) |
| SNOMED CT | `4855003` | Retinopatía diabética | (motivo de remisión) |
| SNOMED CT | `38341003` | Hipertensión | (motivo de remisión) |
| SNOMED CT | `3457005` | Remisión | `ServiceRequest.code`, obligatorio |

UCUM: `http://unitsofmeasure.org` en `valueQuantity.system`, y `valueQuantity.code` igual a la unidad del perfil.

Qué rechaza (422): sistema LOINC mal escrito (`http://loinc.com`), unidad distinta de la del perfil, valor fuera del rango plausible (un error de digitación, no un paciente grave), código fuera del perfil, `ServiceRequest` sin SNOMED `3457005`, motivo de remisión ausente, `subject.identifier` ausente, con otro sistema o que no sea un identificador federado (32 hexadecimales en minúscula), y `status`/`intent` ausentes o inválidos.

Ejemplo de `Observation` válida:

```json
{"resourceType": "Observation", "id": "lab-1", "status": "final",
 "code": {"coding": [{"system": "http://loinc.org", "code": "2339-0"}]},
 "subject": {"identifier": {"system": "<FEDERATED_SYSTEM>", "value": "fa0d2121aa10d0c2bb7a5acd8b596f2f"}},
 "effectiveDateTime": "2026-11-17T10:00:00+00:00",
 "valueQuantity": {"value": 182, "unit": "mg/dL", "system": "http://unitsofmeasure.org", "code": "mg/dL"}}
```

Ejemplo de remisión: igual de `subject`; `"code": {"coding": [{"system": "http://snomed.info/sct", "code": "3457005"}]}`, `"status": "active"`, `"intent": "order"`, `"reasonCode": [{"text": "Control de diabetes tipo 2"}]` y, opcional, `"performer": [{"identifier": {"value": "IPS-NORTE"}}]` con el **código de la IPS destino** (`organizations.code`).

## Qué pasa al recibir (la reacción)

- **Observation, paciente conocido (201).** Se guarda con origen `fhir` en su atención abierta (o la más reciente; si el paciente no tiene ninguna atención, el recurso queda en `fhir_inbound_events` y el aviso sale igual). Notificación en vivo al médico responsable (tipo `interop_observacion`, por la bandeja y el canal en vivo, sin recargar). Si es **glucosa crítica** (`INTEROP_GLUCOSE_CRITICAL_HIGH` = 300 o más, `INTEROP_GLUCOSE_CRITICAL_LOW` = 54 o menos, mg/dL, **por confirmar**: las fuentes solo muestran 310 como crítica y 182 como normal) se crea la alerta crítica (`ai_report_id` nulo; una sola activa por paciente; sigue activa hasta que un médico la reconozca con una nota, R20).
- **ServiceRequest, paciente conocido (201).** Se crea una remisión pendiente en la bandeja del especialista (`GET /api/v1/referrals`, `remitente_id` nulo y `remitente_nombre` = «Sistema externo: `<cliente>`») y el especialista puede leer al paciente mientras esté pendiente, aceptada o completada. **Especialista (decisión D6):** el `especialista` activo de la IPS indicada en `performer`; si no hay, `INTEROP_DEFAULT_SPECIALIST` (id de usuario o nombre de usuario); si tampoco, 202 y cola de conciliación (nunca se descarta). La remisión es idempotente por cliente y `id` del recurso.
- **Paciente desconocido (202).** El mensaje queda en `fhir_inbound_events` (`estado_conciliacion = pendiente`, `motivo_pendiente` = `paciente_desconocido` o `sin_especialista`), el admin recibe una notificación (`interop_pendiente`) y `GET /api/v1/interop/pendientes` lo lista. El admin lo concilia con `POST /api/v1/interop/pendientes/{id}/conciliar` (`{"paciente_id": ..., "especialista_id": ...}`): se ejecuta la misma reacción del 201 y queda `PATIENT_RECONCILED`. (En clase se habló de crear el paciente en el receptor; la diapositiva pide la cola, y se siguió la diapositiva.)
- **Auditoría.** `INTEROP_RECEIVED` (201/202) e `INTEROP_REJECTED` (401, 403, 409, 413, 422, 503; también los fallos de `/oauth/token`), cada una con `origen: "externo"`, cliente, IP (`CF-Connecting-IP` solo con `TRUST_CF_CONNECTING_IP=true`), código HTTP, recurso y resultado; el emisor escribe `INTEROP_SENT`. Entran a la cadena de hashes de la auditoría.

## Registrar un socio

El socio se registra una vez, en la base a la que apunta `DATABASE_URL`, y recibe sus credenciales por un canal privado (nunca el chat del grupo):

```
docker compose exec -T api python -m scripts.registrar_socio --client-id equipo3 --nombre "Equipo 3" \
    --alcances system/Observation.write system/ServiceRequest.write
docker compose exec -T api python -m scripts.registrar_socio --listar
```

Imprime **una sola vez** `client_id`, `client_secret` y `secreto_de_firma`. De la base solo se guardan el hash Argon2id del `client_secret` y el secreto de firma **cifrado** con AES-256-GCM (`FIELD_ENCRYPTION_KEYS`, AAD ligado al cliente): hay que poder recomputar el HMAC, así que no puede ser un hash. **Sin llaves, el script se niega** (falla cerrado); `--permitir-texto-plano` acepta guardarlo sin cifrar (riesgo: se lee en la base) y lo avisa. Para ENVIAR a otro equipo ocurre al revés: es el otro equipo quien registra a SaludRed y le entrega sus credenciales, que se pasan al tester por `PROBAR_*`.

## Correr el tester (`scripts/probar_sistema.py`)

Envía **tres mensajes en vivo** a la URL base de un socio con las credenciales que él entregó: glucosa normal (182), glucosa crítica (310) y una remisión. Imprime una tabla de pasos y códigos y sale con código distinto de cero si algún paso falla. Nunca imprime secretos (que solo se leen del entorno) ni el documento.

```
PROBAR_BASE_URL=https://equipo3.example PROBAR_CLIENT_ID=... PROBAR_CLIENT_SECRET=... \
PROBAR_SIGNING_SECRET=... PROBAR_DOC_TYPE=CC PROBAR_DOC_NUMBER=... FEDERATED_SECRET=... \
docker compose exec -T api python -m scripts.probar_sistema
```

(`docker compose exec -e VAR=...` para pasar las variables al contenedor.) Opciones: `--self` (contra SaludRed mismo, base `http://localhost:8000`, con un segundo cliente registrado), `--negativos` (provoca a propósito 401 sin token, 401 por cuerpo alterado, 401 por fecha de hace una hora, 409 por nonce repetido, 422 por sistema LOINC mal escrito, 202 por paciente desconocido y 403 con un cliente registrado solo para `Observation`; este último pide `PROBAR2_CLIENT_ID`, `PROBAR2_CLIENT_SECRET` y `PROBAR2_SIGNING_SECRET`) y `--sin-auditoria` (no escribe `INTEROP_SENT`). `PROBAR_ORG_CODE` indica la IPS destino de la remisión.

El emisor (`app/services/interop/cliente.py`) maneja cada código: 201 y 202 = entregado; 401 = renueva el token **una vez** y reintenta (un segundo 401 señala credenciales o secreto de firma errados); 403 = informa el alcance faltante y no reintenta; 409 = ya entregado; 422 = muestra el `OperationOutcome`; 429 = espera `Retry-After`; 5xx o sin respuesta = reintenta con espera creciente. Cada intento se firma de nuevo (un nonce nuevo).

## Aviso sobre `Subscription` de HAPI

La alternativa a API a API es una `Subscription` entre dos HAPI. Con un criterio amplio (`Observation?`) **cualquier** glucosa de **cualquier** paciente del emisor viaja al receptor, aunque no tenga relación con él: choca con la circulación restringida de la Ley 1581 de 2012 (art. 4 lit. f). Además no hay firma por mensaje (token fijo en `channel.header`) y el receptor debería volver a consultar el recurso en lugar de confiar en el cuerpo. Por eso los flujos entre equipos van API a API: el emisor decide cada envío, hay OAuth2 y firma por mensaje. La `Subscription` que ya existe en SaludRed (`app/services/fhir_subscription.py`) es entre el HAPI propio y la API propia y no se tocó; si alguna vez se usara hacia un socio, el criterio debe filtrar por paciente y el receptor debe revalidar.

## Remisión de ida y vuelta (R30)

El mismo código hace de **A** cuando envía una remisión y de **B** cuando la recibe. Estado: implementado en código, pendiente de verificación en ASUS (`tests/test_interop_roundtrip.py`). La autoprueba usa a SaludRed como su propio socio; la demo cruzada requiere pareja (R33) y su contrato (firma e identificador federado siguen configurables).

```mermaid
sequenceDiagram
    participant M as Médico de A
    participant A as SaludRed A
    participant B as SaludRed B
    participant E as Especialista de B
    M->>A: POST /api/v1/interop/remisiones
    A->>A: alcance, consentimiento (R32), Bundle mínimo
    A->>B: POST /oauth/token (client credentials)
    A->>B: POST /interop/Bundle (token + firma + nonce)
    B->>B: valida TODO el Bundle antes de escribir
    B-->>A: 201 (id de la remisión) | 202 en cola | 422 OperationOutcome
    B-->>E: remision_creada (bandeja y contador)
    E->>B: aceptar o rechazar (flujo existente)
    B->>B: commit; Task a la bandeja de salida
    B->>A: POST /interop/Task (accepted | rejected + motivo)
    A->>A: actualiza la remisión enviada
    A-->>M: respuesta_socio (en vivo)
```

### Envío (A): `POST /api/v1/interop/remisiones`

Solo el médico (`IPS_CLINICAL_OPERATOR`). Cuerpo: `paciente_id`, `cliente_interop_id` (el `client_id` del socio registrado), `motivo` (hasta 1000), `servicio` (hasta 200) y `prioridad` (`rutina`, `urgente` o `inmediata`). El cuerpo no lleva URLs ni secretos: el destino sale de la tabla `interop_clients` y de las variables de entorno del socio. Orden: rol y alcance del paciente, consentimiento vigente para ese destino (si falta: 403 y `INTEROP_REJECTED`, sin armar ni enviar nada), identificador federado del paciente (si no se puede calcular: 422 con mensaje claro y `INTEROP_REJECTED` con `reason: no_federated_id`, sin guardar ni enviar nada), Bundle, y **antes del transporte** se guarda la remisión enviada (`outbound_referrals`) con estado `enviando` y se confirma el commit. Después del envío el estado pasa a uno de los de la tabla.

| Resultado del socio | Estado local | Respuesta |
|---|---|---|
| 201 o 202 | `enviada` | 201 con la remisión |
| 400 o 422 (no cumple el perfil) | `rechazada_por_validacion`, con el resumen del `OperationOutcome` | 201 con ese estado |
| Sin respuesta, respuesta de token inválida, 401, 403 o 5xx | `error_envio`, con el mismo identificador de `ServiceRequest` | 502; la fila se conserva |

Guardar primero evita la carrera en la que B confirma el Bundle, la lectura de A vence por tiempo y A se queda sin fila: la `Task` posterior de B encuentra la remisión también en `enviando` o `error_envio` y la pasa a `aceptada` o `rechazada`. Si la `Task` llega mientras A aún termina su envío, el estado final de A no la pisa con `enviada`. Un reintento del médico crea una remisión nueva (otro identificador); el socio puede tener entonces dos, y la anterior queda visible como `error_envio` para que el médico lo sepa.

`GET /api/v1/interop/remisiones` lista las remisiones enviadas (el médico, las suyas; el admin, todas). `GET /api/v1/interop/destinos` lista los socios activos con alcance de Bundle (solo nombre, sin secretos).

### Bundle (`POST /interop/Bundle`, alcance `system/Bundle.write`)

Misma protección que las otras entradas (token de sistema con `aud` de interoperabilidad, firma, nonce, tope de 256 KiB). Es una colección con:

- exactamente un `Patient` mínimo: solo `identifier` con el identificador federado (sin nombre, `telecom`, dirección, fecha de nacimiento ni documento);
- exactamente una `ServiceRequest` (SNOMED 3457005, `identifier` con sistema `urn:saludred:referral-request`, `priority`, `orderDetail.text` con el servicio y `reasonCode.text` con el motivo);
- `Observation` del perfil común (LOINC, UCUM); las filas locales que no cumplen el perfil no se envían;
- `DiagnosticReport` con `status: final` solo de análisis de IA **aprobados** (LOINC 11502-2, `conclusion`, `conclusionCode` con el nivel de criticidad, `issued` y el modelo en `extension`). Uno pendiente o rechazado nunca se exporta. Cada análisis viaja una sola vez, como `DiagnosticReport`: la remisión ya no incluye el `RiskAssessment` (B lo descartaba); el envío sin remisión sí lo conserva.

El Bundle es de tipo `collection` y **no lleva `total`** (la invariante bdl-1 de FHIR lo permite solo en `searchset` e `history`). Antes de enviar se recorta al límite del receptor con margen: como máximo `MAX_ENTRIES - 10` entradas y el 75 % de los 256 KiB. Se conservan el `Patient`, la `ServiceRequest`, hasta 20 `DiagnosticReport` y, con el resto del espacio, las `Observation` más recientes primero.

Todos los `subject.identifier` deben ser el del `Patient`. B valida el Bundle completo antes de escribir; una entrada inválida (aunque sea la última) da 422 con `OperationOutcome` y **no queda nada** (ni remisión, ni observaciones, ni informes). Si todo es válido, en una sola transacción: remisión con origen externo (`origen_externo` = cliente, `referencia_externa` = identificador de la `ServiceRequest`), observaciones, informes del socio (`partner_diagnostic_reports`) y auditoría `INTEROP_RECEIVED` con `origen: "externo"`; luego `remision_creada` en vivo al especialista.

| Código | Cuándo |
|---|---|
| 201 | Remisión creada; el `OperationOutcome` trae `id` con el id local |
| 202 | Paciente desconocido o sin especialista: queda en la cola de conciliación (el admin la concilia con `POST /api/v1/interop/pendientes/{id}/conciliar`) |
| 401 / 403 | Token o firma inválidos / falta `system/Bundle.write` |
| 409 | Nonce repetido o Bundle ya recibido (misma remisión) |
| 413 | Más de 256 KiB |
| 422 | Bundle inválido (no es Bundle, no hay un `Patient` y una `ServiceRequest`, recurso no admitido, `DiagnosticReport` que no es `final`, sujeto distinto, `Observation` fuera de perfil) |
| 503 | Sin `FEDERATED_SECRET` |

### Respuesta (B → A): `POST /interop/Task` (alcance `system/Task.write`)

Cuando el especialista acepta o rechaza una remisión recibida por Bundle (rutas existentes `/api/v1/referrals/{id}/accept|reject`), **después del commit** B envía:

```json
{
  "resourceType": "Task",
  "status": "accepted",
  "intent": "order",
  "focus": {"identifier": {"system": "urn:saludred:referral-request", "value": "sr-<id de la ServiceRequest de A>"}},
  "statusReason": {"text": "solo si status es rejected"}
}
```

La fila de la `Task` en `interop_task_outbox` se escribe **en la misma transacción** que el cambio de estado de la remisión (un gancho de `referrals.accept` y `referrals.reject`, justo antes de su `commit`): o quedan guardados los dos o ninguno. Si no se puede escribir la fila, la decisión también se revierte y el especialista puede intentarlo de nuevo. El envío se hace **después de responder** al especialista, en una tarea en segundo plano con su propia sesión: un socio lento no retiene la respuesta. Si el envío falla, la decisión **no** se revierte y la fila queda pendiente. `POST /api/v1/interop/outbox/reintentar` (solo admin) la reenvía, con un solo cliente HTTP por socio en cada tanda, que se cierra al terminar. Cada intento, fallido o no, deja `INTEROP_SENT` (`entregado`, `intento`, `reintento`). Solo las remisiones que llegaron por Bundle generan `Task`.

El motivo de rechazo lo escribe el especialista como texto libre. Antes de salir se limpia con el depurador de privacidad con el paciente asociado: nombre, documento, teléfono, correo y dirección se reemplazan por etiquetas (el nombre queda como `[PACIENTE]`). Lo mismo ocurre con el motivo y el servicio de la remisión que A envía; la copia local de A conserva lo que escribió el médico.

Reintentos: la cola toma primero las filas con menos intentos, y `MAX_DELIVERY_ATTEMPTS` (8) es el tope. Una respuesta 4xx del endpoint `Task` del socio, distinta de 408, 429, 401 y 403, es **permanente** (400, 404, 409, 422...). Un 401 o 403 del endpoint `Task` (API key equivocada o alcance que falta) se reintenta hasta el tope, porque el socio puede corregirlo. Una fila permanente deja la cola (`attempts` queda en el tope) y `last_error` empieza por `permanente: `. Así los fallos definitivos no bloquean las `Task` nuevas. Un 401 o 403 del endpoint de **token** del socio no es permanente (no dice nada de esta `Task` y las credenciales pueden corregirse): se reintenta hasta el tope. No hay cambio de esquema.

A busca la remisión enviada por el identificador y comprueba que pertenece al socio que llama. Códigos: 200 (aplicada o repetida: es idempotente), 404 (desconocida o de otro socio; no se distingue), 409 (ya tiene la respuesta contraria), 422 (`status` distinto de `accepted` o `rejected`, sin `focus.identifier.value`, o rechazo sin `statusReason.text`), más los de autenticación. Al aplicarla: estado `aceptada` o `rechazada` (con el motivo), `INTEROP_RECEIVED` con `origen: "externo"` y el evento `respuesta_socio` al médico remitente. El cierre del envío de A (`enviada`, `error_envio`...) bloquea la fila y solo la mueve desde `enviando` o `error_envio`: nunca pisa una `aceptada` o `rechazada`.

La respuesta también puede llegar como actualización de la `ServiceRequest` (ver «Contrato para cualquier equipo»).

### Configurar el destino de envío

Un socio puede nombrar un prefijo de entorno (`python -m scripts.registrar_socio ... --prefijo-envio SOCIO_A`). SaludRed lee entonces `SOCIO_A_BASE_URL`, `SOCIO_A_CLIENT_ID`, `SOCIO_A_CLIENT_SECRET` y `SOCIO_A_SIGNING_SECRET`; sin prefijo, usa `PROBAR_*` si `PROBAR_CLIENT_ID` coincide con el `client_id`. Para la autoprueba, registre a SaludRed como socio (alcances Bundle y Task) y apunte `PROBAR_*` a su propia URL pública. `scripts/probar_sistema.py --remision-ida-vuelta` corre el ciclo contra una URL real.

Para un socio que usa API key, defina `<PREFIJO>_API_KEY` (el valor exacto que irá en `X-Api-Key`) en lugar de `_CLIENT_ID` y `_CLIENT_SECRET`: SaludRed no pide token y envía la cabecera. Con `<PREFIJO>_SIGN=false` (también `0`, `no`, `off`) no firma y `_SIGNING_SECRET` deja de ser obligatorio. Sin esas variables todo sigue como antes (token OAuth y firma).

## Contrato para cualquier equipo

El docente puede emparejarnos con cualquier equipo y el enunciado (R31) pide solo «credenciales de sistema (client credentials o API key por institución)». Por eso los controles adicionales de SaludRed (firma con nonce, identificador federado) se pueden relajar **por acuerdo con el socio**, uno a uno, con variables de entorno. Todos los valores por defecto conservan el comportamiento estricto.

### Rutas de entrada (solo estas aceptan API key)

| Ruta | Alcance | Qué recibe |
|---|---|---|
| `POST /interop/Bundle` | `system/Bundle.write` | Remisión con su evidencia |
| `POST /interop/Task` | `system/Task.write` | Respuesta a una remisión que enviamos |
| `POST /interop/ServiceRequest` | `system/ServiceRequest.write` | Una remisión suelta (remisión suelta: sin respuesta automática; use `/interop/Bundle` para recibir respuesta), **o** la respuesta a una que enviamos (actualización) |
| `POST /interop/Observation` | `system/Observation.write` | Un resultado de laboratorio |
| `POST /oauth/token` | — | Token OAuth (client credentials), solo si el socio prefiere token |

### Dos formas de autenticarse

1. **Token OAuth** (por defecto): `POST /oauth/token` con `grant_type=client_credentials`, `client_id` y `client_secret`; luego `Authorization: Bearer <token>`.
2. **API key** (`INTEROP_ALLOW_API_KEY=true`): `X-Api-Key: <client_id>:<client_secret>`. Se separa con `:` porque un `client_id` solo admite `[a-z0-9._-]`. Se verifica contra el mismo hash de `client_secret` que usa el endpoint de token (la verificación del hash es de tiempo constante y un cliente desconocido gasta lo mismo que un secreto equivocado), con los mismos alcances registrados, la misma comprobación de cliente activo, el mismo límite de tasa y de tamaño (256 KiB) y la misma auditoría (`origen: "externo"`). El límite de tasa del grupo `interop` usa como identidad la parte `client_id` de la cabecera (`apikey:<client_id>` normalizado, sin verificar, igual que en el grupo `oauth`): dos socios con API key detrás de la misma dirección (el túnel) tienen cubos separados, y el tope por dirección sigue acotando a quien rote identificadores. Si llegan las dos credenciales, gana el Bearer. **Una API key nunca sirve en `/api/v1/...`**: esas rutas solo aceptan el JWT de usuario. Con la opción apagada, `X-Api-Key` recibe 401.

La firma HMAC (`X-Interop-Fecha`, `X-Interop-Nonce`, `X-Interop-Firma`) es obligatoria por defecto. Con `INTEROP_REQUIRE_SIGNATURE=false` se acepta un mensaje **sin** cabeceras de firma y se omiten con ella la fecha y el nonce; un mensaje que sí trae firma se verifica siempre (firma, ventana de fecha y nonce).

### Bundle mínimo (ejemplo)

```json
{
  "resourceType": "Bundle",
  "type": "collection",
  "entry": [
    {"resource": {"resourceType": "Patient",
                  "identifier": [{"system": "urn:uao:salud-digital:paciente-federado",
                                  "value": "<id federado de 32 hex>"}]}},
    {"resource": {"resourceType": "ServiceRequest", "id": "sr-1",
                  "identifier": [{"system": "urn:saludred:referral-request", "value": "sr-1"}],
                  "status": "active", "intent": "order", "priority": "routine",
                  "code": {"coding": [{"system": "http://snomed.info/sct", "code": "3457005"}]},
                  "orderDetail": [{"text": "Endocrinologia"}],
                  "reasonCode": [{"text": "Control de diabetes tipo 2"}],
                  "subject": {"identifier": {"system": "urn:uao:salud-digital:paciente-federado",
                                             "value": "<id federado de 32 hex>"}}}}
  ]
}
```

Exactamente un `Patient` y una `ServiceRequest`; el resto (`Observation`, `DiagnosticReport` final) es opcional. `name`, `telecom`, `address` y `birthDate` se rechazan siempre.

### Paciente sin identificador federado (`INTEROP_ACCEPT_DOCUMENT_IDENTIFIER=true`)

Para un socio que no puede calcular el identificador federado, B acepta un `Patient` (y `subject.identifier`) con el documento nacional: `{"system": "urn:uao:salud-digital:documento", "type": {"text": "CC"}, "value": "1144123456"}`. El tipo (`CC`, `TI`, `CE` o `PA`) puede ir en `type.coding[].code`, en `type.text` o como último segmento de `system`. B calcula con `federado.py` el mismo identificador federado y resuelve al paciente igual que siempre; antes de guardar nada, cambia el documento por el identificador federado en **todo** el Bundle (el `Patient`, el `subject` de cada recurso y, por tanto, también los `DiagnosticReport` guardados en `partner_diagnostic_reports`) y el mismo Bundle ya limpio alimenta `fhir_inbound_events` y la conciliación; la auditoría nunca recibe el número. Esa limpieza **no depende del ajuste**: si un `Patient` trae el identificador federado y además un documento (con el ajuste apagado el federado basta), el documento se descarta. Así el número no queda almacenado en ninguna tabla.

Es un intercambio negociado frente a la minimización de datos (Ley 1581 de 2012, art. 4 lit. b y f: finalidad y circulación restringida): el documento viaja entre las dos instituciones solo porque ambas lo acordaron, y por eso está apagado por defecto. Lo que enviamos nosotros sigue siendo mínimo: siempre el identificador federado; con `INTEROP_SEND_DOCUMENT_IDENTIFIER=true` el `Patient` lleva además el documento (los demás recursos conservan solo el federado), para socios que lo necesitan.

### Respuesta del socio (dos formas equivalentes)

`Task` (`POST /interop/Task`):

```json
{"resourceType": "Task", "status": "rejected", "intent": "order",
 "focus": {"identifier": {"system": "urn:saludred:referral-request", "value": "sr-1"}},
 "statusReason": {"text": "Sin cupos este mes"}}
```

Actualización de la `ServiceRequest` (`POST /interop/ServiceRequest`, alcance `system/ServiceRequest.write`): `status: active` significa aceptada y `revoked` rechazada, con el mismo identificador que enviamos:

```json
{"resourceType": "ServiceRequest", "id": "sr-1",
 "identifier": [{"system": "urn:saludred:referral-request", "value": "sr-1"}],
 "status": "revoked", "intent": "order",
 "note": [{"text": "Sin cupos este mes"}]}
```

El motivo se lee de `statusReason.text`, de `note[].text` o de `reasonCode[].text` y es obligatorio al rechazar. Se distingue de una remisión nueva por el identificador: si coincide con una remisión que **enviamos a ese mismo socio** es una respuesta; si no coincide (o es de otro socio) se valida como remisión nueva. Se trata exactamente como una `Task` (idempotente, 409 si contradice, mismo evento `respuesta_socio`). Cualquier otro `status` en una respuesta da 422.

### Códigos de respuesta

| Código | Significado |
|---|---|
| 201 | Creado (remisión, observación asociada al paciente) |
| 200 | Respuesta (`Task` o actualización de `ServiceRequest`) aplicada, o repetida |
| 202 | Aceptado y en cola de conciliación (paciente desconocido o sin especialista) |
| 400 | Solo en `/oauth/token`: petición mal formada o `grant_type` no soportado |
| 401 | Falta o es inválida la credencial (token, API key, firma o fecha); también API key con la opción apagada |
| 403 | La credencial es válida pero no tiene el alcance de la ruta |
| 404 | `Task` o respuesta que no corresponde a una remisión de ese socio |
| 409 | Nonce repetido, recurso ya recibido o respuesta contraria a una ya registrada |
| 413 | Cuerpo de más de 256 KiB |
| 422 | No cumple el perfil común (`OperationOutcome` con el detalle) |

### Variables de este modo

| Variable | Por defecto | Efecto |
|---|---|---|
| `INTEROP_ALLOW_API_KEY` | `false` | Acepta `X-Api-Key: <client_id>:<client_secret>` en las rutas `/interop/*` |
| `INTEROP_REQUIRE_SIGNATURE` | `true` | `false`: acepta mensajes sin firma (y sin fecha ni nonce); los firmados se verifican |
| `INTEROP_ACCEPT_DOCUMENT_IDENTIFIER` | `false` | Acepta un `Patient` identificado por documento (tipo y número) y calcula el federado |
| `INTEROP_SEND_DOCUMENT_IDENTIFIER` | `false` | Nuestro Bundle añade el documento al `Patient` (además del federado) |
| `INTEROP_DOCUMENT_SYSTEM` | `urn:uao:salud-digital:documento` | `system` del identificador de documento que enviamos |
| `<PREFIJO>_API_KEY` | sin definir | Envío con `X-Api-Key` en lugar de token |
| `<PREFIJO>_SIGN` | firma activa | `false`: envía sin firma (no exige `_SIGNING_SECRET`) |

## Variables de entorno

| Variable | Secreto | Por defecto | Para qué |
|---|---|---|---|
| `<PREFIJO>_BASE_URL`, `_CLIENT_ID` | no | sin definir | Destino y cliente con los que SaludRed envía a un socio (R30) |
| `<PREFIJO>_CLIENT_SECRET`, `_SIGNING_SECRET` | **sí** | sin definir | Credenciales con las que SaludRed envía a ese socio; nunca se leen del cuerpo de una petición |
| `PROBAR_MEDICO_USER/_PASSWORD`, `PROBAR_ESPECIALISTA_USER/_PASSWORD`, `PROBAR_PACIENTE_ID` | **sí** (las claves) | sin definir | Solo para `--remision-ida-vuelta` completo |
| `FEDERATED_SECRET` | **sí** | sin definir (federado apagado) | Secreto del identificador federado; lo entrega el docente |
| `FEDERATED_SYSTEM` | no | `urn:uao:salud-digital:paciente-federado` (provisional) | URI del sistema de `subject.identifier` |
| `INTEROP_JWT_SECRET` | **sí** | `JWT_SECRET_KEY` | Llave de los tokens de sistema (el `aud` ya los separa de los de usuario) |
| `INTEROP_AUDIENCE` | no | `saludred-interop` | `aud` del token de sistema |
| `INTEROP_TOKEN_TTL_SECONDS` | no | 600 | Vida del token |
| `INTEROP_SIGNATURE_TOLERANCE_SECONDS` | no | 300 | Ventana de la fecha de la firma |
| `INTEROP_GLUCOSE_CRITICAL_HIGH` / `_LOW` | no | 300 / 54 | Umbrales críticos de glucosa (mg/dL, por confirmar) |
| `INTEROP_DEFAULT_SPECIALIST` | no | sin definir | Especialista (id o nombre de usuario) cuando la IPS destino no tiene uno |
| `INTEROP_ALLOW_API_KEY`, `INTEROP_REQUIRE_SIGNATURE`, `INTEROP_ACCEPT_DOCUMENT_IDENTIFIER`, `INTEROP_SEND_DOCUMENT_IDENTIFIER`, `INTEROP_DOCUMENT_SYSTEM` | no | `false`, `true`, `false`, `false`, `urn:uao:salud-digital:documento` | Modo «cualquier equipo» (sección «Contrato para cualquier equipo») |
| `<PREFIJO>_API_KEY`, `<PREFIJO>_SIGN` | **sí** (la clave) | sin definir | Envío con API key y sin firma a un socio (ver «Configurar el destino de envío») |
| `FIELD_ENCRYPTION_KEYS`, `FIELD_ENCRYPTION_ACTIVE_KID` | **sí** | sin definir | Necesarias para registrar un socio con el secreto de firma cifrado |
| `PROBAR_*`, `PROBAR2_*` | **sí** (las credenciales) | — | Solo para el tester |

## Mapa normativo (donde aplica a este intercambio)

| Norma | Cómo se refleja |
|---|---|
| Ley 1581 de 2012, art. 4 lit. f (acceso y circulación restringidos) y lit. g (seguridad) | El identificador federado en lugar del documento; un alcance por tipo de recurso concedido solo si está registrado; cola de conciliación en lugar de descartar o publicar; aviso sobre la `Subscription` abierta |
| Ley 1581 de 2012, art. 17 lit. d (conservar la información con seguridad) | Firma HMAC por mensaje con nonce, token de 10 minutos, secreto de firma cifrado en reposo |
| Ley 2015 de 2020 y Resolución 866 de 2021 (historia clínica interoperable) | FHIR R4, LOINC, SNOMED CT y UCUM como perfil común |
| Resolución 1995 de 1999 (historia clínica) | Cada mensaje recibido o rechazado queda en la auditoría encadenada con su origen externo |
| Ley 1273 de 2009, arts. 269A y 269F | El acceso solo con credenciales registradas; el rechazo de cada intento queda auditado |

Las citas «según la diap. 22 del profesor» están marcadas como tales en [`NORMATIVA.md`](NORMATIVA.md), que lleva el mapa completo; no se contrastaron con el texto oficial.

## Límites conocidos

- Las rutas de los socios no pasan por `nginx`; el límite de tasa es de FastAPI, en proceso y por IP (detrás de `tunnel-api` todos los socios comparten la dirección del túnel salvo que `TRUST_CF_CONNECTING_IP=true`).
- Los pacientes existentes en Neon no tienen `id_federado` hasta que se apruebe el relleno (D1); mientras tanto se resuelven por cálculo.
- La ida y vuelta de una remisión (R30) existe solo para el Bundle; la `Observation` y la `ServiceRequest` sueltas no devuelven nada al emisor. Sigue sin haber renovación del token por actividad (T10/11 l. 1092-1105). El `Task` de respuesta no lleva informe del especialista: solo aceptada o rechazada con el motivo. Ver «Pedido por el profesor y no incluido en el brief» en [`ENTREGA_FINAL.md`](ENTREGA_FINAL.md).
- Los resultados de laboratorio que llegan dentro de un Bundle se guardan, pero no disparan alerta crítica de glucosa (solo la `Observation` suelta lo hace).
- La remisión enviada se guarda **antes** del envío (`enviando`), de modo que una `Task` que llegue antes de que A termine, o después de un tiempo de espera agotado (`error_envio`), encuentra su remisión. Queda abierto que un reintento del médico tras un `error_envio` duplica la remisión en el socio, que ya la había confirmado.
- Al conciliar un Bundle pendiente se valida de nuevo; si ya no cumple el perfil, la ruta de administración responde 422 con el motivo y el mensaje sigue pendiente.
- La Observation recibida se guarda en SaludRed; no se reenvía a HAPI (el eco hacia HAPI no forma parte del encargo).
- Los umbrales críticos y los rangos de HbA1c e IMC son propuestas.
