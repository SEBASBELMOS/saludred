import type { AiFeature, AiReportStatus } from "../../api/ai";
export const estados: Record<AiReportStatus, string> = {
  pendiente: "Pendiente de firma",
  aprobado: "Aprobado",
  rechazado: "Rechazado",
};
// Plain-words names and units for each variable the model receives.
export const variables: Record<string, { label: string; unit?: string }> = {
  pseudonym: { label: "Seudónimo" },
  age: { label: "Edad", unit: "años" },
  sex: { label: "Sexo" },
  temperature_c: { label: "Temperatura", unit: "°C" },
  heart_rate: { label: "Frecuencia cardíaca", unit: "lat/min" },
  resp_rate: { label: "Frecuencia respiratoria", unit: "resp/min" },
  o2sat: { label: "Saturación de oxígeno", unit: "%" },
  sbp: { label: "Presión sistólica", unit: "mmHg" },
  dbp: { label: "Presión diastólica", unit: "mmHg" },
  acuity: { label: "Nivel de triage ESI" },
};
export const variableLabel = (key: string) => variables[key]?.label ?? key;
const sexes: Record<string, string> = { male: "hombre", female: "mujer" };
export const percent = (value: number, digits = 1) =>
  `${(value * 100).toLocaleString("es-CO", { maximumFractionDigits: digits })} %`;
export const decimal = (value: number, digits = 2) =>
  value.toLocaleString("es-CO", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
export function variableValue(key: string, value: unknown): string {
  if (typeof value === "string") return sexes[value] ?? value;
  if (typeof value !== "number") return String(value);
  const unit = variables[key]?.unit;
  const text = value.toLocaleString("es-CO", { maximumFractionDigits: 1 });
  return unit ? `${text} ${unit}` : text;
}
export const featureLine = (feature: AiFeature) =>
  `${variableLabel(feature.feature)} ${variableValue(feature.feature, feature.value)}`;
export const nivelName = (critical: boolean) =>
  critical ? "Crítico" : "No crítico";
