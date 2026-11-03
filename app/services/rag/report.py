"""Markdown rendering of the evaluation results."""

from __future__ import annotations

from typing import Any

METHOD_LABELS = {
    "tfidf": "TF-IDF",
    "bm25": "BM25",
    "dense": "Denso",
    "hybrid": "Híbrido (RRF)",
    "hybrid_mmr": "Híbrido + MMR",
}
JUDGE_LABELS = {
    "faithfulness": "Fidelidad (faithfulness)",
    "answer_relevancy": "Relevancia de la respuesta",
    "context_precision": "Precisión del contexto",
    "context_recall": "Cobertura del contexto (recall)",
}
TARGET = 0.75


def _fmt(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.2f}"


def _retrieval_table(retrieval: dict[str, Any], scope: str, ks: list[int], level: str) -> list[str]:
    head = "| Método | " + " | ".join(f"Recall@{k}" for k in ks) + " | MRR |"
    lines = [head, "|---|" + "---:|" * (len(ks) + 1)]
    for method, label in METHOD_LABELS.items():
        agg = retrieval[method].get(scope)
        if agg is None:
            continue
        by_k = agg[f"recall_{level}"]
        # Keys are ints in memory and strings after a JSON round trip.
        recalls = " | ".join(_fmt(by_k.get(k, by_k.get(str(k)))) for k in ks)
        lines.append(f"| {label} | {recalls} | {_fmt(agg[f'mrr_{level}'])} |")
    return lines


def render_markdown(results: dict[str, Any]) -> str:
    ks = results["ks"]
    run = results["run"]
    corpus, models, questions = results["corpus"], results["models"], results["questions"]
    out = [
        "# Resultados de la evaluación del RAG clínico",
        "",
        f"- Fecha de ejecución: {results['run_at']}",
        f"- Embeddings: `{models['embedding']}`",
        f"- Modelo de lenguaje: `{models['llm'] or 'sin modelo (extractivo)'}`"
        + (f" · juez: `{models['judge']}`" if models["judge"] else ""),
        f"- Corpus: {sum(corpus['documents'].values())} documentos "
        f"({', '.join(f'{b}: {n}' for b, n in sorted(corpus['documents'].items()))}) y "
        f"{sum(corpus['chunks'].values())} fragmentos "
        f"({', '.join(f'{b}: {n}' for b, n in sorted(corpus['chunks'].items()))}); "
        f"chunking `{results['chunking']}`",
        f"- Preguntas: {questions['total']} ({questions['answerable']} con respuesta en el corpus, "
        f"{questions['out_of_scope']} fuera de alcance)",
        f"- Parámetros: BM25 k1=1.2 b=0.75 · RRF k=60 · MMR λ={results['params']['mmr_lambda']} "
        f"sobre {results['params']['candidates']} candidatos",
        "",
        "Los fragmentos se ordenan por método y se mide si el documento (o el párrafo) esperado aparece "
        "entre los primeros k. MRR se calcula sobre los primeros "
        f"{results['ranking_depth']} fragmentos.",
        "",
    ]
    non_one = corpus["paragraph_chunking_not_one_to_one"]
    if non_one:
        out += [
            f"> Nota: en {len(non_one)} documento(s) un párrafo supera las 75 palabras y se dividió "
            "(`" + ", ".join(non_one) + "`). A nivel de fragmento, el párrafo esperado se cuenta como "
            "acertado si aparece cualquiera de los fragmentos que lo contienen.",
            "",
        ]
    scopes = ["overall", *[s for s in results["retrieval"]["bm25"] if s != "overall"]]
    for scope in scopes:
        title = "Todas las preguntas" if scope == "overall" else f"Base `{scope}`"
        n = results["retrieval"]["bm25"][scope]["n"]
        n_chunk = results["retrieval"]["bm25"][scope]["n_chunk_level"]
        out += [f"## Recuperación · {title} (n={n})", "", "### Nivel documento", ""]
        out += _retrieval_table(results["retrieval"], scope, ks, "doc")
        out += ["", f"### Nivel fragmento (n={n_chunk})", ""]
        out += _retrieval_table(results["retrieval"], scope, ks, "chunk")
        out.append("")

    ab = results["abstention"]
    out += [
        "## Abstención",
        "",
        f"Umbrales: coseno < {ab['thresholds']['min_cosine']} **y** BM25 < {ab['thresholds']['min_bm25']}.",
        "",
        "| Medida | Valor |",
        "|---|---:|",
        f"| Abstenciones correctas (fuera de alcance) | {ab['correct_abstentions']}/{ab['n_out_of_scope']} |",
        f"| Exactitud de abstención | {_fmt(ab['accuracy'])} |",
        f"| Falsas abstenciones (preguntas con respuesta) | {ab['false_abstentions']}/{ab['n_answerable']} |",
        f"| Tasa de falsa abstención | {_fmt(ab['false_abstention_rate'])} |",
        "",
    ]

    judged = results.get("judge")
    if judged:
        overall = judged["overall"]
        bases = list(judged["by_dataset"])
        out += [
            "## Métricas tipo RAGAS (LLM como juez)",
            "",
            f"- Generador: `{models['llm']}` · juez: `{judged['model']}`"
            + (" (el mismo modelo: puede favorecer las notas)" if models["llm"] == judged["model"] else ""),
            f"- Pausa entre llamadas: {run['sleep_seconds']} s · respuestas HTTP 429 recibidas: {run['rate_limit_hits']}",
            f"- Recuperación del contexto: método `{run['judge_method']}`, k={run['context_k']}, "
            f"pesos RRF denso={run['rrf_dense_weight']} léxico={run['rrf_lexical_weight']}",
            f"- Fecha: {results['run_at']} · temperatura 0, k={judged['k']}. Implementación propia "
            f"(no se usa la librería `ragas`). Meta: ≥ {TARGET:.2f}.",
            "",
            "| Preguntas con respuesta en el corpus | Global | " + " | ".join(bases) + " |",
            "|---|---:|" + "---:|" * len(bases),
        ]
        for key, label in (
            ("n_questions", "Total"),
            ("n_judged", "Juzgadas con respuesta del modelo"),
            ("n_degraded", "Degradadas (el modelo falló; excluidas)"),
            ("n_abstained", "Abstenciones del sistema"),
        ):
            cells = " | ".join(str(judged["by_dataset"][d][key]) for d in bases)
            out.append(f"| {label} | {overall[key]} | {cells} |")
        out += ["", "| Métrica | Global (n) | " + " | ".join(f"{d} (n)" for d in bases) + " |", "|---|---:|" + "---:|" * len(bases)]
        for name, label in JUDGE_LABELS.items():
            cells = " | ".join(
                f"{_fmt(judged['by_dataset'][d][name])} ({judged['by_dataset'][d][f'n_{name}']})" for d in bases
            )
            value = overall[name]
            flag = "" if value is None or value >= TARGET else " (bajo la meta)"
            out.append(f"| {label} | {_fmt(value)}{flag} ({overall[f'n_{name}']}) | {cells} |")
        out.append("")
        answerable = judged["n_answerable"]
        if answerable and overall["n_judged"] < 0.8 * answerable:
            out += [
                f"> ADVERTENCIA: solo {overall['n_judged']} de {answerable} preguntas con respuesta "
                f"({overall['n_judged'] / answerable:.0%}) fueron juzgadas con una respuesta real del modelo "
                "(menos del 80%). Las métricas de arriba no son representativas; repita la ejecución con "
                "`--sleep` mayor o un modelo juez distinto.",
                "",
            ]
    else:
        out += ["## Métricas tipo RAGAS", "", "No se ejecutaron (opción `--judge`).", ""]
    return "\n".join(out)


