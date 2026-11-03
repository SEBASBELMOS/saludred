# Resultados de la evaluación del RAG clínico

- Fecha de ejecución: 2026-10-29T16:39:23+00:00
- Embeddings: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Modelo de lenguaje: `qwen/qwen3.8-27b` · juez: `openai/gpt-oss-20b`
- Corpus: 64 documentos (general: 32, triage: 32) y 1049 fragmentos (general: 518, historia: 0, triage: 531); chunking `paragraph`
- Preguntas: 30 (26 con respuesta en el corpus, 4 fuera de alcance)
- Parámetros: BM25 k1=1.2 b=0.75 · RRF k=60 · MMR λ=0.7 sobre 8 candidatos

Los fragmentos se ordenan por método y se mide si el documento (o el párrafo) esperado aparece entre los primeros k. MRR se calcula sobre los primeros 8 fragmentos.

> Nota: en 39 documento(s) un párrafo supera las 75 palabras y se dividió (`G01, G02, G03, G04, G05, G06, G10, G11, G12, G13, G14, G15, G16, G17, G20, G21, G22, G23, G24, G26, G27, G28, G29, G30, G31, G32, T04, T05, T07, T11, T12, T17, T18, T19, T21, T22, T25, T27, T30`). A nivel de fragmento, el párrafo esperado se cuenta como acertado si aparece cualquiera de los fragmentos que lo contienen.

## Recuperación · Todas las preguntas (n=26)

### Nivel documento

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.23 | 0.42 | 0.46 | 0.35 |
| BM25 | 0.31 | 0.46 | 0.58 | 0.42 |
| Denso | 0.62 | 0.85 | 0.96 | 0.74 |
| Híbrido (RRF) | 0.54 | 0.85 | 0.92 | 0.70 |
| Híbrido + MMR | 0.62 | 0.92 | 0.96 | 0.77 |

### Nivel fragmento (n=26)

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.08 | 0.27 | 0.31 | 0.20 |
| BM25 | 0.15 | 0.31 | 0.38 | 0.26 |
| Denso | 0.35 | 0.54 | 0.65 | 0.46 |
| Híbrido (RRF) | 0.31 | 0.54 | 0.73 | 0.45 |
| Híbrido + MMR | 0.35 | 0.58 | 0.69 | 0.46 |

## Recuperación · Base `general` (n=13)

### Nivel documento

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.38 | 0.62 | 0.69 | 0.50 |
| BM25 | 0.38 | 0.62 | 0.77 | 0.53 |
| Denso | 0.62 | 0.85 | 1.00 | 0.75 |
| Híbrido (RRF) | 0.46 | 0.77 | 0.92 | 0.63 |
| Híbrido + MMR | 0.62 | 0.85 | 0.92 | 0.75 |

### Nivel fragmento (n=13)

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.15 | 0.38 | 0.46 | 0.27 |
| BM25 | 0.15 | 0.38 | 0.46 | 0.28 |
| Denso | 0.15 | 0.31 | 0.54 | 0.29 |
| Híbrido (RRF) | 0.15 | 0.46 | 0.54 | 0.30 |
| Híbrido + MMR | 0.15 | 0.31 | 0.46 | 0.25 |

## Recuperación · Base `triage` (n=13)

### Nivel documento

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.08 | 0.23 | 0.23 | 0.19 |
| BM25 | 0.23 | 0.31 | 0.38 | 0.31 |
| Denso | 0.62 | 0.85 | 0.92 | 0.73 |
| Híbrido (RRF) | 0.62 | 0.92 | 0.92 | 0.77 |
| Híbrido + MMR | 0.62 | 1.00 | 1.00 | 0.79 |

### Nivel fragmento (n=13)

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.00 | 0.15 | 0.15 | 0.13 |
| BM25 | 0.15 | 0.23 | 0.31 | 0.23 |
| Denso | 0.54 | 0.77 | 0.77 | 0.63 |
| Híbrido (RRF) | 0.46 | 0.62 | 0.92 | 0.60 |
| Híbrido + MMR | 0.54 | 0.85 | 0.92 | 0.67 |

## Abstención

Umbrales: coseno < 0.45 **y** BM25 < 1.5.

| Medida | Valor |
|---|---:|
| Abstenciones correctas (fuera de alcance) | 4/4 |
| Exactitud de abstención | 1.00 |
| Falsas abstenciones (preguntas con respuesta) | 1/26 |
| Tasa de falsa abstención | 0.04 |

## Métricas tipo RAGAS (LLM como juez)

- Generador: `qwen/qwen3.8-27b` · juez: `openai/gpt-oss-20b`
- Pausa entre llamadas: 8.0 s · respuestas HTTP 429 recibidas: 0
- Recuperación del contexto: método `hybrid`, k=5, pesos RRF denso=3.0 léxico=1.0
- Fecha: 2026-10-29T16:39:23+00:00 · temperatura 0, k=5. Implementación propia (no se usa la librería `ragas`). Meta: ≥ 0.75.

| Preguntas con respuesta en el corpus | Global | general | triage |
|---|---:|---:|---:|
| Total | 26 | 13 | 13 |
| Juzgadas con respuesta del modelo | 21 | 10 | 11 |
| Degradadas (el modelo falló; excluidas) | 0 | 0 | 0 |
| Abstenciones del sistema | 5 | 3 | 2 |

| Métrica | Global (n) | general (n) | triage (n) |
|---|---:|---:|---:|
| Fidelidad (faithfulness) | 1.00 (21) | 1.00 (10) | 1.00 (11) |
| Relevancia de la respuesta | 0.70 (bajo la meta) (14) | 0.66 (6) | 0.73 (8) |
| Precisión del contexto | 0.55 (bajo la meta) (25) | 0.50 (13) | 0.60 (12) |
| Cobertura del contexto (recall) | 0.74 (bajo la meta) (25) | 0.58 (13) | 0.92 (12) |
