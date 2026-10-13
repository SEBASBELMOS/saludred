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
