import { roleNames } from "../auth/permissions";
import type { Role } from "../../api/types";

const labels: Record<string, string> = {
  username: "Usuario",
  reason: "Motivo",
  outcome: "Resultado",
  role: "Rol",
  fhir_resource: "Recurso FHIR",
  failed_attempts: "Intentos fallidos",
  bed_code: "Cama",
  priority: "Prioridad",
  action: "Acción",
  changed_fields: "Campos modificados",
};
const outcomes: Record<string, string> = {
  success: "correcto",
  ok: "correcto",
  invalid_credentials: "credenciales incorrectas",
  locked: "cuenta bloqueada",
  failed: "fallido",
  error: "con error",
};
const priorities: Record<string, string> = {
  EMERGENCY: "emergencia",
  URGENT: "urgente",
  ROUTINE: "rutina",
};
const actions: Record<string, string> = {
  photo_uploaded: "foto cargada",
  approved: "aprobada",
  rejected: "rechazada",
  created: "creada",
};
const lookup: Record<string, Record<string, string>> = {
  outcome: outcomes,
  priority: priorities,
  action: actions,
};
const humanize = (key: string) => {
  const text = key.replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const show = (key: string, value: unknown): string => {
  if (Array.isArray(value))
    return value.map((item) => show(key, item)).join(", ");
  const text = String(value);
  if (key === "role") return roleNames[text as Role] ?? text;
  return lookup[key]?.[text] ?? text;
};
/**
 * Plain-Spanish summary of an audit entry's metadata, e.g. "Resultado: correcto".
 * Known keys get a label and translated values; unknown ones fall back to a
 * readable version of the key. Identifiers and nested objects are left out.
 */
export function auditDetail(metadata?: Record<string, unknown> | null) {
  return Object.entries(metadata ?? {})
    .filter(([key, value]) => {
      if (value == null || value === "" || typeof value === "object") {
        return Array.isArray(value) && value.length > 0;
      }
      return (
        !key.endsWith("_id") && key !== "manual" && key !== "override_priority"
      );
    })
    .map(
      ([key, value]) => `${labels[key] ?? humanize(key)}: ${show(key, value)}`,
    )
    .join("; ")
    .slice(0, 300);
}
