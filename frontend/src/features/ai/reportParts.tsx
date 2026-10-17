import type { AiFeature, AiReportStatus } from "../../api/ai";
import { dateTime } from "../../components/format";
import {
  estados,
  featureLine,
  nivelName,
  percent,
  variableLabel,
} from "./words";
// Calm treatments: pending is ochre (waiting for a person), approved is green, rejected is slate. Red is left for "crítico".
const estadoTone: Record<AiReportStatus, string> = {
  pendiente: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  aprobado: "bg-bed-available-tint text-bed-available-ink",
  rechazado: "bg-bed-occupied-tint text-bed-occupied-ink",
};
export function EstadoChip({ estado }: { estado: AiReportStatus }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${estadoTone[estado]}`}
    >
      {estados[estado]}
    </span>
  );
}
export function NivelChip({ critical }: { critical: boolean }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${critical ? "bg-critical-tint text-critical-ink" : "bg-surface-sunken text-ink-muted"}`}
    >
      {nivelName(critical)}
    </span>
  );
}
export function Probability({
  value,
  critical,
  threshold,
}: {
  value: number;
  critical: boolean;
  threshold?: number | null;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="font-display text-xl font-semibold tabular-nums">
        {percent(value)}
      </span>
      <NivelChip critical={critical} />
      <span className="text-sm text-ink-muted">
        probabilidad de ingreso urgente
        {threshold != null && (
          <>
            , umbral <span className="tabular-nums">{percent(threshold)}</span>
          </>
        )}
      </span>
    </div>
  );
}
export function TopFeatures({ features }: { features: AiFeature[] }) {
  if (!features.length) return null;
  return (
    <div>
      <dt className="text-xs text-ink-muted">Lo que más pesó</dt>
      <dd className="mt-1">
        <ul className="space-y-0.5 text-sm">
          {features.map((feature) => (
            <li key={feature.feature}>
              <span className="font-semibold tabular-nums">
                {featureLine(feature)}
              </span>
              <span className="text-ink-muted">
                {" "}
                {feature.contribution >= 0
                  ? "sube la probabilidad"
                  : "baja la probabilidad"}
                {feature.imputed &&
                  "; estimado a partir de pacientes parecidos, no medido"}
              </span>
            </li>
          ))}
        </ul>
      </dd>
    </div>
  );
}
export function Signature({
  estado,
  signedBy,
  reviewedAt,
  observaciones,
  motivo,
}: {
  estado: AiReportStatus;
  signedBy: string | null;
  reviewedAt: string | null;
  observaciones: string | null;
  motivo?: string | null;
}) {
  if (estado === "pendiente") return null;
  // The clinician's judgment: the note of an approval, the reason of a rejection.
  const concepto = estado === "aprobado" ? observaciones : motivo;
  const extra = estado === "rechazado" ? observaciones : null;
  return (
    <>
      <div>
        <dt className="text-xs text-ink-muted">
          {estado === "aprobado" ? "Firmado por" : "Rechazado por"}
        </dt>
        <dd className="mt-1 text-sm font-semibold">
          {signedBy ?? "Sin registrar"}
          <span className="font-normal text-ink-muted tabular-nums">
            {" "}
            {dateTime(reviewedAt)}
          </span>
        </dd>
      </div>
      <div className="rounded-lg border border-line bg-surface-sunken p-3 sm:col-span-2">
        <dt className="text-xs font-semibold text-ink-muted">Concepto clínico</dt>
        <dd className="mt-1 whitespace-pre-line break-words text-sm font-semibold">
          {concepto ?? (
            <span className="font-normal text-ink-muted">
              Sin concepto clínico registrado.
            </span>
          )}
        </dd>
      </div>
      {extra && (
        <div className="sm:col-span-2">
          <dt className="text-xs text-ink-muted">Observaciones</dt>
          <dd className="mt-1 break-words text-sm">{extra}</dd>
        </div>
      )}
    </>
  );
}
const origenText = { automatico: "Automático", manual: "Manual" } as const;
/** Who made the report: the system on its own, or a clinician's request. */
export function OrigenChip({ origen }: { origen?: "automatico" | "manual" }) {
  if (!origen) return null;
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-full border border-line px-2 py-0.5 text-xs text-ink-muted">
      {origenText[origen]}
    </span>
  );
}
export const imputedList = (keys: string[]) =>
  keys.length ? keys.map(variableLabel).join(", ") : "Ninguna";
