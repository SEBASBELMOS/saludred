import type { ComparedModel, Comparison } from "../../api/ai";
import { DataTable } from "../../components/ui";
import { decimal, percent } from "./words";
// Plain names for the four cells of the confusion matrix, per task.
const matrixWords: Record<
  string,
  { found: string; missed: string; alarm: string; cleared: string }
> = {
  criticality: {
    found: "Críticos detectados",
    missed: "Críticos perdidos",
    alarm: "Falsas alarmas",
    cleared: "No críticos descartados",
  },
  cleaning_delay: {
    found: "Retrasos detectados",
    missed: "Retrasos perdidos",
    alarm: "Falsas alarmas",
    cleared: "A tiempo descartados",
  },
};
const TOP = 5;
const orDash = (value: number | null, format: (v: number) => string) =>
  value == null ? "—" : format(value);
const score = (value: number | null) => orDash(value, (v) => decimal(v, 3));
function ModelsTable({ models }: { models: ComparedModel[] }) {
  return (
    <DataTable
      caption="Comparación de modelos"
      numeric={[1, 2, 3, 4]}
      headers={["Modelo", "AUC", "F1", "Sensibilidad", "Precisión"]}
    >
      {models.map((model) => (
        <tr key={model.id} className={model.winner ? "bg-surface-sunken" : ""}>
          <td>
            <span className="font-semibold">{model.nombre}</span>
            {model.winner && (
              <span className="ml-2 rounded-full bg-bed-available-tint px-2 py-0.5 text-xs font-semibold text-bed-available-ink">
                Ganador
              </span>
            )}
          </td>
          <td className="tabular-nums">
            {orDash(model.auc?.mean ?? null, (v) => decimal(v, 3))}
          </td>
          <td className="tabular-nums font-semibold">{score(model.f1)}</td>
          <td className="tabular-nums">{orDash(model.recall, percent)}</td>
          <td className="tabular-nums">{orDash(model.precision, percent)}</td>
        </tr>
      ))}
    </DataTable>
  );
}
function ConfusionMatrix({
  model,
  task,
  repeats,
}: {
  model: ComparedModel;
  task: string;
  repeats: number;
}) {
  const words = matrixWords[task] ?? matrixWords.criticality;
  const { tp, fn, fp, tn } = model.confusion;
  const count = (value: number) => decimal(value, 1);
  return (
    <div>
      <table className="w-full max-w-xl border-separate border-spacing-1 text-sm">
        <caption className="pb-2 text-left text-sm font-semibold">
          Matriz de confusión de {model.nombre}
        </caption>
        <thead>
          <tr>
            <td />
            <th scope="col" className="px-2 text-xs font-semibold text-ink-muted">
              El modelo marca
            </th>
            <th scope="col" className="px-2 text-xs font-semibold text-ink-muted">
              El modelo no marca
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row" className="pr-2 text-left text-xs font-semibold text-ink-muted">
              Caso real
            </th>
            <td className="rounded-lg bg-surface-sunken p-3">
              <span className="block font-display text-xl font-semibold tabular-nums">
                {count(tp)}
              </span>
              <span className="text-xs">{words.found}</span>
              <span className="block text-xs text-ink-muted">TP</span>
            </td>
            <td className="rounded-lg bg-critical-tint p-3 text-critical-ink">
              <span className="block font-display text-xl font-semibold tabular-nums">
                {count(fn)}
              </span>
              <span className="text-xs font-semibold">{words.missed}</span>
              <span className="block text-xs">FN</span>
            </td>
          </tr>
          <tr>
            <th scope="row" className="pr-2 text-left text-xs font-semibold text-ink-muted">
              No es caso
            </th>
            <td className="rounded-lg bg-surface-sunken p-3">
              <span className="block font-display text-xl font-semibold tabular-nums">
                {count(fp)}
              </span>
              <span className="text-xs">{words.alarm}</span>
              <span className="block text-xs text-ink-muted">FP</span>
            </td>
            <td className="rounded-lg bg-surface-sunken p-3">
              <span className="block font-display text-xl font-semibold tabular-nums">
                {count(tn)}
              </span>
              <span className="text-xs">{words.cleared}</span>
              <span className="block text-xs text-ink-muted">TN</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p className="mt-2 text-xs text-ink-muted">
        Conteo promedio por repetición ({repeats} repeticiones de validación
        cruzada), con el umbral{" "}
        <span className="tabular-nums">{percent(model.threshold)}</span>. F1{" "}
        <span className="tabular-nums">{score(model.f1)}</span>.
      </p>
    </div>
  );
}
function Importance({ comparison }: { comparison: Comparison }) {
  const { importance } = comparison;
  const items = (importance.by_model[importance.shown_model] ?? []).slice(
    0,
    TOP,
  );
  if (!items.length) return null;
  const top = Math.max(...items.map((item) => item.importance), 0.0001);
  return (
    <div>
      <h5 className="text-sm font-semibold">
        Variables que más pesan ({importance.shown_model_name})
      </h5>
      <ul className="mt-2 space-y-1.5">
        {items.map((item) => (
          <li
            key={item.feature}
            className="grid grid-cols-[minmax(0,12rem)_1fr_3.5rem] items-center gap-3 text-sm"
          >
            <span className="truncate">{item.label}</span>
            <span aria-hidden className="h-2 rounded-full bg-surface-sunken">
              <span
                className="block h-2 rounded-full bg-accent"
                style={{
                  width: `${Math.max(0, (item.importance / top) * 100)}%`,
                }}
              />
            </span>
            <span className="text-right tabular-nums">
              {decimal(item.importance, 3)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-ink-muted">{importance.reading}</p>
    </div>
  );
}
function OddsRatios({ comparison }: { comparison: Comparison }) {
  const { items, model_name: name } = comparison.odds_ratios;
  const rows = items.slice(0, TOP);
  if (!rows.length) return null;
  return (
    <div className="space-y-2">
      <DataTable
        caption={`Razones de momios (${name})`}
        numeric={[1]}
        headers={["Variable", "Razón de momios", "Lectura"]}
      >
        {rows.map((item) => (
          <tr key={item.feature}>
            <td className="font-semibold">{item.label}</td>
            <td className="tabular-nums">{decimal(item.odds_ratio, 2)}</td>
            <td className="text-xs text-ink-muted">{item.reading}</td>
          </tr>
        ))}
      </DataTable>
      <p className="text-xs text-ink-muted">
        Los momios (odds) son la probabilidad de que ocurra el evento dividida
        entre la de que no ocurra. La razón de momios compara los momios al
        subir una desviación estándar la variable: 1 significa sin efecto, más
        de 1 aumenta y menos de 1 disminuye.
      </p>
    </div>
  );
}
export function ComparisonPanel({
  comparison,
  task,
}: {
  comparison: Comparison;
  task: string;
}) {
  const winner = comparison.models.find((model) => model.winner);
  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-base font-semibold">Comparación de modelos</h4>
        <p className="text-sm text-ink-muted">
          {comparison.models.length} modelos con el mismo protocolo (
          {comparison.protocol.repeats} repeticiones de validación cruzada de{" "}
          {comparison.protocol.folds} folds). La métrica principal es el F1 con
          el umbral elegido fuera de muestra.
        </p>
      </div>
      <ModelsTable models={comparison.models} />
      <p className="text-sm text-ink-muted">
        Línea base: marcar a todos da F1{" "}
        <span className="tabular-nums">
          {decimal(comparison.baseline_flag_everyone.f1, 3)}
        </span>{" "}
        y precisión{" "}
        <span className="tabular-nums">
          {percent(comparison.baseline_flag_everyone.precision)}
        </span>
        .{" "}
        {comparison.models.some(
          (model) =>
            model.f1 != null && model.f1 > comparison.baseline_flag_everyone.f1,
        )
          ? "Los modelos que la superan en F1 aportan algo sobre no filtrar."
          : "Ningún modelo la supera en F1: con tantos casos positivos marcar a todos ya da un F1 alto; el aporte de los modelos es descartar casos (especificidad)."}
      </p>
      <p className="text-sm">
        <strong className="font-semibold">Cómo se eligió el ganador:</strong>{" "}
        {comparison.rule}
      </p>
      {winner && (
        <div className="grid gap-6 lg:grid-cols-2">
          <ConfusionMatrix
            model={winner}
            task={task}
            repeats={comparison.protocol.repeats}
          />
          <Importance comparison={comparison} />
        </div>
      )}
      <OddsRatios comparison={comparison} />
    </div>
  );
}
