# Asistente clínico con RAG

Este documento describe el módulo de **generación aumentada por recuperación (RAG)** de SaludRed: cómo se construyen las bases de conocimiento, cómo se recupera la evidencia, qué se envía al modelo de lenguaje, cómo firma el médico y cómo se mide la calidad. Sigue la misma regla que el resto de la IA del proyecto ([`IA.md`](IA.md)): **el sistema propone y un clínico decide**.

## Arquitectura

```mermaid
flowchart LR
    subgraph Ingesta["Ingesta (python -m scripts.rag_ingest)"]
        C["rag/corpus/{general,triage}/ID.md<br/>front matter + párrafos"] --> CH["Chunking por párrafo<br/>50-75 palabras"]
        CH --> EMB["Embeddings (servicio ml, POST /embed)<br/>MiniLM multilingüe, 384-d"]
        EMB --> PG[("PostgreSQL + pgvector<br/>rag_chunks")]
    end
    subgraph Consulta["Consulta (POST /api/v1/rag/ask)"]
        Q["Pregunta del médico<br/>(+ patient_id opcional)"] --> PS["Seudonimización<br/>de pregunta y contexto"]
        HC["Resumen clínico seudonimizado<br/>(motivo, últimos valores, reportes IA aprobados)"] --> PS
        PS --> R["Recuperación híbrida<br/>BM25 + denso -> RRF -> MMR"]
        PG --> R
        R --> AB{"¿Evidencia?<br/>coseno >= 0.45 o BM25 >= 1.5"}
        AB -- no --> ABS["Abstención<br/>nada se guarda"]
        AB -- sí --> LLM["Groq (qwen3.8-27b)<br/>o modo extractivo"]
        LLM --> ANS["Respuesta con citas [n]"]
    end
    ANS --> SUG["Sugerencia PENDIENTE<br/>(rag_suggestions + prompt exacto)"]
    SUG --> FIRMA{"Médico o especialista"}
    FIRMA -- rechaza --> REJ["Rechazada<br/>queda como evidencia"]
    FIRMA -- aprueba --> HIST["Historia clínica (JSON/PDF)<br/>+ chunk base=historia del paciente"]
    HIST --> PG
```

Piezas y dónde viven:

| Pieza | Código |
|---|---|
| Ingesta y CLI | `app/services/rag/ingest.py`, `scripts/rag_ingest.py` |
| Chunking | `app/services/rag/chunking.py` |
| BM25, TF-IDF, RRF, MMR | `app/services/rag/lexical.py`, `app/services/rag/fusion.py` (Python puro, sin numpy) |
| Recuperación (usada por la API y por la evaluación) | `app/services/rag/retrieval.py` (`retrieve(...)`) |
| Almacenamiento y búsqueda densa | `app/models/rag.py`, `app/services/rag/store.py` |
| Embeddings | servicio `ml`: `ml/embeddings.py`, `POST /embed`; cliente en `app/services/ml_client.py` |
| Seudonimización de texto | `app/services/rag/privacy.py`, `app/services/rag_context.py` |
| Respuesta (Groq / extractivo) | `app/services/rag/llm.py`, `app/services/rag/answer.py`, `app/services/rag/prompt.py` |
| Consulta, firma y retroalimentación | `app/services/rag_suggestions.py`, `app/api/routes/rag.py` |
| Evaluación | `app/services/rag/evaluation.py`, `app/services/rag/judge.py`, `scripts/rag_eval.py` |

Por qué los embeddings viven en `ml`: la imagen de la API no tiene numpy, scikit-learn ni torch, y no debe tenerlos. El modelo de embeddings pesa cientos de MB y necesita torch; ya existe un contenedor para eso, sin base de datos y sin puertos publicados. Su `Dockerfile` descarga los pesos **al construir la imagen**, así que en ejecución no necesita internet.

## Las dos bases de conocimiento

| Base | Contenido | Quién la puede consultar |
|---|---|---|
| `general` (IDs `G01`...) | Guías clínicas y normas de uso general (diabetes, hipoglucemia, retinopatía, protección de datos...) | Todo el personal, siempre |
| `triage` (IDs `T01`...) | Normas y guías de triage y urgencias (Resolución 5596 de 2015 y similares) | Todo el personal, siempre |
| `historia` | Sugerencias **aprobadas** por un médico sobre un paciente, seudonimizadas | Solo con el seudónimo de **ese** paciente |

