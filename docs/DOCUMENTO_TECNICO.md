# Documento técnico de SaludRed

Documento de entrega del Proyecto 2 (Corte 2) de «Sistemas en Salud Digital». Resume el contexto, las decisiones de arquitectura y sus motivos, el modelo de datos, la seguridad, los modelos de IA, la estrategia de pruebas y las limitaciones. Para el detalle de cada tema enlaza a los documentos específicos en lugar de repetirlos.

## 1. Contexto y problema

Una EPS coordina una red de IPS. La información sobre ocupación, limpieza, bloqueos y disponibilidad de camas está fragmentada y se actualiza tarde: una cama puede estar físicamente libre y, sin embargo, no usarse porque su estado no se conoce a tiempo. El resultado son esperas para pacientes que requieren hospitalización, verificaciones repetidas entre áreas y ausencia de trazabilidad.

SaludRed ataca cuatro puntos: **visibilidad** (todos los actores autorizados ven el mismo estado), **oportunidad** (el cambio se registra cuando ocurre), **coordinación** (solicitudes y asignaciones se comparten entre IPS) y **trazabilidad** (cada cambio deja usuario, acción, recurso y fecha).

El Corte 2 pide además un sistema **activo, no un buzón de archivos**: empuja la información a quien la necesita, un hecho dispara consecuencias (notificar, facturar, alertar) y lo crítico no se pierde hasta que un clínico lo atiende. Los requisitos R01 a R22 se mapean a módulos en el [README](../README.md#mapa-de-requisitos-r01r22).

## 2. Arquitectura y decisiones

Servicios, roles y diagramas: [README](../README.md#arquitectura). A continuación, las decisiones y su motivo.

### 2.1 PostgreSQL para lo clínico, MongoDB para la facturación

- La historia clínica exige integridad referencial, restricciones (`CHECK`, índices únicos parciales como «una cama, una asignación activa») y migraciones versionadas: encaja en PostgreSQL.
- Una factura es un documento con campos que varían según el servicio (`reporte_id`, `solicitud_cama_id`, `remision_id`); en el modelo relacional serían columnas casi siempre vacías o una tabla por servicio. En MongoDB un servicio nuevo no exige migración.
- La separación es **física**: el contable no ve nada clínico y el médico no ve nada financiero. MongoDB guarda solo `paciente_id` opaco, nunca nombre ni documento (minimización, Ley 1581).
- La idempotencia de las facturas automáticas la garantizan índices únicos parciales, no el código. Detalle: [`CONTABILIDAD.md`](CONTABILIDAD.md).

### 2.2 Bus de eventos y reactores

Las acciones de negocio (remitir, aprobar, liberar una cama, bloquear una cuenta) **solo publican un hecho de dominio** después del commit; pequeñas funciones reactoras notifican, facturan, alertan y cuentan. Así la lógica no queda pegada a cada endpoint, un reactor que falla no rompe la acción de negocio (si MongoDB está caído, aprobar sigue funcionando y la conciliación recupera la factura), y un flujo nuevo es un reactor más. Catálogo de eventos: [`EVENTOS.md`](EVENTOS.md).

### 2.3 WebSocket frente a SSE

El requisito acepta cualquiera de los dos. Se implementaron **ambos sobre el mismo registro en memoria**, con el mismo JSON. Se verificó que los túneles rápidos de Cloudflare entregan los encabezados de una respuesta SSE pero **no el cuerpo** mientras la conexión siga abierta (130 s sin recibir ni el mensaje inicial ni latidos), con independencia de cabeceras, compresión o protocolo. Los túneles sí admiten WebSocket, así que el canal público es `GET /api/v1/events/ws?token=` y el SSE (`/api/v1/events/stream`) queda para la red local. El servidor envía `{"tipo":"ping"}` cada 15 s, por debajo del límite de inactividad de Cloudflare (unos 100 s). nginx tiene ubicaciones exactas, sin búfer, para ambos.

Consecuencia de diseño: el registro de conexiones vive en la memoria del proceso, por lo que la API corre **un solo proceso**; una prueba (`tests/test_deploy_constraints.py`) falla si alguien añade `--workers` o réplicas. Escalar exigiría un broker (Redis pub/sub) solo para el canal en vivo; la bandeja en base de datos ya funciona con varias réplicas.

### 2.4 El servicio `ml` aislado de la base de datos

Los modelos corren en su propio contenedor, sin puertos publicados, sin cadena de conexión a la base y con un contrato estricto (`extra="forbid"`: un campo desconocido se rechaza con 422). La API es el único cliente y le entrega variables clínicas minimizadas y un seudónimo. Así, aunque el servicio fuera comprometido, no puede leer más de lo que la API le entrega. También corre en Linux porque en el servidor de despliegue (Windows) las DLL de scikit-learn se bloquean.

### 2.5 FHIR con supresión del eco e idempotencia

- **Sincronización hacia HAPI (R15):** automática al guardar, en una tarea de fondo que nunca bloquea ni hace fallar la escritura clínica; es una actualización condicional por identificador, así que repetirla no duplica. El paciente se encuentra en HAPI por tres identificadores.
- **Reacción a datos externos (R21):** una `Subscription` con canal `rest-hook` hace que **HAPI llame a la API**; no hay sondeo. El receptor autentica una clave compartida, **descarta el eco** (todo recurso que SaludRed envía lleva una etiqueta de origen, y sus propias observaciones también disparan la suscripción), aplica **idempotencia** por `(id, versionId)` con una restricción `UNIQUE`, ubica al paciente por `subject.reference`, guarda la observación con origen `fhir`, notifica al médico responsable y audita el evento con origen FHIR.
- **Intercambio:** exportación e importación de la historia como `Bundle` (los reportes de IA aprobados viajan como `RiskAssessment`) y `ServiceRequest` para las remisiones.

Detalle: [`INTEROPERABILIDAD.md`](INTEROPERABILIDAD.md) y [`EVENTOS.md`](EVENTOS.md).

### 2.6 Soft put: la IA propone y un clínico firma

El análisis de IA crea un reporte `pendiente` que **no forma parte de la historia clínica**. Solo un `medico` o `especialista` lo aprueba o rechaza (el rechazo exige motivo); el reporte guarda quién lo firmó. El payload enviado, la respuesta del modelo y la versión son inmutables, y no existe `DELETE`: un reporte rechazado queda como evidencia. Solo el reporte aprobado aparece en la historia y en el PDF, y solo entonces su probabilidad alimenta el orden de la cola de camas. El mismo principio rige la limpieza: quien registra una limpieza no la aprueba. Detalle: [`IA.md`](IA.md).

### 2.7 Otras decisiones

- **Cola de camas** ordenada por prioridad clínica, luego por `criticality_score` (sin puntaje al final) y luego por hora de llegada; el orden y la regla del 409 usan la misma clave (`_queue_key` en `app/services/beds.py`).
- **Borrado lógico:** `deleted_at IS NULL` es la única fuente de verdad; la edición conserva la versión anterior; solo el admin restaura.
- **Dos bases separadas** para la aplicación y para HAPI, para no acoplar las migraciones al esquema interno de HAPI.

## 3. Modelo de datos (resumen)

Veintidós tablas en PostgreSQL, más la vista `v_inventario_camas` y una colección `facturas` en MongoDB:

| Bloque | Tablas |
|---|---|
| Red y ubicaciones | `organizations` (EPS e IPS, jerarquía autorreferenciada), `locations` (sedes, servicios, habitaciones y camas) |
| Identidad | `roles`, `users` |
| Clínico | `patients`, `encounters`, `observations` (LOINC y UCUM, con origen `saludred`, `fhir` o `import`), `imaging_studies`, `referrals` |
| Camas | `bed_requests` (con `criticality_score`), `bed_assignments`, `bed_status_events`, `bed_cleaning_records` |
| IA y alertas | `ai_reports`, `alerts` |
| Cohorte real | `cohort_records` (marcadas `REAL` o `SYNTHETIC`, con grupo), `cohort_clusters` |
| Trazabilidad e interoperabilidad | `record_versions`, `audit_log`, `fhir_sync_log`, `fhir_inbound_events`, `notifications` |

La cadena de migraciones de Alembic termina en `f6b3d8e1a742`. El diagrama entidad-relación y el mapeo a FHIR están en [`modelo-datos-y-fhir.md`](modelo-datos-y-fhir.md) y [`diagramas.md`](diagramas.md). Los datos y sus licencias: [`DATASETS.md`](DATASETS.md).

## 4. Seguridad

Resumen; la correspondencia con las normas y las pruebas de cada control están en [`NORMATIVA.md`](NORMATIVA.md).

- **Acceso:** JWT con expiración y contraseñas con bcrypt. Autorización en el servidor por rol, institución y pertenencia del registro; lo ajeno responde 404 o 403. Seis roles; solo el admin crea cuentas.
- **Bloqueo:** tres intentos fallidos bloquean la cuenta (423); una cuenta bloqueada se rechaza antes de mirar la contraseña; un usuario inexistente responde igual que una clave errónea; el bloqueo avisa al admin y el token anterior deja de servir.
- **Red:** nginx limita la tasa (20 solicitudes por segundo por cliente con ráfaga de 40, y 10 por minuto en el login; el exceso recibe 429), envía una `Content-Security-Policy` y cabeceras de seguridad; la API envía `nosniff` y `X-Frame-Options`; CORS cerrado por defecto. `ml`, `mongo` y Orthanc no publican puertos al exterior.
- **Minimización hacia la IA (R12):** el payload se arma campo por campo desde una lista de permitidos, con un seudónimo HMAC; el payload exacto queda registrado y consultable.
- **Auditoría:** usuario, acción, recurso y fecha para las acciones relevantes; solo el admin la consulta.
- **Secretos:** ninguno en el repositorio; `.env` está excluido y los secretos obligatorios no tienen valor por defecto.

## 5. Modelos de IA y métricas

Para cada tarea se entrenan y comparan ocho modelos supervisados (regresión logística, KNN, SVM con kernel RBF, árbol de decisión, random forest, gradient boosting, stacking y una red neuronal pequeña) con el mismo protocolo, y el servicio `ml` atiende al ganador. Las métricas y la comparación se consultan en `GET /api/v1/ai/models`, se ven en Indicadores y se resumen en `ml/REPORTE_MODELOS.md`. Detalle completo, método y tablas en [`IA.md`](IA.md).

**Protocolo.** Validación cruzada de 5 particiones repetida 3 veces, ajuste de hiperparámetros dentro de cada partición de entrenamiento y **umbral elegido con las predicciones fuera de partición** (el más alto con la sensibilidad objetivo). El ganador es el de mayor F1 fuera de partición entre los que cumplen la sensibilidad; si la diferencia con otro es menor que un error estándar, gana el más simple y explicable.

### 5.1 Criticidad del paciente

Predice si el paciente necesitará ingreso hospitalario con urgencia. Se entrena con 192 visitas reales de 51 pacientes (MIMIC-IV-ED Demo v2.2). La evaluación usa solo filas reales y está **agrupada por paciente**; el aumento por grupos se aplica solo al entrenamiento. Sensibilidad objetivo 0,85.

| Modelo | AUC | F1 | Sensibilidad | Precisión |
|---|---|---|---|---|
| **Regresión logística (ganador)** | 0,623 | 0,746 | 0,850 | 0,666 |
| Árbol de decisión | 0,556 | 0,750 | 0,856 | 0,669 |
| KNN | 0,585 | 0,753 | 0,861 | 0,671 |
| Random forest | 0,576 | 0,760 | 0,853 | 0,686 |
| Gradient boosting | 0,581 | 0,752 | 0,850 | 0,674 |
| SVM (RBF) | 0,538 | 0,756 | 0,853 | 0,680 |
| Red neuronal (MLP) | 0,616 | 0,753 | 0,850 | 0,675 |
| Stacking | 0,573 | 0,759 | 0,853 | 0,684 |

**Por qué ganó la logística.** Todos los F1 están entre 0,746 y 0,760, dentro de un error estándar (0,023): es un empate práctico y gana el más simple y explicable, que además tiene el AUC más alto. Ningún modelo supera el F1 de marcar a todos (0,796). Matriz de confusión del ganador (promedio por repetición, umbral 0,415): 108,0 críticos detectados, 19,0 perdidos, 54,3 falsas alarmas y 10,7 descartados correctamente; especificidad 0,164 y 85 % de pacientes marcados.

**Umbral.** El error más costoso es no marcar a quien sí necesita cama con urgencia. Por eso el umbral no es 0,5: es el más alto cuya sensibilidad fuera de partición alcanza 0,85 (0,415). El costo es una baja especificidad.

**Sesgo por subgrupo** (al umbral 0,415, sobre filas reales):

| Subgrupo | n | Sensibilidad | Precisión | AUC |
|---|---|---|---|---|
| Mujeres | 106 | 0,828 | 0,636 | 0,650 |
| Hombres | 86 | 0,874 | 0,699 | 0,575 |
| Menores de 45 años | 14 | 0,889 | 0,690 | 0,785 |
| 45 a 64 años | 117 | 0,797 | 0,617 | 0,523 |
| 65 años o más | 61 | 0,932 | 0,746 | 0,774 |

El subgrupo de 45 a 64 años es el más grande y el peor (AUC cercano al azar; sensibilidad por debajo del objetivo de 0,85). La sensibilidad es menor en mujeres que en hombres. Los menores de 45 son 14 filas y no son concluyentes.

**Lectura honesta.** La señal es débil: a su umbral, la precisión (0,666) apenas supera la de marcar a todos como críticos (0,661, igual a la tasa de ingresos). Por eso el reporte es un apoyo a la priorización que un clínico firma, no un diagnóstico, y el puntaje solo desempata dentro de la misma prioridad clínica. Con valores claramente graves el modelo sí separa (probabilidad de 0,999 contra 0,272 para un paciente con signos normales).

### 5.2 Retraso de limpieza de camas

Predice si una cama que entra a limpieza estará lista en más de 90 minutos. Se entrena con 800 eventos **simulados** (20,6 % de retrasos). Sensibilidad objetivo 0,80.

| Modelo | AUC | F1 | Sensibilidad | Precisión |
|---|---|---|---|---|
| **Regresión logística (ganador)** | 0,823 | 0,525 | 0,806 | 0,389 |
| Árbol de decisión | 0,778 | 0,487 | 0,806 | 0,349 |
| KNN | 0,783 | 0,507 | 0,814 | 0,368 |
| Random forest | 0,808 | 0,520 | 0,804 | 0,384 |
| Gradient boosting | 0,807 | 0,507 | 0,806 | 0,370 |
| SVM (RBF) | 0,770 | 0,478 | 0,810 | 0,339 |
| Red neuronal (MLP) | 0,812 | 0,521 | 0,802 | 0,386 |
| Stacking | 0,816 | 0,521 | 0,802 | 0,385 |

La logística gana con el F1 más alto. Matriz de confusión del ganador (umbral 0,15): 133,0 retrasos detectados, 32,0 perdidos, 209,0 falsas alarmas y 426,0 descartados correctamente; especificidad 0,671.

El sesgo por subgrupo muestra que casi no detecta retrasos en Urgencias (6 casos, sensibilidad 0,17) y menos en Hospitalización que en UCI; no debe usarse como alerta en Urgencias hasta contar con datos reales. Las métricas describen la simulación, no hospitales reales.

## 6. Estrategia de pruebas

| Capa | Cantidad | Cómo se ejecuta |
|---|---|---|
| API (Python, pytest) | **614** | Dentro del contenedor `api`, con SQLite en memoria |
| Servicio `ml` (pytest) | 29 | En un contenedor Linux |
| Frontend (Vitest y React Testing Library) | **269** | En un contenedor `node:22-alpine`, junto con lint y compilación |

Los comandos están en el [README](../README.md#pruebas). Principios:

- **Las pruebas no tocan la base real:** usan SQLite en memoria y `mongomock`. Como SQLite acepta SQL que PostgreSQL rechaza, hay pruebas que compilan las consultas con el dialecto de PostgreSQL (`test_cleaning_prediction_sql.py`, `test_reactive_sql.py`, `test_billing_sql.py`, `test_analytics_sql.py`).
- **Cada regla de acceso tiene su prueba por rol** (`paciente` y `contable` reciben 403 donde corresponde), y una prueba recorre todas las rutas de la API para verificar que, sin token, respondan 401 o 403 salvo el login.
- **Las pruebas de ambiente:** dos pruebas de alertas y dos de limpieza dependen de variables de entorno (`ALERT_ESCALATION_ENABLED`, `REQUIRE_CLEANING_EVIDENCE`); el `conftest.py` las fija con `setdefault`, que no pisa una variable ya definida en el contenedor en ejecución. Por eso el comando del README las fuerza a `false`.

### Verificación en vivo, por epic

Además de las pruebas automáticas, cada epic se verificó contra el sistema real desplegado (bases gestionadas en Neon) y, desde el Epic 5, por los túneles públicos, con respaldo previo de la base. Resultados:

| Epic | Alcance | Verificación en vivo |
|---|---|---|
| 1 | Inventario de camas, cola por criticidad, EPS e IPS | Migraciones aplicadas; vista con 48 camas; flujo de EPS e IPS con sus 409, 422 y 403; 200 pruebas |
| 2 | Seis roles, CORS, cabeceras, límite de tasa | Roles, 403 y 401 por rol; CORS sin reflejo de orígenes; 429 en el login (del séptimo intento) y 58 de 100 peticiones simultáneas |
| 3 | Cohorte real, grupos, imágenes, limpieza | 13 de 13 verificaciones; 258 pruebas |
| 4 | IA, seudonimización, firma, PDF | 59 de 59; 291 pruebas de la API y 16 de `ml`; un error de SQL (`IS NOT` con parámetro) hallado y corregido con una prueba nueva |
| 5 | FHIR, `Subscription`, bandeja, WebSocket | 68 de 68, más 19 de 19 del WebSocket por el túnel público; 416 pruebas |
| 6 | Contabilidad en MongoDB | 80 de 80; factura automática 2,7 s después de aprobar; Mongo caído sin afectar la aprobación; sin datos personales en Mongo; 464 pruebas |
| 7 | Sistema reactivo, remisiones, alertas | Guion de 6 pasos por los túneles (98 de 98), escalamiento (14 de 14), alerta de cama (21 de 21), tres ventanas sin recargar (22 de 22); 614 pruebas |
| 8 | Frontend: limpieza, red de atención, cohorte, roles | 93 de 93 por la API y 51 de 51 de pantalla; 268 pruebas del frontend |

Latencias medidas (de extremo a extremo): remitir 1,9 s, aceptar 1,8 s, alerta crítica 2,8 s, factura 2,9 a 3,2 s, bloqueo de cuenta 3,7 s, `Observation` de HAPI 5 a 9 s.

## 7. Limitaciones conocidas

- **Modelo de criticidad modesto:** AUC de 0,64 con 192 visitas de un solo hospital de Estados Unidos; no está calibrado ni validado para la población colombiana. El subgrupo de 45 a 64 años está cerca del azar. Con el 82 % de los pacientes marcados, la alerta se limita a una activa por paciente.
- **Modelo de limpieza simulado:** sus métricas describen la simulación.
- **Túneles rápidos:** las URLs cambian en cada reinicio; no entregan SSE (se usa WebSocket); la primera conciliación de un historial grande excede los 100 s del túnel y debe ejecutarse por la red local.
- **Latencia de HAPI:** la tarea de entrega de suscripciones corre cada 5 s (fijo en HAPI 7.4); un dato externo tarda entre 5 y 9 s en llegar.
- **Un solo proceso de API:** el canal en vivo vive en memoria; escalar exigiría un broker.
- **Cifrado a nivel de campo:** fuera del alcance del Corte 2 (`REQUISITOS.md`, sección 9).
- **Autorización del titular:** no se registra con fecha y versión del aviso de privacidad.
- **Interoperabilidad parcial:** sin mapeo campo por campo contra la Resolución 866 ni perfil del RDA; los estudios de imagen no se envían a FHIR.
- **Cobertura de pruebas del límite de tasa:** se verificó en vivo, no con pruebas unitarias, porque nginx no corre en `pytest`. Una ráfaga secuencial contra un endpoint de datos puede no llegar a 429 si cada petición tarda más de 50 ms; una ráfaga simultánea o contra el login sí.
- **Tarifas ilustrativas:** los valores de `app/billing/tariffs.py` son inventados para la demostración.
- **Imágenes sin vínculo clínico:** las radiografías cargadas no pertenecen a los pacientes de la red; demuestran el flujo.
- **El PACS no tiene respaldo:** `docker compose down -v` borra las imágenes.

## 8. Próximos pasos

- Registrar la autorización del titular (fecha y versión del aviso de privacidad).
- Ampliar la muestra del modelo de criticidad y reevaluar el sesgo por subgrupo antes de cualquier uso clínico.
- Reemplazar los túneles rápidos por un túnel con nombre y dominio propio, lo que permitiría usar SSE.
- Mapear campo por campo la Resolución 866 y el perfil del RDA (Resolución 1888); enviar los estudios de imagen a FHIR.
- Plan de seguridad y privacidad escrito; respaldo del volumen del PACS.
- Llevar `publish` a un broker si la API debe escalar a varias réplicas.
- Pruebas de carga del límite de tasa y de los canales en vivo.
