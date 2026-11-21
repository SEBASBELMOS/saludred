# Conjuntos de datos

Este documento describe los datos reales que alimentan SaludRed (requisito R07): la cohorte tabular de triage y las radiografías de tórax cargadas en el PACS. Los datos sintéticos de la red hospitalaria (pacientes, camas, solicitudes) siguen generándose con `scripts/seed.py` y no se tratan aquí.

## Tabular

**Nombre:** MIMIC-IV-ED Demo v2.2 (tablas `triage` y `edstays`).

**Fuente:** https://physionet.org/content/mimic-iv-ed-demo/2.2/ (archivos `ed/triage.csv.gz` y `ed/edstays.csv.gz`, descargables sin cuenta).

**Licencia:** Open Data Commons Open Database License v1.0 (ODbL 1.0). Acceso abierto, sin credencialización.

**Cita requerida:** Johnson, A., Bulgarelli, L., Pollard, T., Celi, L. A., Horng, S., & Mark, R. (2023). MIMIC-IV-ED Demo (version 2.2). PhysioNet. https://doi.org/10.13026/jzz5-vs76

La página de PhysioNet puede pedir además citar a Goldberger et al. (2000), *PhysioBank, PhysioToolkit, and PhysioNet*, Circulation 101(23):e215-e220. Esta cita adicional no se verificó en la página de la versión; confirmarla antes de la entrega.

### Registros disponibles

Datos reales y desidentificados del servicio de urgencias del Beth Israel Deaconess Medical Center. Medido sobre los archivos descargados:

- 222 estancias en urgencias de 64 pacientes distintos (la página del dataset menciona 100 pacientes; los archivos contienen 64 `subject_id` únicos, y se usan las cifras medidas).
- El cruce de `triage` con `edstays` por (`subject_id`, `stay_id`) da 222 filas, una por estancia.
- `disposition`: ADMITTED 150, HOME 60, TRANSFER 5, OTHER 2, LEFT AGAINST MEDICAL ADVICE 2, LEFT WITHOUT BEING SEEN 2, ELOPED 1.
- Nulos en `triage`: temperatura 26, FC 24, FR 23, SpO2 24, PAS 23, PAD 23, dolor 21, `acuity` 15.

### Por qué responde al problema

Contiene los signos vitales de triage (temperatura, frecuencia cardíaca, frecuencia respiratoria, saturación, presión sistólica y diastólica, dolor), el nivel de urgencia ESI (`acuity`, 1 = más urgente) y el desenlace (`disposition`: ingreso hospitalario o alta). Con eso se puede describir qué tan cerca está un paciente del caso crítico y estimar la probabilidad de que necesite cama, que es la decisión que la red debe coordinar.

### Cómo se usa

Se construye con `python -m scripts.build_cohort` (módulo `app/services/cohort_builder.py`, Python puro):

1. **Limpieza.** Se descartan las filas sin temperatura, FC, FR, SpO2, PAS, PAD o `acuity`, las que quedan fuera de rangos clínicos rígidos y las que tienen PAS ≤ PAD. Cada descarte se cuenta por motivo y se reporta.
   - Rangos: los de plausibilidad del modelo clínico (`PLAUSIBLE_RANGE`: FC 30–220, FR 6–60, SpO2 50–100, PAS 50–260, PAD 30–150), temperatura 33–42 °C y `acuity` entera entre 1 y 5.
   - El dolor (`pain`) mezcla números y texto: se convierte a entero 0–10 y, si no se puede, queda nulo. No es criterio de descarte.