Reglas de curaduría del corpus (`rag/corpus/<base>/<ID>.md`):

1. **Solo fuentes públicas y citables**: normas colombianas, guías de ministerios y sociedades científicas, documentos de acceso abierto. Cada archivo declara en su front matter `publisher`, `url`, `year`, `doc_type` y `license`.
2. **Nada de datos de pacientes** en el corpus. Lo del paciente solo entra por el flujo de aprobación (base `historia`).
3. **Texto fiel a la fuente**, en párrafos de una sola idea separados por una línea en blanco, de unas 50 a 75 palabras.
4. **Un documento, una carpeta**: la carpeta (`general` o `triage`) debe coincidir con `base` del front matter; el ingest falla si no, y si hay IDs repetidos.
5. El ID del documento es estable: la evaluación y las citas lo usan (`<ID>-<i>` es el id del fragmento).

Formato de un documento:

```markdown
---
id: T01
base: triage
title: "Resolución 5596 de 2015 ..."
publisher: "Ministerio de Salud y Protección Social de Colombia"
url: "https://..."
year: 2015
doc_type: norma
license: "norma pública Colombia"
language: es
keywords: [triage, urgencias]
---

Primer párrafo...

Segundo párrafo...
```

## Chunking

La estrategia por defecto es **por párrafo**: un fragmento por párrafo del documento. Un párrafo se corta solo si supera 75 palabras: primero en límites de oración, y si una sola oración es más larga, en ventanas fijas de 60 palabras con 15 de solape. Un párrafo de menos de 15 palabras se conserva entero y se registra un aviso en el ingest (unirlo con el vecino mezclaría dos ideas).

Por qué 50-75 palabras: un fragmento tiene que contener **una idea citable** y caber de a tres en el prompt sin gastar el contexto. Fragmentos más largos diluyen la señal de BM25 y del embedding (el vector promedia varias ideas); más cortos pierden el contexto que hace que una cifra o un criterio se entienda. Además, cortar por párrafo respeta la estructura que ya puso el autor de la fuente, mientras que una ventana fija parte oraciones y tablas por la mitad.

`--chunking fixed` existe solo para comparar: corta todo el cuerpo en ventanas de 60 palabras con 15 de solape, sin mirar los párrafos.

**Supuesto 1:1 de la evaluación.** Con chunking por párrafo y párrafos de hasta 75 palabras, el párrafo `i` del documento `D` es exactamente el fragmento `D-i`. Si algún párrafo se dividió, la evaluación a nivel de fragmento acepta **cualquiera** de los fragmentos que lo contienen (se calcula con el mismo chunker, no se asume), y `RESULTS.md` avisa qué documentos no cumplen el 1:1.

## Recuperación

Todos los métodos comparten la misma tokenización del cuaderno del curso: minúsculas, sin tildes (NFD), expresión `[a-z0-9]+(,[0-9]+)?`, stopwords en español y tokens de más de un carácter.

