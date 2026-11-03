# Resultados de la evaluación del RAG clínico

- Fecha de ejecución: 2026-10-29T13:52:31+00:00
- Embeddings: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Modelo de lenguaje: `sin modelo (extractivo)`
- Corpus: 64 documentos (general: 32, triage: 32) y 1362 fragmentos (general: 640, historia: 0, triage: 722); chunking `fixed`
- Preguntas: 30 (26 con respuesta en el corpus, 4 fuera de alcance)
- Parámetros: BM25 k1=1.2 b=0.75 · RRF k=60 · MMR λ=0.7 sobre 8 candidatos

Los fragmentos se ordenan por método y se mide si el documento (o el párrafo) esperado aparece entre los primeros k. MRR se calcula sobre los primeros 8 fragmentos.

> Nota: en 39 documento(s) un párrafo supera las 75 palabras y se dividió (`G01, G02, G03, G04, G05, G06, G10, G11, G12, G13, G14, G15, G16, G17, G20, G21, G22, G23, G24, G26, G27, G28, G29, G30, G31, G32, T04, T05, T07, T11, T12, T17, T18, T19, T21, T22, T25, T27, T30`). A nivel de fragmento, el párrafo esperado se cuenta como acertado si aparece cualquiera de los fragmentos que lo contienen.

## Recuperación · Todas las preguntas (n=26)

### Nivel documento

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.31 | 0.42 | 0.58 | 0.38 |
| BM25 | 0.23 | 0.46 | 0.54 | 0.36 |
| Denso | 0.50 | 0.73 | 0.85 | 0.66 |
| Híbrido (RRF) | 0.46 | 0.73 | 0.81 | 0.61 |
| Híbrido + MMR | 0.46 | 0.81 | 0.88 | 0.64 |

### Nivel fragmento (n=26)

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.23 | 0.27 | 0.54 | 0.30 |
| BM25 | 0.08 | 0.31 | 0.42 | 0.24 |
| Denso | 0.23 | 0.46 | 0.69 | 0.41 |
| Híbrido (RRF) | 0.23 | 0.54 | 0.65 | 0.41 |
| Híbrido + MMR | 0.19 | 0.50 | 0.62 | 0.37 |

## Recuperación · Base `general` (n=13)

### Nivel documento

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.46 | 0.54 | 0.69 | 0.52 |
| BM25 | 0.31 | 0.54 | 0.69 | 0.45 |
| Denso | 0.54 | 0.69 | 0.85 | 0.67 |
| Híbrido (RRF) | 0.38 | 0.69 | 0.77 | 0.55 |
| Híbrido + MMR | 0.38 | 0.85 | 0.85 | 0.60 |

### Nivel fragmento (n=13)

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.38 | 0.46 | 0.69 | 0.46 |
| BM25 | 0.08 | 0.31 | 0.54 | 0.26 |
| Denso | 0.23 | 0.38 | 0.62 | 0.38 |
| Híbrido (RRF) | 0.31 | 0.62 | 0.69 | 0.47 |
| Híbrido + MMR | 0.15 | 0.46 | 0.62 | 0.35 |

## Recuperación · Base `triage` (n=13)

### Nivel documento

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.15 | 0.31 | 0.46 | 0.25 |
| BM25 | 0.15 | 0.38 | 0.38 | 0.28 |
| Denso | 0.46 | 0.77 | 0.85 | 0.65 |
| Híbrido (RRF) | 0.54 | 0.77 | 0.85 | 0.67 |
| Híbrido + MMR | 0.54 | 0.77 | 0.92 | 0.68 |

### Nivel fragmento (n=13)

| Método | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---:|---:|---:|---:|
| TF-IDF | 0.08 | 0.08 | 0.38 | 0.15 |
| BM25 | 0.08 | 0.31 | 0.31 | 0.21 |
| Denso | 0.23 | 0.54 | 0.77 | 0.45 |
| Híbrido (RRF) | 0.15 | 0.46 | 0.62 | 0.34 |
| Híbrido + MMR | 0.23 | 0.54 | 0.62 | 0.39 |

## Abstención

Umbrales: coseno < 0.45 **y** BM25 < 1.5.

| Medida | Valor |
|---|---:|
| Abstenciones correctas (fuera de alcance) | 4/4 |
| Exactitud de abstención | 1.00 |
| Falsas abstenciones (preguntas con respuesta) | 0/26 |
| Tasa de falsa abstención | 0.00 |

## Métricas tipo RAGAS

No se ejecutaron (opción `--judge`).
