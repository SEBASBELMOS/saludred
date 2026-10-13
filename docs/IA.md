# Inteligencia artificial

Este documento describe cómo SaludRed usa modelos de aprendizaje automático y qué controles técnicos protegen al paciente (requisitos R10, R11, R12 y R22). La regla que organiza todo el diseño es una sola: **la IA propone y un clínico decide**. Ningún resultado del modelo entra a la historia clínica ni mueve la cola de camas sin la firma de un médico o especialista.

## Flujo

```
paciente ──► capa de seudonimización ──► servicio ml ──► reporte "pendiente" ──► firma clínica ──► historia / PDF
 (API)        (lista de permitidos         (sin base          (ai_reports,            (aprobar o          (solo
               + HMAC del id)               de datos)          inmutable)              rechazar)           aprobados)
```

1. **Solicitud.** Un médico o especialista pide el análisis de un paciente de su IPS (`POST /api/v1/patients/{id}/ai-analyses`).
2. **Capa de seudonimización (R12).** La API arma el payload desde las últimas observaciones del paciente. Solo salen variables clínicas de la lista de permitidos, la edad en años enteros, el sexo y un seudónimo.
3. **Modelo.** La API llama al servicio `ml`, que está en su propio contenedor, no tiene puertos publicados y no accede a la base de datos. Responde con la probabilidad, el nivel (`critico` / `no_critico`), el umbral, la versión del modelo y las variables que más pesaron.
4. **Reporte pendiente (R10).** La API guarda en `ai_reports` el payload exacto que se envió y la respuesta exacta, con estado `pendiente`. En este punto el reporte **no forma parte** de la historia clínica ni afecta la cola.
5. **Firma clínica.** El médico o especialista lo revisa, puede escribir observaciones y lo aprueba (`aprobado`) o lo rechaza (`rechazado`, con motivo obligatorio). Solo se puede revisar desde `pendiente`; una segunda revisión responde 409.
6. **Historia y PDF (R11).** Solo los reportes aprobados aparecen en `GET /api/v1/patients/{id}/clinical-record` (campo `reportes_ia`) y en el PDF `clinical-record.pdf`. Los pendientes y los rechazados quedan en la base y en la auditoría, pero no en la historia.

### Por qué la IA no puede aprobar

- Las rutas de aprobación y rechazo exigen el rol `medico` o `especialista`. Administrador, coordinador, paciente y contable reciben 403; el modelo no tiene cuenta y no puede llamar la ruta.
- El reporte guarda quién lo firmó (`reviewed_by`, `reviewed_at`) y el PDF muestra su nombre.
- El payload, la respuesta del modelo y la versión son **inmutables** tras la creación (una guarda del ORM rechaza cualquier cambio). Lo único que cambia en un reporte es su estado de firma.
- No existe ruta `DELETE`: un reporte rechazado queda como evidencia de lo que el modelo dijo y de quién discrepó.
- **Efecto sobre las camas.** Al aprobar un reporte de criticidad, la probabilidad aprobada pasa a ser el `criticality_score` de la solicitud de cama abierta del paciente (la cola ya ordena por prioridad, luego por ese puntaje y luego por antigüedad). Un reporte pendiente o rechazado nunca toca la cola. La auditoría de la aprobación registra la solicitud afectada y el puntaje escrito.
- Si el servicio `ml` no responde, la solicitud de análisis devuelve 503 con un mensaje claro y **no se guarda nada** (ni reporte ni auditoría).

## Minimización de datos (R12)

La función `build_criticality_payload` (`app/services/pseudonymization.py`) construye el payload **campo por campo** a partir de una lista de permitidos; no copia el paciente y le quita campos. Un campo nuevo en la tabla `patients` nunca llega al modelo porque ningún código lo lee.

| Variable enviada | Origen |
|---|---|
| `pseudonym` | `pz-` + 16 hex de HMAC-SHA256(`PSEUDONYM_KEY`, id del paciente) |
| `age` | Años cumplidos (entero), calculados desde la fecha de nacimiento; la fecha no se envía |
| `sex` | `male` / `female` (otros valores se omiten) |
| `temperature_c`, `heart_rate`, `resp_rate`, `o2sat`, `sbp`, `dbp` | Última observación con los códigos LOINC 8310-5, 8867-4, 9279-1, 59408-5, 8480-6 y 8462-4, en la unidad esperada |
| `acuity` | Nivel de triage ESI (1 a 5), opcional, lo informa el clínico al pedir el análisis |

