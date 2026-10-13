export type SearchMode = "auto" | "document_number" | "name";
export function searchCriterion(
  term: string,
  mode: SearchMode = "auto",
): { key: "name" | "document_number"; value: string; error?: string } | null {
  const value = term.trim();
  if (!value) return null;
  const key =
    mode === "auto" ? (/\d/.test(value) ? "document_number" : "name") : mode;
  if (key === "document_number" && value.length < 6)
    return {
      key,
      value,
      error: "Escribe el documento completo: al menos 6 caracteres.",
    };
  if (key === "name" && (value.match(/\p{L}/gu) || []).length < 3)
    return {
      key,
      value,
      error: "Escribe al menos 3 letras del nombre o apellido.",
    };
  return { key, value };
}
export const searchCount = (total: number) =>
  total > 10
    ? `${total} coincidencias. Se muestran las 10 primeras.`
    : `${total} ${total === 1 ? "coincidencia" : "coincidencias"}`;
export const vitalCatalog = [
  {
    code: "8480-6",
    name: "Presión sistólica",
    unit: "mmHg",
    ucum: "mm[Hg]",
    min: 50,
    max: 260,
  },
  {
    code: "8462-4",
    name: "Presión diastólica",
    unit: "mmHg",
    ucum: "mm[Hg]",
    min: 30,
    max: 150,
  },
  {
    code: "8867-4",
    name: "Frecuencia cardíaca",
    unit: "lat/min",
    ucum: "/min",
    min: 30,
    max: 220,
  },
  {
    code: "9279-1",
    name: "Frecuencia respiratoria",
    unit: "resp/min",
    ucum: "/min",
    min: 6,
    max: 60,
  },
  {
    code: "59408-5",
    name: "Saturación de oxígeno",
    unit: "%",
    ucum: "%",
    min: 50,
    max: 100,
  },
  {
    code: "8310-5",
    name: "Temperatura",
    unit: "°C",
    ucum: "Cel",
    min: 32,
    max: 42,
  },
  {
    code: "2339-0",
    name: "Glucosa",
    unit: "mg/dL",
    ucum: "mg/dL",
    min: 20,
    max: 800,
  },
  { code: "29463-7", name: "Peso", unit: "kg", ucum: "kg", min: 1, max: 400 },
  { code: "8302-2", name: "Talla", unit: "cm", ucum: "cm", min: 30, max: 250 },
];
export function validateVitals(values: Record<string, string>) {
  const readings = vitalCatalog.flatMap((vital) => {
    const raw = values[vital.code]?.trim();
    if (!raw) return [];
    const value = Number(raw.replace(",", "."));
    if (!Number.isFinite(value) || value < vital.min || value > vital.max)
      throw new Error(
        `${vital.name}: ${raw} está fuera del rango válido (${vital.min}–${vital.max}).`,
      );
    return [{ ...vital, value }];
  });
  const systolic = readings.find((r) => r.code === "8480-6");
  const diastolic = readings.find((r) => r.code === "8462-4");
  if (systolic && diastolic && systolic.value <= diastolic.value)
    throw new Error("La presión sistólica debe ser mayor que la diastólica.");
  return readings;
}