| Método | Qué hace | Parámetros |
|---|---|---|
| `tfidf` | Coseno sobre vectores TF-IDF (idf suavizado, filas normalizadas) | n/a |
| `bm25` | BM25 implementado desde cero | `k1=1.2`, `b=0.75`, `idf = ln(1 + (N-n+0.5)/(n+0.5))` |
| `dense` | Coseno entre la pregunta y los fragmentos con `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensiones, normalizados: coseno = producto punto). En PostgreSQL: `ORDER BY embedding <=> :q` con índice HNSW `vector_cosine_ops` | n/a |
| `hybrid` | **RRF**: suma de `1/(60 + rango)` sobre el ranking BM25 y el denso | `k=60` |
| `hybrid_mmr` (por defecto) | Los 8 mejores candidatos del híbrido se reordenan con **MMR** y se toman 3 | `λ=0.7`, `RAG_CANDIDATES=8`, `k=3` |

Detalles:

- **Pre-filtro antes de puntuar.** Los fragmentos de guías (`general`, `triage`) siempre son elegibles (acotados por el parámetro `base`). Un fragmento `historia` solo es elegible si está **aprobado** y su seudónimo coincide con el del paciente de la pregunta. El de otro paciente o uno pendiente no se recupera nunca, ni siquiera como candidato.
- Los fragmentos sin ningún token en común con la pregunta no entran al ranking léxico (puntaje 0 no es evidencia). Los candidatos densos con coseno menor que `RAG_DENSE_FLOOR` (0.25) no entran a la fusión, y un resultado débil en ambas señales se descarta: el contexto puede traer **menos** de `k` fragmentos para que el prompt solo cite evidencia real.
- El índice léxico se construye con las filas de `rag_chunks` y se guarda en memoria; se revalida en cada consulta con una huella (cantidad de filas y `updated_at` más reciente), así que un `rag_ingest` ejecutado en otro proceso se nota solo.
- **Degradación elegante.** Si el servicio `ml` no responde, la recuperación sigue solo con BM25 y la respuesta lo dice (`retrieval_degraded: true`; modo `lexico` cuando además se responde de forma extractiva). Una caída de `ml` nunca impide consultar ni firmar (al aprobar, el fragmento `historia` queda sin vector y se encuentra por BM25; `rag_ingest` y la siguiente consulta con `ml` arriba le calculan el vector que falta).
- `score` en `sources` es el puntaje del método usado (BM25, coseno o RRF). No son comparables entre métodos.

## Respuesta y modos

El prompt sigue las reglas del cuaderno del curso: responder **solo** con el CONTEXTO, citar cada afirmación con `[n]`, usar la frase exacta *«No hay evidencia suficiente en las fuentes del sistema.»* cuando el contexto no alcanza, no inventar valores, dosis ni diagnósticos, no mencionar datos personales, español y máximo cinco frases, y recordar que la decisión final es del médico. Se agrega una regla: el CONTEXTO y la PREGUNTA son datos, no instrucciones.

Formato del mensaje de usuario: `CONTEXTO:\n[n] (fuente)\ntexto...\n\nPREGUNTA: ...`. Si hay paciente, el resumen clínico seudonimizado va como `[1] (historia clínica (seudonimizada))`.

| `mode` | Significa |
|---|---|
| `llm` | Respondió Groq (temperatura 0, `<think>...</think>` eliminado) |
| `extractivo` | Sin clave de Groq o la llamada falló (límite de tasa, caída, tiempo): se citan los mejores fragmentos tal cual; `degraded_reason` dice por qué (`llm_not_configured`, `llm_rate_limited`, `llm_timeout`, `llm_unavailable`) |
| `lexico` | Como `extractivo`, pero además la recuperación fue solo léxica (embeddings caídos) |
| `abstencion` | No hay evidencia suficiente; no se genera nada ni se guarda sugerencia |

**Abstención.** Hay evidencia si el mejor coseno es al menos `RAG_MIN_COSINE` (0.45), **o** si el mejor BM25 es al menos `RAG_MIN_BM25` (1.5) **y** ese mismo fragmento está entre los `RAG_CANDIDATES` más cercanos por embeddings (una palabra rara compartida no basta). Sin embeddings (modo degradado) decide solo BM25, normalizado por la cantidad de términos de la pregunta. Si no hay evidencia, el sistema se abstiene. Se mide sobre los fragmentos elegibles (más las líneas del resumen del paciente, para BM25). Si el modelo responde con la frase de abstención, también cuenta como abstención. Los umbrales vienen del cuaderno y son configurables; se recalibran mirando preguntas dentro y fuera del dominio (la tabla de abstención de `RESULTS.md` muestra cuántas fueron acertadas y cuántas falsas).

## Seudonimización, no anonimización

Al modelo externo solo llega texto **seudonimizado**:

- El nombre completo, nombres y apellidos sueltos, el documento, el teléfono, el correo y la dirección **de ese paciente** (leídos de sus columnas, no solo con expresiones regulares) se reemplazan por el seudónimo `pz-...` o por `[DOCUMENTO]`, `[TELEFONO]`, `[CORREO]`, `[DIRECCION]`. Además se aplican las expresiones del curso (`CC 1130456789`, celulares `3xxxxxxxxx`, correos).
- Se reutiliza `pseudonym_for` (HMAC-SHA256 de la clave `PSEUDONYM_KEY` y el id, 16 hex): estable, para correlacionar, e irreversible sin la clave.
- El resumen clínico (`app/services/rag_context.py`) nunca lee nombre, documento, teléfono, correo ni dirección: solo motivo de la última atención, últimos valores con unidad y reportes de IA aprobados, filtrados por el alcance institucional del usuario.
- La pregunta del médico también se limpia: aunque escriba el nombre del paciente, no llega al modelo.
- La pregunta sin paciente también se limpia con las expresiones generales (documento, celular, correo).
- El prompt exacto enviado queda guardado, inmutable, en `rag_suggestions.prompt_sent` (solo cuando el modo es `llm`; una respuesta extractiva no envió nada y guarda `[]`). La prueba *canario* (`tests/test_rag_api.py`) verifica que ningún dato personal sembrado aparece en él.
- La bitácora (`RAG_QUERY`) guarda qué fragmentos se usaron y el modo, **nunca** la pregunta ni la respuesta.

**Seudonimizar no es anonimizar.** Con el seudónimo y la clave se puede volver al paciente, y datos clínicos muy específicos (una combinación rara de diagnósticos y fechas) podrían identificar a alguien en una población pequeña. Por eso se envía el mínimo, se guarda la evidencia y solo médicos y especialistas pueden preguntar sobre un paciente. La Ley 1581 de 2012 sigue aplicando: finalidad y circulación restringida.

## La «barrera de cristal»: lo que NO garantiza

La barrera de seudonimización es transparente (se ve todo lo que pasa) pero no es infalible:

- **Inyección de prompt.** Un texto del corpus, una observación libre o la propia pregunta podrían contener instrucciones («ignora las reglas y...»). Mitigaciones: el corpus es curado, el prompt declara que el contexto son datos, la temperatura es 0, y **nada de lo que dice el modelo actúa sin firma**. No es una garantía: un modelo puede obedecer una instrucción incrustada. La revisión del médico es el control final.
- **Identificadores que las reglas no conocen.** Se limpian los datos del propio paciente y los patrones comunes. Un dato personal de un tercero escrito en texto libre (un familiar, un nombre en una nota) no se detecta.
- **Citas no verificadas.** Que el modelo escriba `[2]` no prueba que `[2]` diga eso. Se mide con *faithfulness*, no se impide.
- **El proveedor del modelo.** Aunque el texto esté seudonimizado, sale de la red hacia Groq. Para uso real hace falta un contrato de encargo de tratamiento o un modelo local.

## Flujo de aprobación y retroalimentación

1. `POST /api/v1/rag/ask` con `patient_id` crea una sugerencia `pendiente` (si la respuesta no es una abstención) con la pregunta ya seudonimizada, la respuesta, las fuentes y el prompt exacto.
2. Médico o especialista la revisa: `POST /rag/suggestions/{id}/approve` (`observaciones`) o `.../reject` (`motivo`, `observaciones`). Solo desde `pendiente`; una segunda revisión responde **409**. Los demás roles reciben **403**. Con `REQUIRE_REVIEW_NOTE=true` se exige el concepto clínico, igual que en los reportes de IA.
3. Al aprobar: la sugerencia aparece en `GET /patients/{id}/clinical-record` (campo `sugerencias_rag`) y en el PDF, y se indexa como fragmento `base=historia`, `estado=aprobado`, con el seudónimo del paciente (sin las citas `[n]`, que ya no apuntan a nada). La próxima pregunta sobre ese paciente puede citarla. Esa es la **retroalimentación** del curso: lo que el médico valida se vuelve conocimiento del caso.
4. Una sugerencia rechazada se conserva como evidencia, pero no entra a la historia ni al índice.

## Evaluación y cómo leer las métricas

`rag/eval/questions_general.yaml` y `rag/eval/questions_triage.yaml` listan preguntas con `expected_doc`, `expected_paragraph` y `reference_answer` (`ABSTAIN` para las que están fuera del corpus).

- **Recall@k (documento y fragmento):** fracción de preguntas cuyo documento (o párrafo) esperado está entre los primeros `k` fragmentos. Recall@1 mide si lo mejor quedó primero; Recall@5 si estaba al alcance del prompt.
- **MRR:** promedio de `1/posición` del primer acierto; premia estar arriba.
- **Exactitud de abstención:** de las preguntas fuera de alcance, cuántas fueron rechazadas. Se reporta también la **tasa de falsa abstención** (preguntas con respuesta que se rechazaron); subir el umbral mejora una y empeora la otra.
- **Métricas tipo RAGAS** (`--judge`), con Groq como juez, JSON estricto, temperatura 0 y reintentos si no se puede interpretar la respuesta. **No usamos la librería `ragas`**: la implementación es propia y sigue sus definiciones:
  - *Faithfulness*: fracción de afirmaciones de la respuesta que el contexto recuperado respalda. Baja = alucina.
  - *Answer relevancy*: similitud (coseno) entre la pregunta y preguntas que el juez genera a partir de la respuesta. Baja = respuesta que no contesta lo que se preguntó.
  - *Context precision*: fracción de fragmentos recuperados que son relevantes, ponderada por posición (`Σ precision@k · relevante_k / Σ relevante_k`). Baja = ruido en el contexto.
  - *Context recall*: fracción de frases de la respuesta de referencia que el contexto puede respaldar. Baja = falta evidencia.

  Meta del curso: **≥ 0.75** en cada una. Ojo: el juez es el mismo modelo que genera, lo que favorece las notas; son una guía relativa, no una verdad absoluta.

## Cómo ejecutarlo

### Cambio de imagen de la base de datos (leer antes de desplegar)

La `db` pasó de `postgres:16-alpine` a `pgvector/pgvector:pg16` (necesaria para la extensión `vector`). Misma versión mayor, así que el volumen **arranca**, pero **no es transparente**: alpine usa musl y la imagen nueva usa glibc, y el orden de los textos (collation `en_US.utf8`) cambia. Los índices btree de texto ya existentes (correos, números de documento, restricciones únicas) quedan **inconsistentes** hasta reconstruirlos. Con un volumen existente hay que hacer **una vez** este paso, justo después del cambio y antes de usar el sistema:

```bash
docker compose exec db psql -U saludred -d saludred \
  -c "REINDEX DATABASE saludred;" -c "ALTER DATABASE saludred REFRESH COLLATION VERSION;"