**Nunca se envían:** nombre, apellido, documento, correo, teléfono, dirección, fecha de nacimiento ni el UUID real del paciente. Los valores faltantes se omiten; el servicio los imputa con la mediana de entrenamiento y devuelve la lista `imputed`.

Dos controles adicionales, uno en cada extremo:

- El servicio `ml` declara sus modelos de entrada con `extra="forbid"`: un campo desconocido (por ejemplo `name` o `document_number`) se rechaza con 422 en lugar de ignorarse.
- El payload exacto queda en `ai_reports.payload_sent` y se consulta en `GET /api/v1/ai-reports/{id}/payload`. La prueba automática crea un paciente con datos "canario", ejecuta el análisis y verifica que ninguno aparezca en el payload, en la respuesta guardada ni en lo que recibió el modelo.

El seudónimo es estable (el mismo paciente produce siempre el mismo valor, lo que permite correlacionar reportes) y unidireccional: sin `PSEUDONYM_KEY` no se puede recomputar desde un id conocido. Cambiar la clave cambia todos los seudónimos, por lo que se genera una vez por entorno.

## Modelos

Para cada tarea se entrenan y comparan **ocho modelos supervisados** con el mismo protocolo, y el servicio `ml` atiende al ganador. Las semillas son fijas (2026) y todo se entrena al construir la imagen (`RUN python train.py`, unos 3 minutos en el servidor del proyecto), de modo que cada imagen lleva exactamente los modelos que describe su `metrics.json`. La comparación completa se consulta en `GET /api/v1/ai/models` (sección `comparacion` de cada tarea), se ve en Indicadores → «Modelos de IA» y se genera como informe legible en `ml/REPORTE_MODELOS.md`.

### Comparación de modelos

**Modelos:** regresión logística (estandarizada, L2, C = 1), KNN (k elegido por validación cruzada entre 3, 5, 9, 15 y 25), SVM con kernel RBF (C entre 0,25, 1 y 4, probabilidades con `probability=True`), árbol de decisión (`max_depth` entre 2 y 6, hojas de al menos 5 filas), random forest (bagging, 200 árboles), gradient boosting de scikit-learn (`GradientBoostingClassifier`, profundidad 2, entre 50 y 200 árboles; no se usa XGBoost), stacking (logística + random forest + boosting, meta-modelo logístico) y una red neuronal pequeña (`MLPClassifier`, una capa de 16 neuronas, estandarizada). Las cuadrículas son pequeñas a propósito para que la construcción de la imagen sea corta.

**Protocolo (igual para todos los modelos y las dos tareas):**

1. Validación cruzada de 5 particiones repetida 3 veces, estratificada (y agrupada por paciente en criticidad), solo sobre filas reales. En criticidad el aumento por grupos se aplica únicamente a la parte de entrenamiento de cada partición.
2. Los hiperparámetros se ajustan **dentro** de cada partición de entrenamiento (validación cruzada anidada de 3 particiones internas, por AUC): la partición que se evalúa nunca influye en el ajuste.
3. El **umbral de cada modelo se elige con las predicciones fuera de partición** (nunca con un modelo ajustado sobre todos los datos): el umbral más alto cuya sensibilidad fuera de partición alcanza el objetivo de la tarea (0,85 en criticidad, 0,80 en limpieza). Esto corrige la debilidad de la tarea de la semana 9, donde el umbral se tomaba sobre el conjunto de prueba.
4. Por modelo y tarea se informan el AUC (media ± desviación entre repeticiones), el F1, la precisión, la sensibilidad y la especificidad al umbral elegido, la **matriz de confusión** fuera de partición (promedio por repetición) y el tiempo de ajuste.

