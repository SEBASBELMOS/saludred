import { useQuery } from "@tanstack/react-query";
import { aiApi, type ModelMetrics, type Subgroup } from "../../api/ai";
import { DataTable, Loading, Panel } from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { ComparisonPanel } from "./ComparisonPanel";
import { decimal, percent } from "./words";
// Titles and questions are fixed copy; every figure on this screen comes from the API.
const modelCopy: Record<string, { title: string; question: string }> = {
  criticality: {
    title: "Criticidad del paciente",
    question: "¿El paciente necesitará ingreso hospitalario con urgencia?",
  },
  cleaning_delay: {
    title: "Retraso de limpieza",
    question:
      "Cuando una cama entra a limpieza, ¿tardará más de 90 minutos en quedar lista?",
  },
};
export const smallSample = 30;
const bands: Record<string, string> = {
  "<45": "menos de 45 años",
  "45-64": "45 a 64 años",
  ">=65": "65 años o más",
};
const shifts: Record<string, string> = {
  morning: "mañana",
  afternoon: "tarde",
  night: "noche",
};
const sexes: Record<string, string> = { female: "mujeres", male: "hombres" };
export function subgroupLabel(key: string) {
  // Only the first "=" separates kind and value: the band ">=65" has another.
  const cut = key.indexOf("=");
  const kind = cut < 0 ? key : key.slice(0, cut);
  const value = cut < 0 ? "" : key.slice(cut + 1);
  if (kind === "sex") return `Sexo: ${sexes[value] ?? value}`;
  if (kind === "age") return `Edad: ${bands[value] ?? value}`;
  if (kind === "shift") return `Turno: ${shifts[value] ?? value}`;
  if (kind === "service") return `Servicio: ${value}`;
  return key;
}
const orMissing = (
  value: number | null | undefined,
  format: (v: number) => string,
) => (value == null ? "—" : format(value));
function Figures({ metrics }: { metrics: ModelMetrics }) {
  const { evaluation } = metrics;
  const items = [
    [
      "AUC",
      orMissing(evaluation.auc?.mean, (v) => decimal(v, 3)),
      evaluation.auc
        ? `Desviación ${decimal(evaluation.auc.std, 3)} entre repeticiones`
        : "",
    ],
    [
      "Umbral elegido",
      percent(evaluation.threshold),
      "Probabilidad desde la cual se marca",
    ],
    [
      "Sensibilidad en el umbral",
      orMissing(evaluation.at_threshold.recall, percent),
      "De los casos reales, los que se detectan",
    ],
    [
      "Precisión en el umbral",
      orMissing(evaluation.at_threshold.precision, percent),
      "De los marcados, los que eran casos reales",
    ],
  ];
  return (
    <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-4">
      {items.map(([term, value, help]) => (
        <div key={term}>
          <dt className="text-sm font-semibold">{term}</dt>
          <dd>
            <span className="font-display text-xl font-semibold tabular-nums">
              {value}
            </span>
            <span className="mt-0.5 block text-xs text-ink-muted">{help}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
// States the limits of the model using only what the API reports.
function Caveats({ metrics }: { metrics: ModelMetrics }) {
  const { evaluation, validation, ablation } = metrics;
  const notes: string[] = [];
  const auc = evaluation.auc?.mean;
  const { precision } = evaluation.at_threshold;
  if (auc != null && auc < 0.7) {
    const base = evaluation.baseline_flag_everyone.precision;
    notes.push(
      `El AUC es modesto (${decimal(auc, 3)}): el modelo separa poco a los casos críticos de los demás. A este umbral marca al ${percent(evaluation.flagged_rate, 0)} de los pacientes${
        precision != null
          ? ` y su precisión (${percent(precision)}) ${precision <= base + 0.05 ? "apenas supera" : "supera"} la de marcar a todos (${percent(base)})`
          : ""
      }. Es un apoyo a la priorización, no un diagnóstico: la decisión es del clínico.`,
    );
  }
  if (validation.evaluated_on_synthetic_rows)
    notes.push(
      "Estas métricas se calcularon con datos simulados: describen la simulación, no hospitales reales.",
    );
  if (ablation)
    notes.push(
      `El aumento de datos sintéticos en el entrenamiento da un AUC de ${decimal(ablation.auc_with_augmentation, 3)}; sin aumento, ${decimal(ablation.auc_without_augmentation, 3)}.`,
    );
  if (!notes.length) return null;
  return (
    <div className="space-y-2 rounded-lg bg-surface-sunken p-4 text-sm">
      {notes.map((note) => (
        <p key={note}>{note}</p>
      ))}
    </div>
  );
}
function Subgroups({
  groups,
  targetRecall,
}: {
  groups: Record<string, Subgroup>;
  targetRecall: number;
}) {
  const rows = Object.entries(groups);
  if (!rows.length) return null;
  return (
    <DataTable
      caption="Desempeño por subgrupo"
      numeric={[1, 2, 3]}
      headers={["Subgrupo", "n", "Sensibilidad", "AUC"]}
    >
      {rows.map(([key, group]) => {
        const small = group.n < smallSample;
        const below =
          !small && group.recall != null && group.recall < targetRecall;
        return (
          <tr key={key}>
            <td>
              <span className="font-semibold">{subgroupLabel(key)}</span>
              {small && (
                <span className="ml-2 rounded-full bg-bed-cleaning-tint px-2 py-0.5 text-xs font-semibold text-bed-cleaning-ink">
                  muestra pequeña
                </span>
              )}
            </td>
            <td className="tabular-nums">{group.n}</td>
            <td className="tabular-nums">
              {orMissing(group.recall, percent)}
              {below && (
                <small className="block text-xs text-ink-muted">
                  bajo el objetivo de {percent(targetRecall, 0)}
                </small>
              )}
            </td>
            <td className="tabular-nums">
              {orMissing(group.auc, (v) => decimal(v, 3))}
            </td>
          </tr>
        );
      })}
    </DataTable>
  );
}
function Model({ name, metrics }: { name: string; metrics: ModelMetrics }) {
  const copy = modelCopy[name] ?? { title: name, question: "" };
  const target = metrics.evaluation.target_recall;
  const rows = metrics.data.real_rows ?? metrics.data.rows;
  const servedName = metrics.comparacion?.models.find(
    (model) => model.winner,
  )?.nombre;
  return (
    <section aria-label={copy.title} className="space-y-4 px-5 py-5">
      <div>
        <h3 className="text-lg font-semibold">{copy.title}</h3>
        <p className="text-sm text-ink-muted">
          {copy.question}
          {rows != null && (
            <>
              {" "}
              Entrenado con <span className="tabular-nums">{rows}</span> filas.
            </>
          )}
        </p>
        {servedName && (
          <p className="text-sm">
            Modelo en servicio:{" "}
            <strong className="font-semibold">{servedName}</strong>
          </p>
        )}
        <p className="text-xs text-ink-muted">
          Versión{" "}
          <span className="break-all tabular-nums">
            {metrics.model_version}
          </span>
        </p>
      </div>
      <Figures metrics={metrics} />
      <p className="text-sm">
        <strong className="font-semibold">Por qué este umbral:</strong> es el
        más alto cuya sensibilidad alcanza {percent(target, 0)}. Se prefiere no
        dejar pasar un caso, aunque se marquen más pacientes (
        {percent(metrics.evaluation.flagged_rate, 0)} quedan marcados).
      </p>
      <Caveats metrics={metrics} />
      {metrics.comparacion && (
        <ComparisonPanel comparison={metrics.comparacion} task={name} />
      )}
      <Subgroups groups={metrics.bias_by_subgroup} targetRecall={target} />
    </section>
  );
}
export function ModelsSection() {
  const query = useQuery({
    queryKey: ["ai-models"],
    queryFn: ({ signal }) => aiApi.models(signal),
    // An AI outage must not mark the whole page as offline.
    meta: { silent: true },
  });
  return (
    <Panel
      id="modelos"
      title="Modelos de IA"
      description="Comparación de modelos y métricas de validación cruzada leídas del servicio de IA. Ayudan a decidir cuánta confianza darle a cada reporte."
    >
      {query.error ? (
        <ErrorState error={query.error} retry={query.refetch} />
      ) : !query.data ? (
        <Loading />
      ) : (
        (() => {
          const models = Object.entries(query.data).filter(
            (entry): entry is [string, ModelMetrics] =>
              typeof entry[1] === "object",
          );
          return models.length ? (
            <div className="divide-y divide-line">
              {models.map(([name, metrics]) => (
                <Model key={name} name={name} metrics={metrics} />
              ))}
            </div>
          ) : (
            <EmptyState title="El servicio de IA no informó modelos" />
          );
        })()
      )}
    </Panel>
  );
}