2. **Conversión de temperatura.** MIMIC guarda la temperatura en grados Fahrenheit; se convierte a Celsius con `(F − 32) × 5/9`, redondeada a 0,1.
3. **Etiqueta.** `admitido = (disposition == "ADMITTED")`.
4. **Agrupamiento.** k-means (k-means++, 8 reinicios, semilla 2026) sobre temperatura, FC, FR, SpO2, PAS, PAD y ESI estandarizados. Los grupos se ordenan por tasa de ingreso y se leen clínicamente: **lejos**, **intermedio** o **cerca** del caso crítico. Cada grupo guarda una descripción en español generada desde su centroide y su tasa de ingreso; el grupo intermedio, cuando ronda el 50 % de ingresos, se marca como zona de confusión, donde el modelo se equivoca más.
5. **Aumento dentro de cada grupo (tipo SMOTE).** Para cada fila sintética se elige una fila real del grupo como base y uno de sus 5 vecinos reales más cercanos del **mismo** grupo (distancia estandarizada); con u ~ U(0, 1) cada variable es `base + u · (vecino − base)`. Luego se redondea a la precisión clínica (temperatura 0,1; el resto enteros), se recorta a los rangos rígidos, se exige PAS > PAD (si no, se conservan los valores de la base) y la etiqueta es la de la base si u < 0,5 o la del vecino si no. Por defecto se generan 2 filas sintéticas por cada fila real de cada grupo. Un candidato idéntico a una fila real se descarta y se vuelve a muestrear; el resumen reporta la distancia mínima y mediana de las filas sintéticas a la fila real más cercana.
6. **Trazabilidad.** Cada fila queda en `cohort_records` con `origin` = `REAL` o `SYNTHETIC` y su grupo (`cluster_id`). En las reales, `source_ref` es `mimic-iv-ed-demo:stay_id=<id>`; en las sintéticas, `base=<uuid>;vecino=<uuid>;u=<0.xx>`. Los grupos, con su descripción, centroide, tasa de ingreso y silueta, están en `cohort_clusters` y se consultan en `GET /api/v1/cohort/clusters`.

### Número de grupos

Se usa **k = 3** porque el planteamiento del curso pide tres zonas clínicas: lejos, intermedia y cerca del caso crítico. No se toma como verdad por decreto: el script calcula la silueta para k = 2 a 6 y la inercia (codo) para k = 1 a 6, y las guarda en el resumen JSON. Si la silueta de k = 3 no es la mayor, se mantiene k = 3 por la interpretación clínica y se declara aquí la diferencia.

**Valores de la ejecución del 30/09/2026** (semilla 2026, sobre las 192 visitas reales que pasaron la limpieza):

| k | Silueta | Inercia |
|---|---------|---------|
| 1 | no aplica | 1344,0 |
| 2 | **0,173** (la mayor) | 1134,1 |
| 3 (usado) | 0,141 | 993,8 |
| 4 | 0,143 | 897,3 |
| 5 | 0,141 | 816,5 |
| 6 | 0,129 | 765,5 |

**Lectura honesta:**
- **La silueta es baja en todos los k (0,13 a 0,17):** los signos vitales de urgencias se solapan mucho y no forman grupos nítidos.
- **La mejor silueta es la de k = 2,** pero la diferencia con k = 3 es pequeña (0,17 contra 0,14).
- **La curva de inercia no muestra un codo claro:** baja de forma gradual.

Con esa estructura débil, se mantiene **k = 3** por dos razones:
- responde a las tres zonas clínicas que pide el planteamiento;
- ordena bien el riesgo: la tasa de ingreso sube de un grupo al siguiente, del 52 % al 71 % y al 84 %.

**Resultado por grupo:**

| Grupo | Zona | Filas reales | Filas sintéticas | Ingreso (reales) | Descripción generada |
|---|---|---|---|---|---|
| 1 | Lejos del caso crítico | 77 | 154 | 52 % | SpO2 media 99 %, FC 89, FR 17, PAS 154, ESI medio 2,7 |
| 2 | Intermedio | 72 | 144 | 71 % | SpO2 media 98 %, FC 82, FR 17, PAS 121, ESI medio 2,3. **Zona de transición:** sus signos se parecen a los de los grupos vecinos y el modelo puede confundirse cerca de esas fronteras |
| 3 | Cerca del caso crítico | 43 | 86 | 84 % | SpO2 media 96 %, **FC 109**, FR 20, PAS 134, ESI medio 2,1 |

**Qué se descartó y por qué:** de las 222 visitas se conservaron 192. Se descartaron 30:
- 26 no tenían temperatura;
- 1 no tenía frecuencia cardíaca;
- 1 no tenía saturación;
- 1 tenía una presión diastólica fuera de rango;
- 1 tenía una temperatura fuera de rango.

**Control de privacidad:** ninguna fila sintética es idéntica a una real. La distancia estandarizada a la fila real más cercana es de 0,036 como mínimo y 0,405 de mediana. Se volvieron a muestrear 35 candidatos que quedaban idénticos.

**Una observación sobre el grupo "lejos":** aun ahí, la mitad de los pacientes terminó hospitalizada. La muestra viene de urgencias de un hospital de alta complejidad, donde la mayoría de los pacientes está grave: el 66 % del total fue hospitalizado. Por eso "lejos del caso crítico" significa "menos crítico que los demás", no "sano".

### Uso en el modelo de criticidad (`ml/`)