**Regla del ganador.** La métrica principal es el **F1** fuera de partición al umbral elegido (con desbalance, la exactitud engaña: un modelo que marcara a todos tendría exactitud alta). Entre los modelos que cumplen la sensibilidad objetivo gana el de mayor F1; si otro queda a menos de **un error estándar** del F1 del mejor (error estándar de los F1 por partición), la diferencia no se distingue del ruido y gana el más simple y explicable (orden: regresión logística, árbol, KNN, random forest, boosting, SVM, red neuronal, stacking). La regla y su motivo quedan en `metrics.json` (`comparacion.rule` y `comparacion.decision`). Nota de honestidad: una primera corrida con un margen fijo de 0,01 de F1 elegía un árbol de decisión con el AUC más bajo (0,556); se cambió a la regla del error estándar, que no depende de un número arbitrario.

**Importancia de variables.** Importancia por permutación sobre filas fuera de partición (caída del AUC al mezclar una variable, bloque completo para las categóricas), para el mejor modelo basado en árboles, y **razones de momios** de la regresión logística: `exp(coeficiente)` por desviación estándar para las variables numéricas y, para una categoría, la razón de estar en ella frente a no estarlo. Los **momios** (odds) son la probabilidad de que ocurra el evento dividida entre la de que no ocurra; la **razón de momios** compara los momios entre dos situaciones (1 = sin efecto).

**Qué recibe el reporte.** Si el ganador es la regresión logística, `top_features` trae la contribución exacta `coeficiente × valor estandarizado` (log-odds). Si el ganador fuera cualquier otro modelo, el servicio calcula una explicación **local** por oclusión: cuánto cambia la probabilidad de ese paciente al reemplazar cada variable por su valor típico de entrenamiento (positivo = sube la probabilidad). El campo `explanation` de la respuesta dice cuál se usó, y `model_version` lleva la familia del modelo (por ejemplo `criticality-lr-1.0-…`).

### 1. Criticidad del paciente

**Pregunta:** ¿el paciente necesitará ingreso hospitalario con urgencia? **Etiqueta:** `admitido` (disposición `ADMITTED` del servicio de urgencias).

**Datos:** 192 estancias reales de 51 pacientes (MIMIC-IV-ED Demo v2.2, las mismas de la cohorte de `docs/DATASETS.md`), 66,1 % de ingresos. Variables: edad, sexo, temperatura, frecuencia cardíaca, frecuencia respiratoria, SpO2, presión sistólica y diastólica y nivel ESI. Edad y sexo salen de las tablas públicas `patients` y `edstays`; el dolor no se usa porque el sistema no lo registra como observación LOINC. Los CSV de entrenamiento no contienen identificadores (`ml/data/criticality_triage.csv`).

**Método de evaluación (solo filas reales):** el de la sección anterior, con partición **agrupada por paciente** (todas las visitas de una persona caen en la misma partición; sin eso, el modelo vería a la misma persona en entrenamiento y en prueba). El aumento dentro de cada grupo (k-means con k = 3 y vecinos del mismo grupo, 2 filas sintéticas por fila real) se aplica **solo a la parte de entrenamiento** de cada partición; nunca se evalúa sobre filas sintéticas. El modelo desplegado se ajusta con las 192 filas reales más 384 sintéticas.

**Comparación** (fuera de partición, al umbral de cada modelo; sensibilidad objetivo 0,85; TN, FP, FN y TP son conteos promedio por repetición sobre las 192 visitas):

| Modelo | AUC | F1 | Precisión | Sensibilidad | Especificidad | Umbral | TN | FP | FN | TP |
|---|---|---|---|---|---|---|---|---|---|---|
| **Regresión logística (ganador)** | 0,623 ± 0,038 | 0,746 | 0,666 | 0,850 | 0,164 | 0,415 | 10,7 | 54,3 | 19,0 | 108,0 |
| Árbol de decisión | 0,556 ± 0,017 | 0,750 | 0,669 | 0,856 | 0,169 | 0,435 | 11,0 | 54,0 | 18,3 | 108,7 |
| KNN | 0,585 ± 0,018 | 0,753 | 0,671 | 0,861 | 0,174 | 0,330 | 11,3 | 53,7 | 17,7 | 109,3 |
| Random forest (bagging) | 0,576 ± 0,035 | 0,760 | 0,686 | 0,853 | 0,236 | 0,490 | 15,3 | 49,7 | 18,7 | 108,3 |
| Gradient boosting | 0,581 ± 0,025 | 0,752 | 0,674 | 0,850 | 0,195 | 0,465 | 12,7 | 52,3 | 19,0 | 108,0 |
| SVM (kernel RBF) | 0,538 ± 0,021 | 0,756 | 0,680 | 0,853 | 0,215 | 0,365 | 14,0 | 51,0 | 18,7 | 108,3 |
| Red neuronal (MLP) | 0,616 ± 0,034 | 0,753 | 0,675 | 0,850 | 0,200 | 0,430 | 13,0 | 52,0 | 19,0 | 108,0 |
| Stacking (LR + RF + GB) | 0,573 ± 0,035 | 0,759 | 0,684 | 0,853 | 0,231 | 0,430 | 15,0 | 50,0 | 18,7 | 108,3 |

