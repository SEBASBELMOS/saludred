const months = [
  "ene",
  "feb",
  "mar",
  "abr",
  "may",
  "jun",
  "jul",
  "ago",
  "sep",
  "oct",
  "nov",
  "dic",
];
// One date family: dates read "13 may 1976", date-times "2 oct, 20:36".
// The year of a date-time is only written when it is not the current year.
export const dateOnly = (value?: string | null) => {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match
    ? `${Number(match[3])} ${months[Number(match[2]) - 1]} ${match[1]}`
    : "—";
};
export const dateTime = (value?: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const year =
    date.getFullYear() === new Date().getFullYear()
      ? ""
      : ` ${date.getFullYear()}`;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getDate()} ${months[date.getMonth()]}${year}, ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
export const minutes = (value: number) => {
  const n = Math.max(0, Math.round(value));
  return n >= 1440
    ? `${Math.floor(n / 1440)} d ${Math.floor((n % 1440) / 60)} h`
    : n >= 60
      ? `${Math.floor(n / 60)} h ${n % 60} min`
      : `${n} min`;
};
export const fullName = (p: { first_name: string; last_name: string }) =>
  `${p.first_name} ${p.last_name}`;
export const normalize = (value: string) =>
  value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
export const localDateTime = () => {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
export const today = () => localDateTime().slice(0, 10);
export const profileNames: Record<string, string> = {
  HEALTHY: "Sin comorbilidad",
  HYPERTENSIVE: "Hipertensión",
  DIABETIC: "Diabetes",
  CARDIAC: "Cardiopatía",
  RESPIRATORY: "Enfermedad respiratoria",
  ELDERLY_FRAIL: "Adulto mayor frágil",
  SIN_PERFIL: "Sin perfil",
};
export const genders = {
  female: "Femenino",
  male: "Masculino",
  other: "Otro",
  unknown: "Sin dato",
};
export const documentTypes = {
  CC: "Cédula de ciudadanía",
  TI: "Tarjeta de identidad",
  CE: "Cédula de extranjería",
  PA: "Pasaporte",
  RC: "Registro civil",
};
export const encounterClasses = {
  EMER: "Urgencias",
  IMP: "Hospitalización",
  AMB: "Ambulatorio",
};
export const encounterStatuses = {
  planned: "Planeada",
  arrived: "En admisión",
  "in-progress": "En curso",
  finished: "Finalizada",
  cancelled: "Cancelada",
};
// Colombian pesos: "$ 1.250.000", no decimals.
const copFormat = new Intl.NumberFormat("es-CO", {
  maximumFractionDigits: 0,
  useGrouping: true,
});
export const cop = (value: number) => {
  const digits = copFormat.format(Math.round(value));
  // Some ICU builds skip the thousands separator for 4-digit es-CO numbers.
  const grouped = /^\d{4,}$/.test(digits)
    ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".")
    : digits;
  return `$ ${grouped}`;
};