def render_sweep_markdown(sweep: dict[str, Any]) -> str:
    ks = sweep["ks"]
    out = [
        "# Barrido de pesos RRF (solo recuperación, sin llamadas al modelo)",
        "",
        f"- Fecha de ejecución: {sweep['run_at']}",
        f"- Embeddings: `{sweep['models']['embedding']}` · chunking `{sweep['chunking']}` · "
        f"{sweep['n_questions']} preguntas con respuesta en el corpus",
        f"- Mejor configuración: {sweep['selection']} (marcada con **★**)",
        "",
        "Fusión: `peso_denso/(60 + rango_denso) + peso_léxico/(60 + rango_bm25)`. "
        "`denso` solo es la línea base.",
        "",
    ]
    for level, title in (("chunk", "Nivel fragmento"), ("doc", "Nivel documento")):
        out += [
            f"## {title}",
            "",
            "| | Método | Peso denso | Peso léxico | "
            + " | ".join(f"Recall@{k}" for k in ks)
            + " | MRR |",
            "|---|---|---:|---:|" + "---:|" * (len(ks) + 1),
        ]
        for row in sweep["configs"]:
            agg = row["overall"]
            by_k = agg[f"recall_{level}"]
            recalls = " | ".join(_fmt(by_k.get(k, by_k.get(str(k)))) for k in ks)
            dense_w = "n/a" if row["dense_weight"] is None else f"{row['dense_weight']:g}"
            lex_w = "n/a" if row["lexical_weight"] is None else f"{row['lexical_weight']:g}"
            mark = "★" if row["best"] else ""
            label = METHOD_LABELS[row["method"]]
            out.append(f"| {mark} | {label} | {dense_w} | {lex_w} | {recalls} | {_fmt(agg[f'mrr_{level}'])} |")
        out.append("")
    best = next(r for r in sweep["configs"] if r["best"])
    if best["dense_weight"] is None:
        out.append("Recomendación: el denso solo supera a las fusiones; deje `RAG_RRF_DENSE_WEIGHT` alto o use `--method dense`.")
    else:
        out.append(
            f"Recomendación: `RAG_RRF_DENSE_WEIGHT={best['dense_weight']:g}` "
            f"`RAG_RRF_LEXICAL_WEIGHT={best['lexical_weight']:g}` con el método `{best['method']}`."
        )
    out.append("")
    return "\n".join(out)