**Por qué ganó la regresión logística.** El F1 más alto es el del random forest (0,760), pero la diferencia con la logística (0,746) es de 0,014, menor que un error estándar del F1 (0,023): todos los modelos quedan entre 0,746 y 0,760, un empate práctico. Con empate gana el más simple y explicable, y además la logística tiene el AUC más alto de los ocho (0,623). Todos los modelos cumplen la sensibilidad objetivo por construcción del umbral, y ninguno supera el F1 de la línea base «marcar a todos» (0,796): con 66 % de ingresos, marcar a todos ya da sensibilidad 1,0 y precisión 0,661. Lo que aportan los modelos no es F1 sino descartar algunos pacientes (especificidad de 0,16 a 0,24).

**Matriz de confusión del ganador** (promedio por repetición, umbral 0,415):

| | Marcado crítico | No marcado |
|---|---|---|
| Era crítico (127) | 108,0 críticos detectados | 19,0 críticos perdidos |
| No era crítico (65) | 54,3 falsas alarmas | 10,7 descartados correctamente |

**Resultados del ganador:** AUC **0,623** (desviación 0,038 entre repeticiones); sin aumento sintético (ablación) 0,634; umbral clínico **0,415**; sensibilidad 0,850, precisión 0,666, F1 0,746, especificidad 0,164; fracción de pacientes marcados como críticos 0,845.

**Variables que más pesan.** Importancia por permutación del random forest (caída del AUC): presión sistólica 0,046, temperatura 0,021, frecuencia respiratoria 0,006, saturación de oxígeno 0,002, edad −0,001 (ruido). Razones de momios de la regresión logística, por desviación estándar: saturación de oxígeno 0,59 (más saturación, los momios de ser crítico bajan 41 %), presión sistólica 0,60, nivel ESI 0,63 (un ESI más alto es menos urgente), temperatura 1,29 (suben 29 %) y frecuencia respiratoria 1,21. Los signos coinciden con la clínica.

**Lectura honesta.** El modelo tiene una señal débil: con solo signos vitales de triage, edad y sexo en 192 filas, el AUC ronda 0,62 y ningún modelo, sencillo o complejo, lo mejora de forma distinguible (con 192 filas un modelo más flexible no tiene de dónde aprender). A su umbral, la precisión (0,666) apenas supera la de marcar a todos como críticos (0,661), y el 85 % de los pacientes queda marcado. El aumento sintético no mejora el AUC (0,623 con él, 0,634 sin él, dentro del ruido); se mantiene porque es el método del proyecto y se reporta la ablación. Por todo esto el reporte es un **apoyo a la priorización que un clínico firma**, no un diagnóstico, y el puntaje solo desempata dentro de la misma prioridad clínica. Con valores claramente graves el modelo sí separa: SpO2 86 %, FC 118, FR 28, PAS 92, temperatura 38,9 °C, 78 años da una probabilidad de 0,999, contra 0,272 para un paciente de 40 años con signos normales (FC 75, FR 14, SpO2 99 %, PAS 120, PAD 78, 36,6 °C, ESI 4).

### Justificación clínica del umbral