```

(usa el usuario y la base de tu `.env`). Alternativa segura: `pg_dump` con la imagen vieja, levantar la nueva con un volumen vacío y restaurar. Con un volumen nuevo no hay nada que hacer.

**Si `DATABASE_URL` apunta a Neon** (base gestionada) no se cambia ninguna imagen: pgvector está disponible allí y basta la migración, que ejecuta `CREATE EXTENSION IF NOT EXISTS vector` con el rol propietario. Si el rol no tiene permiso, la migración falla con un mensaje que lo explica (ejecute `CREATE EXTENSION vector;` con un rol autorizado y repita).

### Secuencia en el servidor

```bash
docker compose build db ml api
docker compose up -d db                                    # imagen nueva, volumen existente
docker compose exec db psql -U saludred -d saludred \
  -c "REINDEX DATABASE saludred;" -c "ALTER DATABASE saludred REFRESH COLLATION VERSION;"   # solo con volumen existente
docker compose up -d                                       # la API aplica alembic upgrade head al arrancar
docker compose exec api python -m scripts.rag_ingest --reset  # carga rag/corpus (idempotente; completa vectores faltantes de la historia)
docker compose exec api python -m scripts.rag_eval --k 1,3,5  # recuperación + abstención
docker compose exec -T api python -m scripts.rag_eval --judge --judge-model openai/gpt-oss-20b --sleep 3   # además, métricas tipo RAGAS (necesita GROQ_API_KEY)
```

Comparar estrategias de chunking:

```bash
docker compose exec api python -m scripts.rag_ingest --reset --chunking fixed
docker compose exec api python -m scripts.rag_eval --chunking fixed --tag fixed
docker compose exec api python -m scripts.rag_ingest --reset   # volver a la estrategia por defecto
```

Opciones de `rag_eval` para Groq: `--judge-model` (modelo juez propio), `--sleep SEGUNDOS` (pausa tras cada llamada al modelo, generación y juez) y `--retries` (5 por defecto). Ante un HTTP 429 se espera la cabecera `retry-after` o 2, 4, 8, 16 y 32 s. Si aun así una pregunta no logra respuesta del modelo, queda **degradada** (respuesta extractiva) y se excluye de los promedios del juez; las abstenciones del sistema solo aportan las métricas de contexto. `RESULTS.md` informa por base cuántas preguntas fueron juzgadas, degradadas y abstenidas, cuántos 429 hubo, y advierte si se juzgó menos del 80 %. Con modelos `gpt-oss` el cliente pide `reasoning_effort: low` y, si la API responde 400 a un parámetro opcional, lo retira y lo recuerda.

**Barrido de pesos sin llamadas al modelo:** `rag_eval --sweep` compara solo la recuperación (Recall@1/3/5 a nivel documento y fragmento, y MRR) del denso solo contra `hybrid` y `hybrid_mmr` con peso denso {1, 1.5, 2, 3} y léxico 1, escribe `rag/eval/SWEEP.md` y `sweep.json`, y marca con ★ la mejor (Recall@5 a nivel fragmento, desempate por MRR). Con ese resultado se fijan las variables `RAG_RRF_*`. Para una corrida con juez, `--method`, `--context-k` (fragmentos que recibe el modelo; `--k` sigue siendo la lista de cortes de Recall) y `--rrf-dense-weight` / `--rrf-lexical-weight` sustituyen la configuración solo en esa corrida y quedan en el encabezado de `RESULTS.md`. El prompt no recorta el contexto: `GROQ_MAX_TOKENS` limita únicamente la respuesta generada.

La evaluación escribe `rag/eval/results.json` y `rag/eval/RESULTS.md` (tablas, fecha, modelos y tamaño del corpus).

Variables de entorno (ver `.env.example`):

| Variable | Por defecto | Para qué |
|---|---|---|
| `GROQ_API_KEY` | vacía | Clave de Groq. Sin ella: modo extractivo. Nunca se versiona |
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Modelo de lenguaje y juez |
| `RAG_JUDGE_MODEL` | vacía | Modelo juez de la evaluación (también `--judge-model`). Vacío: el modelo generador. Los límites de Groq son **por modelo**: un juez distinto reparte la cuota y evita que el modelo se califique a sí mismo |
| `GROQ_TIMEOUT_SECONDS` | `30` | Espera de Groq; al agotarse se responde en modo extractivo |
| `GROQ_MAX_TOKENS` | `600` | Tope de la respuesta (qwen3 razona antes de responder; se envía `reasoning_format: hidden`) |
| `RAG_RRF_DENSE_WEIGHT` / `RAG_RRF_LEXICAL_WEIGHT` | `3.0` / `1.0` | RRF ponderado: `w_denso/(60+rango_denso) + w_léxico/(60+rango_bm25)`. 1/1 es RRF clásico; con un corpus mixto es/en BM25 es débil y conviene dar más peso al denso |
| `RAG_DENSE_FLOOR` | `0.25` | Coseno mínimo para que un candidato denso entre a la fusión |
| `RAG_MIN_COSINE` | `0.45` | Umbral de coseno para la abstención |
| `RAG_MIN_BM25` | `1.5` | Umbral de BM25 para la abstención |
| `RAG_MMR_LAMBDA` | `0.7` | Relevancia (1.0) frente a diversidad (0.0) |
| `RAG_CANDIDATES` | `8` | Candidatos del híbrido que recibe MMR |
| `RAG_DEFAULT_K` | `5` | Fragmentos citados en el prompt |
| `ML_EMBED_TIMEOUT_SECONDS` | `30` | Espera de `/embed` (la primera llamada carga el modelo) |

Endpoints (todos bajo `/api/v1`): `POST /rag/ask`, `GET /rag/suggestions`, `GET /rag/suggestions/{id}`, `POST /rag/suggestions/{id}/approve`, `POST /rag/suggestions/{id}/reject`, `GET /rag/stats`. Preguntar sin paciente lo puede hacer cualquier rol institucional; preguntar sobre un paciente y firmar, solo médico o especialista de la IPS que lo atiende.

## Limitaciones conocidas

- El corpus es pequeño y didáctico; las métricas de recuperación sobre pocas preguntas tienen mucha varianza (un acierto de diferencia mueve varios puntos).
- Los umbrales de abstención se calibraron con el cuaderno del curso; con otro corpus hay que recalibrar.
- El juez comparte modelo con el generador y Groq tiene límites de tasa: una evaluación completa con `--judge` hace unas cinco llamadas por pregunta.
- Búsqueda léxica sin lematización: «diabético» y «diabetes» son tokens distintos (por eso existe la parte densa).
- El índice léxico vive en la memoria del proceso. Es válido porque la API corre con **un** worker (ver [`EVENTOS.md`](EVENTOS.md)).
- Una sugerencia aprobada es texto libre de una respuesta del modelo con la firma del médico; no se estructura en datos clínicos codificados (FHIR).
- Solo hay español. El modelo de embeddings es multilingüe, pero el corpus, el prompt y los umbrales se calibraron en español.
- Las pruebas usan embeddings de juguete (hash de palabras); la calidad semántica real solo se mide en el servidor, con el modelo de verdad.