El servicio `ml` entrena su modelo de criticidad del paciente con las mismas 192 visitas reales de la cohorte (la limpieza de `app/services/cohort_builder.py`), exportadas sin identificadores a `ml/data/criticality_triage.csv` con `ml/tools/export_triage.py`. La etiqueta es `admitido`.

- **Columnas exportadas:** `group` (índice opaco por paciente, solo para que la validación cruzada mantenga juntas las visitas de una misma persona), `age`, `sex`, `temperature_c`, `heart_rate`, `resp_rate`, `o2sat`, `sbp`, `dbp`, `acuity`, `admitted`. No se exportan `subject_id`, `stay_id`, fechas ni texto libre (`chiefcomplaint`).
- **Edad y sexo.** El sexo viene de `edstays.gender`. La edad viene de la tabla `patients` de **MIMIC-IV Clinical Database Demo v2.2** (módulo `hosp`, columna `anchor_age`, https://physionet.org/content/mimic-iv-demo/2.2/), que se une por `subject_id`. Comparte licencia (ODbL 1.0) y acceso abierto con el demo de urgencias. Cita: Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023). MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet. https://doi.org/10.13026/dp1f-ex47 (DOI verificado el 03/10/2026 en la página de la versión en PhysioNet). `anchor_age` es la edad en el año ancla del paciente, no en la fecha de cada visita.
- **Resultado del cruce:** las 192 filas tienen edad y sexo; son 51 pacientes distintos, con 127 ingresos (66,1 %).
- **Atribución.** Los datos derivados (el CSV de entrenamiento y los modelos entrenados con él) se distribuyen bajo la misma ODbL 1.0 y conservan las citas de arriba.
- **Aumento.** Dentro de cada partición de entrenamiento se aplica el aumento por grupos descrito en el punto 5 (k-means con k = 3 sobre edad y signos vitales, vecinos del mismo grupo). Se aplica solo al entrenamiento: las métricas se calculan con filas reales y con validación cruzada agrupada por paciente. Detalle y resultados en `docs/IA.md`.

## Limpieza de camas (simulado)

**Nombre:** eventos de limpieza terminal de camas, 800 filas (`ml/data/cleaning_events.csv`).

**Origen:** simulados con `ml/tools/generate_cleaning_events.py` (semilla 2026), el generador de la tarea de la semana 9 con nombres de columna en inglés; produce las mismas filas. No existe un conjunto público de limpieza de camas en Colombia. Los parámetros (tiempos base de limpieza por servicio, minutos extra por aislamiento y cortinas, estándar de 40 a 45 minutos de alta a cama lista, corte de 250 RLU de ATP) salen de la literatura citada en la tarea (CDC, AHE); lo que no tiene fuente está marcado en el generador como supuesto del equipo.

**Etiqueta:** `late` = la cama tardó más de 90 minutos en estar lista (20,6 % de los eventos). Las columnas posteriores a la liberación (espera, minutos de limpieza, ATP) se generan para la simulación pero no se guardan en el CSV de entrenamiento: serían fuga de información.

**Limitación:** todo el conjunto es simulado, por lo que las métricas del modelo describen la simulación y no hospitales reales.

## Imagen

**Nombre:** NIH ChestX-ray14, subconjunto de radiografías de tórax de adultos (espejo Hugging Face `chehablab/NIHChestXR`, shard 0).

**Fuente:** original del NIH Clinical Center, https://nihcc.app.box.com/v/ChestXray-NIHCC; espejo en formato parquet, https://huggingface.co/datasets/chehablab/NIHChestXR (archivo `data/train-00000-of-00006.parquet`, unos 417 MB).

**Licencia:** el NIH declara que no hay restricciones de uso de las imágenes, con atribución obligatoria (enlazar la fuente, citar el artículo y reconocer al NIH Clinical Center como proveedor). El espejo declara CC0 1.0. El texto de licencia en la página primaria del NIH no se pudo verificar directamente.

**Cita requerida:** Wang, X., Peng, Y., Lu, L., Lu, Z., Bagheri, M., & Summers, R. M. (2017). ChestX-Ray8: Hospital-Scale Chest X-Ray Database and Benchmarks on Weakly-Supervised Classification and Localization of Common Thorax Diseases. IEEE CVPR 2017. https://doi.org/10.1109/cvpr.2017.369. Datos proporcionados por el NIH Clinical Center; el espejo pide además reconocer a Chehab Lab.

### Registros disponibles

El original reúne 112.120 radiografías frontales de 30.805 pacientes con 14 etiquetas más "No Finding". El shard 0 contiene 18.687 imágenes (edad de 2 a 94 años, mediana 50) de 224×224 píxeles en PNG. Se filtran las de edad mayor o igual a 18 años.

**Imágenes cargadas (30/09/2026):** **60**, todas subidas con éxito (60 respuestas 201) a través de la API, así que pasaron por las mismas reglas que cualquier subida: validación del tipo por sus bytes, identidad del paciente reescrita en el PACS y auditoría.
- **30 con hallazgos críticos:** la etiqueta incluye neumonía, derrame, edema, consolidación o cardiomegalia.
- **30 normales:** solo "No Finding".
- **De dónde salen:** se eligieron al azar, con semilla 2026, entre las 17.806 radiografías de adultos del shard 0.
- **A qué estudios van:** a 60 de los 189 estudios de tórax (modalidad CR o DX) que aún no tenían imagen. Las críticas se asignaron preferentemente a pacientes con perfil respiratorio, cardíaco o de adulto mayor frágil, y las normales a pacientes sanos o hipertensos.
- **Qué etiquetas tiene cada una:** el manifiesto `xray_manifest.csv` (en la ASUS, carpeta `checks`) dice qué radiografía de NIH quedó en qué estudio y con qué etiquetas.

**Nombres de las etiquetas:** el archivo parquet guarda las etiquetas como números, sin sus nombres. El orden se tomó de la tabla "Labels / Classes" de la ficha del dataset: 0 = No Finding, 1 = Atelectasis, 2 = Cardiomegaly, 3 = Effusion, 4 = Infiltration, 5 = Mass, 6 = Nodule, 7 = Pneumonia, 8 = Pneumothorax, 9 = Consolidation, 10 = Edema, 11 = Emphysema, 12 = Fibrosis, 13 = Pleural Thickening y 14 = Hernia. Se pasa al cargador con `--label-names`.

### Por qué responde al problema

Neumonía, derrame, edema, consolidación y cardiomegalia son hallazgos ligados a la gravedad respiratoria y a la necesidad de hospitalización. Radiografías reales de adultos aportan evidencia de imagen complementaria a los signos vitales de triage.

### Cómo se vinculan a los estudios

`scripts/load_chest_xrays.py` corre fuera de la API (necesita `pyarrow`), elige al azar con semilla fija 30 imágenes cuyas etiquetas incluyen alguno de Pneumonia, Effusion, Edema, Consolidation o Cardiomegaly, y 30 que solo tienen "No Finding". Luego busca, en modo solo lectura, estudios de imagen CR o DX sin imágenes en el PACS y sube cada imagen por `POST /api/v1/imaging-studies/{id}/images`, de modo que se aplican las reglas del PACS (reescritura de identidad, validación y auditoría). Las imágenes con hallazgos críticos se asignan de preferencia a pacientes de perfil respiratorio, cardíaco o adulto mayor frágil, y las normales a pacientes sanos o hipertensos; hay una imagen por estudio. El script escribe un manifiesto CSV con estudio, paciente, identificador NIH, etiquetas, edad, sexo y código HTTP.

### Alternativa descartada

Kermany et al. (2018), *Labeled Optical Coherence Tomography (OCT) and Chest X-Ray Images for Classification*, Mendeley Data V2, doi 10.17632/rscbjbr9sj.2 (CC BY 4.0), con etiquetas normal/neumonía asignadas por expertos y un split de prueba de solo 78 MB. Se descartó porque son radiografías pediátricas (1 a 5 años): no representan a los pacientes adultos de la red. Queda como respaldo más liviano.

## Corpus

Fuentes declaradas del asistente clínico (RAG, `docs/RAG.md`) y del agente (`docs/AGENTE.md`, herramienta `buscar_guias`). Son 64 documentos en dos carpetas bajo `rag/corpus/`, cada uno en `rag/corpus/<carpeta>/<id>.md`; el índice completo con licencia por documento está en `rag/corpus/general/SOURCES.md` y `rag/corpus/triage/SOURCES.md`. Se cargan con `python -m scripts.rag_ingest` (ver `docs/RAG.md`).

**Para qué se usa.** Es la base documental con la que el asistente y el agente responden preguntas clínicas y de proceso: cada respuesta cita el documento y el fragmento usados (`fuentes` en `POST /api/v1/agente/chat` y `POST /api/v1/rag/ask`: `doc_id`/`documento`, título, `chunk_id` y `fragmento`), y si ninguna fuente respalda la pregunta el sistema lo dice en vez de inventar. La carpeta `triage` responde al problema clínico del equipo (clasificación y criticidad en urgencias); la carpeta `general` responde al marco normativo y de proceso (referencia y contrarreferencia, habilitación, historia clínica, protección de datos, interoperabilidad, gestión de camas y limpieza, ética de la IA). Aparte existe la base `historia`: no es un corpus de documentos, sino las sugerencias de IA **aprobadas** por un clínico para un paciente, seudonimizadas, y solo la ve el paciente al que pertenecen.

Resumen por carpeta:

| Carpeta (base) | Ids | Documentos | Qué cubre |
|---|---|---|---|
| `rag/corpus/triage` | T01 a T32 | 32 | Resolución 5596 de 2015 y manual ESI v4 (AHRQ); documentación de MIMIC-IV-ED; estudios sobre Manchester, NEWS2, índice de choque y signos vitales anormales; hipoglucemia, ACV y dolor torácico; ABCDE de la OMS y el CICR |
| `rag/corpus/general` | G01 a G32 | 32 | Decretos 4747/2007, 780/2016 y 1377/2013; Resoluciones 2335/2023, 1220/2010, 3100/2019, 1995/1999 y 866/2021; Leyes 1751/2015, 1581/2012 y 2015/2020; recursos FHIR; limpieza hospitalaria; ética de la IA (OMS); flujo y ocupación de camas; compartir datos de salud |

### Carpeta `triage`

| Id | Título | Emisor | Fuente |
|---|---|---|---|
| T01 | Resolución 5596 de 2015: criterios técnicos para el Sistema de Selección y Clasificación de pacientes en los servicios de urgencias «Triage» (objeto, definición y categorías) | Ministerio de Salud y Protección Social de Colombia | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minsaludps_5596_2015.htm |
| T02 | Resolución 5596 de 2015: Triage (responsabilidades de IPS, EPS y personal) | Ministerio de Salud y Protección Social de Colombia | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minsaludps_5596_2015.htm |
| T03 | Emergency Severity Index v4, Implementation Handbook, cap. 3 (introducción y punto de decisión A: ¿el paciente se está muriendo?) | AHRQ, US Department of Health and Human Services | https://git.lsit.ucsb.edu/publicdata/ahrq-gov/raw/commit/58222b51ed2ca5fbc109e641d86b5c7039a3725c/archive.ahrq.gov/research/esi/esi3.htm |
| T04 | ESI v4, cap. 3 (punto de decisión B: ¿el paciente debe esperar?) | AHRQ | misma URL que T03 |
| T05 | ESI v4, cap. 3 (puntos de decisión C y D: recursos y signos vitales de zona de peligro) | AHRQ | misma URL que T03 |
| T06 | ESI v4, cap. 5 (necesidades esperadas de recursos) | AHRQ | https://git.lsit.ucsb.edu/publicdata/ahrq-gov/raw/commit/58222b51ed2ca5fbc109e641d86b5c7039a3725c/archive.ahrq.gov/research/esi/esi5.htm |
| T07 | ESI v4, cap. 6 (papel de los signos vitales en el triage ESI) | AHRQ | https://git.lsit.ucsb.edu/publicdata/ahrq-gov/raw/commit/58222b51ed2ca5fbc109e641d86b5c7039a3725c/archive.ahrq.gov/research/esi/esi6.htm |
| T08 | Documentación de MIMIC-IV-ED: la tabla `triage` | MIT Laboratory for Computational Physiology / PhysioNet | https://mimic.mit.edu/docs/iv/modules/ed/triage.html |
| T09 | Documentación de MIMIC-IV-ED: las tablas `vitalsign` y `edstays` | MIT Laboratory for Computational Physiology / PhysioNet | https://mimic.mit.edu/docs/iv/modules/ed/vitalsign.html |
| T10 | MIMIC-IV-ED v2.2: descripción de la base, valoración de triage y limitaciones | PhysioNet (Johnson et al.) | https://physionet.org/content/mimic-iv-ed/2.2/ |
| T11 | El triaje en los servicios de urgencias en hospitales de la subregión suroeste de Antioquia (estudio observacional descriptivo) | Iatreia (Universidad de Antioquia), SciELO Colombia | http://www.scielo.org.co/scielo.php?script=sci_arttext&pid=S0121-07932023000200158 |
| T12 | Propuesta de modelo de predicción de mortalidad complementario al triaje en un hospital universitario en Bogotá | Universitas Medica (Pontificia Universidad Javeriana), SciELO Colombia | http://www.scielo.org.co/scielo.php?pid=S2011-08392021000300004&script=sci_arttext&tlng=es |
| T13 | Validity of the Manchester Triage System in emergency care: a prospective observational study | PLoS ONE | https://pmc.ncbi.nlm.nih.gov/articles/PMC5289484/ |
| T14 | Manchester Triage System: effectiveness, limitations, and global implementation (structured narrative review) | Healthcare | https://pmc.ncbi.nlm.nih.gov/articles/PMC13512649/ |
| T15 | NEWS2 as a prognostic tool for adult patients in the emergency department (retrospective observational study) | PLOS One | https://pmc.ncbi.nlm.nih.gov/articles/PMC12169516/ |
| T16 | Evaluation of NEWS2 response thresholds in a retrospective observational study from a UK acute hospital | BMJ Open | https://pmc.ncbi.nlm.nih.gov/articles/PMC8830252/ |
| T17 | NEWS outperforms qSOFA for early detection of sepsis in the emergency department | Antibiotics | https://pmc.ncbi.nlm.nih.gov/articles/PMC9686998/ |
| T18 | Comparison of nine early warning scores for short-term mortality in acute neurological disease in the emergency department | Journal of Personalized Medicine | https://pmc.ncbi.nlm.nih.gov/articles/PMC9024907/ |
| T19 | Prognostic value of NEWS and MEWS on ICU readmission and mortality (prospective observational study) | Frontiers in Medicine | https://pmc.ncbi.nlm.nih.gov/articles/PMC9386480/ |
| T20 | Shock Index as a marker for mortality in patients admitted to the medical ICU from the emergency department | Cureus | https://pmc.ncbi.nlm.nih.gov/articles/PMC7263408/ |
| T21 | Use of the reverse shock index for identifying high-risk patients in a five-level triage system | Scandinavian Journal of Trauma, Resuscitation and Emergency Medicine | https://pmc.ncbi.nlm.nih.gov/articles/PMC4748603/ |
| T22 | Predictive and prognostic importance of NEWS2 in emergency room patients | Cureus | https://pmc.ncbi.nlm.nih.gov/articles/PMC11942526/ |
| T23 | Sepsis (hoja informativa) | Organización Mundial de la Salud | https://www.who.int/news-room/fact-sheets/detail/sepsis |
| T24 | Abnormal vital signs are strong predictors for ICU admission and in-hospital mortality in adults triaged in the emergency department | Scandinavian Journal of Trauma, Resuscitation and Emergency Medicine | https://pmc.ncbi.nlm.nih.gov/articles/PMC3384463/ |
| T25 | Triage vital signs predict in-hospital mortality among emergency department patients with acute poisoning | BMC Health Services Research | https://pmc.ncbi.nlm.nih.gov/articles/PMC3459725/ |
| T26 | Risk scores for patients with chest pain: evaluation in the emergency department | Current Cardiology Reviews | https://pmc.ncbi.nlm.nih.gov/articles/PMC3131711/ |
| T27 | Diagnostic values of chest pain history, ECG, troponin and clinical gestalt in potential acute coronary syndrome | SpringerPlus | https://pmc.ncbi.nlm.nih.gov/articles/PMC4431985/ |
| T28 | Low blood glucose (hypoglycemia) | NIDDK, National Institutes of Health | https://www.niddk.nih.gov/health-information/diabetes/overview/preventing-problems/low-blood-glucose-hypoglycemia |
| T29 | If time is brain, where is the improvement in prehospital time after stroke? | Frontiers in Neurology | https://pmc.ncbi.nlm.nih.gov/articles/PMC5701972/ |
| T30 | A fast emergency department triage score based on mobility, mental status and oxygen saturation compared with the ESI | QJM: An International Journal of Medicine | https://pmc.ncbi.nlm.nih.gov/articles/PMC10559338/ |
| T31 | Inter-rater reliability of the Glasgow Coma Scale between triage and resuscitation nurses after standardized training | Journal of Nursing Management | https://pmc.ncbi.nlm.nih.gov/articles/PMC12714156/ |
| T32 | Basic Emergency Care: approach to the acutely ill and injured (ABCDE y glosario) | OMS y Comité Internacional de la Cruz Roja | https://hlh.who.int/docs/librariesprovider4/hlh-documents/who-icrc-basic-emergency-care.pdf?sfvrsn=4460e22e_5 |

Nota: T03 a T07 son páginas archivadas de AHRQ recuperadas de un espejo público porque el servidor original no resolvía desde la máquina de construcción. Las licencias de T08 a T10 distinguen la documentación (licencia MIT) de los datos (licencia de PhysioNet): solo se usa el texto descriptivo.

### Carpeta `general`

| Id | Título | Emisor | Fuente |
|---|---|---|---|
| G01 | Decreto 4747 de 2007: objeto, campo de aplicación y definiciones (referencia y contrarreferencia) | Ministerio de la Protección Social | https://normograma.supersalud.gov.co/compilacion/docs/decreto_4747_2007.htm |
| G02 | Decreto 4747 de 2007: atención inicial de urgencias, autorizaciones, referencia y CRUE (arts. 12 a 18) | Ministerio de la Protección Social | misma URL que G01 |
| G03 | Decreto 780 de 2016: atención inicial de urgencias, referencia y contrarreferencia y CRUE (arts. 2.5.3.2.1 a 2.5.3.2.4, 2.5.3.2.16 y 2.5.3.2.17) | Ministerio de Salud y Protección Social | https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=77813 |
| G04 | Decreto 780 de 2016: atención integral, referencia y contrarreferencia y cambio de prestador (arts. 2.5.3.4.7.3 a 2.5.3.4.7.7) | Ministerio de Salud y Protección Social | misma URL que G03 |
| G05 | Resolución 2335 de 2023: informe de urgencias, autorizaciones posteriores, referencia y contrarreferencia | Ministerio de Salud y Protección Social | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minsaludps_2335_2023.htm |
| G06 | Resolución 1220 de 2010: organización y operación de los CRUE | Ministerio de la Protección Social | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minproteccion_1220_2010.htm |
| G07 | Resolución 3100 de 2019: manual de habilitación, servicio de hospitalización | Ministerio de Salud y Protección Social | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minsaludps_3100_2019.htm |
| G08 | Resolución 3100 de 2019: manual de habilitación, servicio de urgencias | Ministerio de Salud y Protección Social | misma URL que G07 |
| G09 | Ley Estatutaria 1751 de 2015: objeto, naturaleza del derecho fundamental a la salud, elementos y principios (arts. 1, 2 y 6) | Congreso de la República de Colombia | https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=60733 |
| G10 | Ley Estatutaria 1751 de 2015: integralidad, redes de servicios y prohibición de negar servicios (arts. 8, 13 y 14) | Congreso de la República de Colombia | misma URL que G09 |
| G11 | Ley 1581 de 2012: protección de datos personales, objeto, ámbito y definiciones | Congreso de la República de Colombia | https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=49981 |
| G12 | Ley 1581 de 2012: principios del tratamiento y datos sensibles (arts. 4 a 7) | Congreso de la República de Colombia | misma URL que G11 |
| G13 | Ley 1581 de 2012: derechos de los titulares, autorización y suministro de información (arts. 8 a 13) | Congreso de la República de Colombia | misma URL que G11 |
| G14 | Decreto 1377 de 2013: definiciones, autorización y revocatoria en el tratamiento de datos personales | Presidencia de la República de Colombia | https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=53646 |
| G15 | Resolución 1995 de 1999: manejo de la historia clínica (definiciones, características y obligatoriedad del registro) | Ministerio de Salud | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minsalud_r1995_99.htm |
| G16 | Resolución 1995 de 1999: custodia, acceso y seguridad del archivo de historias clínicas | Ministerio de Salud | misma URL que G15 |
| G17 | Ley 2015 de 2020: interoperabilidad de la historia clínica electrónica (objeto, definiciones y ámbito) | Congreso de la República de Colombia | https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=105472 |
| G18 | Ley 2015 de 2020: guarda y custodia, titularidad, autorización a terceros, contenido, gratuidad y autenticidad de la historia clínica electrónica | Congreso de la República de Colombia | misma URL que G17 |
| G19 | Resolución 866 de 2021: conjunto de elementos de datos clínicos relevantes para la interoperabilidad | Ministerio de Salud y Protección Social | https://normograma.supersalud.gov.co/compilacion/docs/resolucion_minsaludps_0866_2021.htm |
| G20 | Resolución 866 de 2021: titularidad, privacidad desde el diseño, seguridad y calidad del dato clínico interoperable | Ministerio de Salud y Protección Social | misma URL que G19 |
| G21 | Patient, FHIR v5.0.0 | HL7 International | https://hl7.org/fhir/patient.html |
| G22 | Encounter, FHIR v5.0.0 | HL7 International | https://hl7.org/fhir/encounter.html |
| G23 | Location, FHIR v5.0.0 | HL7 International | https://hl7.org/fhir/location.html |
| G24 | Instructivo de acciones de limpieza y desinfección en instituciones de salud (personal de servicios generales) | Ministerio de Salud y Protección Social con la OPS | https://www.minsalud.gov.co/sites/rid/Lists/BibliotecaDigital/RIDE/VS/PP/ET/instructivo-limpieza-hospitalaria-ops.pdf |
| G25 | Ethics and governance of artificial intelligence for health: WHO guidance, resumen ejecutivo (principios éticos) | Organización Mundial de la Salud | https://www.who.int/publications/i/item/9789240029200 |
| G26 | Ethics and governance of artificial intelligence for health: WHO guidance, responsabilidad y decisiones autónomas | Organización Mundial de la Salud | misma URL que G25 |
| G27 | Is bed turnover rate a good metric for hospital scale efficiency? (Southeast Nigeria) | Cost Effectiveness and Resource Allocation | https://pmc.ncbi.nlm.nih.gov/articles/PMC7329453/ |
| G28 | Delay in discharge and its impact on unnecessary hospital bed occupancy | BMC Health Services Research | https://pmc.ncbi.nlm.nih.gov/articles/PMC3511236/ |
| G29 | Reducing delays, improving flow: the importance of a dedicated discharge coordinator | Cureus | https://pmc.ncbi.nlm.nih.gov/articles/PMC12166983/ |
| G30 | Association between boarding in the emergency department and in-hospital mortality (revisión sistemática) | PLoS ONE | https://pmc.ncbi.nlm.nih.gov/articles/PMC7159217/ |
| G31 | Dry-fogged hydrogen peroxide as an untact room disinfection automation system for terminal decontamination of an isolation room | Antimicrobial Resistance and Infection Control | https://pmc.ncbi.nlm.nih.gov/articles/PMC12296622/ |
| G32 | Anonymization, accountability, and access: legal dimensions of health data sharing in federated networks | Frontiers in Digital Health | https://pmc.ncbi.nlm.nih.gov/articles/PMC13044123/ |

Las licencias de los 64 documentos (normas públicas de Colombia, dominio público de EE. UU., CC BY, CC0 de HL7 y CC BY-NC-SA 3.0 IGO de la OMS) están en las columnas `license` de los dos `SOURCES.md`. El corpus no contiene datos de pacientes; lo que se sabe de un paciente solo entra por el flujo de aprobación clínica (base `historia`).

## Limitaciones

- **Tamaño de la base real.** 222 estancias de 64 pacientes es poco. Con esa base, el aumento no crea información nueva: interpola entre filas reales. Las métricas de un modelo deben calcularse con validación cruzada o sobre las filas reales, y nunca con filas sintéticas en el conjunto de prueba.
- **Origen único.** Los datos de MIMIC-IV-ED son de un hospital de Estados Unidos; la población, el sistema de triage (ESI) y los criterios de ingreso pueden diferir de los de la red colombiana.
- **Descartes.** Se pierden las filas con datos incompletos (por ejemplo, 15 sin `acuity`); si faltan de forma no aleatoria, la cohorte queda sesgada hacia los pacientes con registro completo.
- **Etiqueta gruesa.** `admitido` mezcla todos los motivos de ingreso; `disposition` distinto de ADMITTED agrupa altas, traslados y abandonos.
- **Agrupamiento.** Con n pequeño, la silueta es una medida inestable y los grupos dependen de la semilla y de la estandarización. La lectura "lejos / intermedio / cerca" ordena grupos por su tasa de ingreso; describe la cohorte, no es un diagnóstico.
- **Filas sintéticas.** Heredan los sesgos de sus vecinos reales y se mueven solo dentro de un grupo. Se marcan siempre como `SYNTHETIC`, pero no son pacientes.
- **Etiquetas de imagen por NLP.** Las etiquetas de NIH ChestX-ray14 se extrajeron de informes radiológicos con procesamiento de lenguaje natural (exactitud estimada superior al 90 %) y no son diagnósticos confirmados ni fueron revisadas una a una por personas.
- **Imágenes sin vínculo clínico.** Las radiografías no pertenecen a los pacientes de MIMIC ni a los pacientes sintéticos de la red: se asignan a estudios de forma arbitraria, con una preferencia por el perfil clínico. Sirven como demostración del flujo de imagen y no como evidencia de un paciente concreto.
- **Resolución.** El espejo entrega las imágenes reducidas a 224×224 píxeles; no sirven para evaluar detalle diagnóstico fino.
- **PACS.** El visor trabaja con vistas previas de 8 bits y el flujo de carga sigue en versión borrador.