El error más costoso en este uso es **no marcar** a un paciente que sí necesita una cama con urgencia: es una demora que puede significar deterioro. Una falsa alarma cuesta una revisión adicional del clínico, que de todas formas debe firmar el reporte. Por eso el umbral no se deja en 0,5: se toma **el umbral más alto cuya sensibilidad en validación cruzada alcanza 0,85** (resultado 0,415, sensibilidad 0,850). El costo es la baja especificidad (0,164) descrita arriba. El umbral se elige sobre las predicciones fuera de partición (nunca sobre un modelo ajustado con todos los datos), pero son las mismas con que se informa la sensibilidad, por lo que la sensibilidad objetivo se cumple por construcción y esa cifra es ligeramente optimista; con un parámetro y 192 filas el efecto es pequeño pero no nulo. El umbral queda en `metrics.json` y es el que alimenta el campo `critico` del reporte, punto de enganche de las alertas críticas (R20). **Contrato `paciente_critico`:** los valores que garantizan el disparo con este modelo son SpO2 86 %, FC 118, FR 28, PAS 92, PAD 58, temperatura 38,9 °C y 78 años (probabilidad ≈ 0,999, contra el umbral 0,415 que se lee de `GET /api/v1/ai/models`); el JSON exacto para crear al paciente y sus observaciones está en `docs/EVENTOS.md`.

### Sesgo por subgrupo (R22)

Sensibilidad, precisión y AUC por subgrupo, al umbral 0,415, sobre las filas reales (media de las 3 repeticiones):

| Subgrupo | n | Críticos reales | Sensibilidad | Precisión | AUC | Marcados |
|---|---|---|---|---|---|---|
| Mujeres | 106 | 66 | 0,828 | 0,636 | 0,650 | 0,811 |
| Hombres | 86 | 61 | 0,874 | 0,699 | 0,575 | 0,888 |
| < 45 años | 14 | 9 | 0,889 | 0,690 | 0,785 | 0,833 |
| 45–64 años | 117 | 74 | **0,797** | 0,617 | **0,523** | 0,818 |
| ≥ 65 años | 61 | 44 | 0,932 | 0,746 | 0,774 | 0,902 |

- **Edad 45–64:** es el subgrupo más grande y donde peor funciona (AUC 0,523, casi azar; sensibilidad 0,797, por debajo del objetivo de 0,85). Un paciente de esa edad que sí necesita cama tiene más probabilidad de no ser marcado.
- **Sexo:** la sensibilidad es menor en mujeres (0,828) que en hombres (0,874), y el modelo marca más a los hombres (89 % contra 81 %); la diferencia puede deberse en parte a la tasa de ingreso (71 % en hombres, 62 % en mujeres).
- **Menores de 45:** solo 14 filas; las cifras no son concluyentes.
- **Acción:** mientras no se amplíe la muestra, el reporte no se usa como único criterio, y toda decisión pasa por la firma clínica.

### 2. Retraso de limpieza

**Pregunta:** cuando una cama entra a *En limpieza*, ¿estará lista en más de 90 minutos (el doble del estándar de 40 a 45 minutos de alta a cama lista)? **Datos:** 800 eventos simulados con el generador de la tarea de la semana 9 (semilla 2026; `ml/tools/generate_cleaning_events.py`), 20,6 % de retrasos. No hay un conjunto público colombiano de limpieza de camas; los parámetros salen de la literatura (CDC, AHE) y lo que no tiene fuente está marcado como supuesto del equipo en el generador.

**Variables operativas, sin datos del paciente:** servicio, motivo de la liberación, aislamiento, hora, día de la semana, turno, horas de estancia previa, personal de aseo en el turno, camas pendientes de limpieza, ocupación del servicio, cambio de cortinas y paciente en espera. Las que el sistema no conoce al momento de la transición (personal, aislamiento, cortinas) se imputan y se informan.

**Comparación** (estratificada de 5 particiones repetida 3 veces; umbral por modelo con sensibilidad fuera de partición ≥ 0,80; conteos promedio por repetición sobre los 800 eventos):

