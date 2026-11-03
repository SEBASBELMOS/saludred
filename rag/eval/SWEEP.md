# Barrido de pesos RRF (solo recuperación, sin llamadas al modelo)

- Fecha de ejecución: 2026-10-29T16:16:33+00:00
- Embeddings: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` · chunking `paragraph` · 26 preguntas con respuesta en el corpus
- Mejor configuración: chunk-level Recall@5, then chunk-level MRR (marcada con **★**)

Fusión: `peso_denso/(60 + rango_denso) + peso_léxico/(60 + rango_bm25)`. `denso` solo es la línea base.

## Nivel fragmento

| | Método | Peso denso | Peso léxico | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
|  | Denso | n/a | n/a | 0.35 | 0.54 | 0.65 | 0.46 |
|  | Híbrido (RRF) | 1 | 1 | 0.19 | 0.58 | 0.62 | 0.37 |
|  | Híbrido (RRF) | 1.5 | 1 | 0.23 | 0.54 | 0.62 | 0.41 |
|  | Híbrido (RRF) | 2 | 1 | 0.27 | 0.54 | 0.69 | 0.43 |
| ★ | Híbrido (RRF) | 3 | 1 | 0.31 | 0.54 | 0.73 | 0.45 |
|  | Híbrido + MMR | 1 | 1 | 0.31 | 0.50 | 0.62 | 0.41 |
|  | Híbrido + MMR | 1.5 | 1 | 0.35 | 0.58 | 0.69 | 0.47 |
|  | Híbrido + MMR | 2 | 1 | 0.35 | 0.58 | 0.69 | 0.46 |
|  | Híbrido + MMR | 3 | 1 | 0.35 | 0.58 | 0.69 | 0.46 |

## Nivel documento

| | Método | Peso denso | Peso léxico | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
|  | Denso | n/a | n/a | 0.62 | 0.85 | 0.96 | 0.74 |
|  | Híbrido (RRF) | 1 | 1 | 0.46 | 0.77 | 0.81 | 0.62 |
|  | Híbrido (RRF) | 1.5 | 1 | 0.50 | 0.77 | 0.85 | 0.65 |
|  | Híbrido (RRF) | 2 | 1 | 0.50 | 0.81 | 0.92 | 0.67 |
| ★ | Híbrido (RRF) | 3 | 1 | 0.54 | 0.85 | 0.92 | 0.70 |
|  | Híbrido + MMR | 1 | 1 | 0.58 | 0.81 | 0.85 | 0.69 |
|  | Híbrido + MMR | 1.5 | 1 | 0.62 | 0.85 | 0.88 | 0.73 |
|  | Híbrido + MMR | 2 | 1 | 0.58 | 0.81 | 0.88 | 0.70 |
|  | Híbrido + MMR | 3 | 1 | 0.62 | 0.92 | 0.96 | 0.77 |

Recomendación: `RAG_RRF_DENSE_WEIGHT=3` `RAG_RRF_LEXICAL_WEIGHT=1` con el método `hybrid`.