| Modelo | AUC | F1 | Precisión | Sensibilidad | Especificidad | Umbral | TN | FP | FN | TP |
|---|---|---|---|---|---|---|---|---|---|---|
| **Regresión logística (ganador)** | 0,823 ± 0,002 | 0,525 | 0,389 | 0,806 | 0,671 | 0,150 | 426,0 | 209,0 | 32,0 | 133,0 |
| Árbol de decisión | 0,778 ± 0,003 | 0,487 | 0,349 | 0,806 | 0,609 | 0,115 | 386,7 | 248,3 | 32,0 | 133,0 |
| KNN | 0,783 ± 0,003 | 0,507 | 0,368 | 0,814 | 0,636 | 0,160 | 404,0 | 231,0 | 30,7 | 134,3 |
| Random forest (bagging) | 0,808 ± 0,004 | 0,520 | 0,384 | 0,804 | 0,665 | 0,180 | 422,3 | 212,7 | 32,3 | 132,7 |
| Gradient boosting | 0,807 ± 0,000 | 0,507 | 0,370 | 0,806 | 0,643 | 0,145 | 408,3 | 226,7 | 32,0 | 133,0 |
| SVM (kernel RBF) | 0,770 ± 0,007 | 0,478 | 0,339 | 0,810 | 0,590 | 0,140 | 374,3 | 260,7 | 31,3 | 133,7 |
| Red neuronal (MLP) | 0,812 ± 0,004 | 0,521 | 0,386 | 0,802 | 0,669 | 0,155 | 424,7 | 210,3 | 32,7 | 132,3 |
| Stacking (LR + RF + GB) | 0,816 ± 0,003 | 0,521 | 0,385 | 0,802 | 0,668 | 0,135 | 424,0 | 211,0 | 32,7 | 132,3 |

**Por qué ganó la regresión logística.** Tiene el F1 más alto (0,525; error estándar 0,013) y también el AUC más alto; es además la más simple, así que no hace falta recurrir al desempate. Aquí sí hay señal que aprovechar: todos los modelos superan la línea base de marcar a todos (F1 0,342, precisión 0,206). Los modelos de ensamble (random forest 0,520, MLP 0,521, stacking 0,521) quedan dentro del ruido. Como los datos son simulados, el resultado describe el generador y no demuestra que la logística sea mejor con datos reales.

**Matriz de confusión del ganador** (promedio por repetición, umbral 0,15):

| | Marcado con retraso | No marcado |
|---|---|---|
| Hubo retraso (165) | 133,0 retrasos detectados | 32,0 retrasos perdidos |
| A tiempo (635) | 209,0 falsas alarmas | 426,0 descartados correctamente |

**Resultados del ganador:** AUC **0,823** (desviación 0,002); umbral 0,15; sensibilidad 0,806, precisión 0,389, F1 0,525, especificidad 0,671; fracción marcada 0,428.

**Variables que más pesan.** Importancia por permutación del random forest (caída del AUC): aislamiento 0,091, servicio 0,038, cambio de cortinas 0,018, personal de limpieza en turno 0,010, camas pendientes 0,007. Razones de momios de la logística: por cada desviación estándar más de camas pendientes de limpieza, los momios de retraso suben 66 % (1,66); estar en aislamiento por esporas multiplica los momios por 6,65 frente a no estarlo; estar en UCI Adultos, por 2,34; estar en Urgencias, por 0,25 (bajan 75 %); el cambio de cortinas, 1,42 por desviación estándar.

Las métricas describen la simulación, no hospitales reales. Una alerta de cama tarde es barata de descartar, pero se prefiere detectar la mayoría de los retrasos; por eso el umbral baja a 0,15.

**Sesgo por subgrupo:**

| Subgrupo | n | Tardías | Sensibilidad | Precisión | AUC |
|---|---|---|---|---|---|
| Hospitalización | 401 | 54 | 0,636 | 0,289 | 0,755 |
| UCI Adultos | 271 | 105 | 0,930 | 0,463 | 0,787 |
| Urgencias | 128 | 6 | **0,167** | 0,084 | 0,712 |
| Turno mañana | 396 | 76 | 0,789 | 0,371 | 0,812 |
| Turno tarde | 326 | 56 | 0,738 | 0,355 | 0,813 |
| Turno noche | 78 | 33 | 0,960 | 0,495 | 0,794 |

El modelo casi no detecta retrasos en Urgencias (6 casos en total, sensibilidad 0,17) y detecta menos en Hospitalización que en UCI. La causa es la poca frecuencia de retrasos en esos servicios, no una regla del modelo; no debe usarse como alerta en Urgencias hasta contar con datos reales.

**Uso en el sistema.** Cuando una cama pasa a *En limpieza* (por liberación de la asignación o por cambio manual de estado), la API consulta el modelo con las variables operativas y guarda probabilidad, bandera (`predicted_delay`) y versión en el evento de la cama; el registro de limpieza la copia al crearse, y `GET /api/v1/beds/cleaning/overdue` la expone. Es de mejor esfuerzo: si el servicio no responde, la cama cambia de estado igual y los campos quedan nulos.

## Auditoría

| Acción | Cuándo | Metadatos |
|---|---|---|
| `AI_ANALYSIS` | Se crea un reporte pendiente | `report_id`, `patient_id`, seudónimo, versión del modelo, probabilidad |
| `REPORT_APPROVED` | Firma de aprobación | `report_id`, `patient_id`, solicitud de cama y puntaje escritos, si hubo |
| `REPORT_REJECTED` | Firma de rechazo | `report_id`, `patient_id`, motivo |
| `RECORD_VIEWED` | Se consulta la historia en JSON | `patient_id`, ids de los reportes incluidos |
| `CLINICAL_RECORD_EXPORTED` | Se descarga el PDF (incluye al propio paciente) | `patient_id`, ids de los reportes incluidos |

## Correspondencia con la normativa

| Control técnico | Norma | Qué responde |
|---|---|---|
| Lista de permitidos, seudónimo HMAC, payload registrado, `extra="forbid"` en el servicio | Ley 1581 de 2012, principios de finalidad y de circulación restringida (art. 4, literales b y f) | Al modelo solo llegan las variables necesarias para el fin declarado, sin datos que identifiquen a la persona |
| Servicio `ml` sin base de datos ni puertos públicos | Ley 1581 de 2012, principio de seguridad (art. 4, literal g) | El tratamiento externo no puede leer más de lo que la API le entrega |
| Reporte pendiente, firma por médico o especialista, rechazo con evidencia | Resolución 1995 de 1999 (la historia clínica es un documento del profesional; contenido, integridad y reserva) | Solo entra a la historia lo que un profesional revisó y firmó |
| Historia con datos del paciente, observaciones y reportes aprobados; descarga auditada | Resolución 866 de 2021 (conjunto de datos clínicos y su intercambio) y Resolución 1995 de 1999 | El contenido exportable es el aprobado, con trazabilidad de quién y cuándo |
| Auditoría de análisis, firma y exportación | Ley 2015 de 2020 (historia clínica electrónica interoperable), trazabilidad | Cada paso deja usuario, acción, recurso y fecha |

Las citas indican a qué se orienta cada control y no constituyen una opinión jurídica; la verificación de cumplimiento le corresponde al responsable del tratamiento.

## Limitaciones

- 192 filas de un solo hospital de Estados Unidos: el modelo de criticidad no está calibrado para la población colombiana ni validado clínicamente. Con tan pocas filas, las diferencias de F1 entre los ocho modelos están dentro del ruido (error estándar de 0,02), así que el ganador se decide por simplicidad y no por rendimiento.
- Los hiperparámetros se ajustaron con cuadrículas pequeñas y la comparación usa 3 repeticiones de validación cruzada, para que la imagen se construya en pocos minutos; con más repeticiones o cuadrículas más amplias los números cambiarían ligeramente. Las probabilidades de KNN, árbol y SVM no están calibradas.
- El umbral y las métricas al umbral salen de las mismas predicciones fuera de partición; la sensibilidad objetivo se cumple por construcción. Una estimación sin ese sesgo exigiría una partición externa adicional.
- La explicación de un modelo distinto de la logística (oclusión) es local y aproximada: no suma como las contribuciones lineales ni detecta interacciones entre variables.
- `anchor_age` de MIMIC es la edad en el año ancla del paciente, no en la fecha exacta de cada visita; la edad tiene un error de pocos años.
- El modelo de limpieza se entrena y se evalúa con datos simulados.
- La imputación de variables no registradas (por ejemplo el nivel ESI) reduce la información de la predicción; el reporte lista siempre qué se imputó.
- El campo `critico` del reporte dispara la alerta crítica (R20) en cuanto el análisis crea el reporte (no al aprobarlo). Con el 85 % de los pacientes marcados como críticos, la alerta se limita a una activa por paciente. Los valores clínicos que garantizan el disparo, el procedimiento de reconocimiento y el escalamiento están en `docs/EVENTOS.md` (sección «Contrato `paciente_critico`»).
