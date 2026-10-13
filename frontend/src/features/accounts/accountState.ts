import { ApiError } from "../../api/client";
import type { Role } from "../../api/types";

// Which institution each role belongs to, in the words of the new-account form.
export const roleLink: Partial<Record<Role, string>> = {
  IPS_CLINICAL_OPERATOR: "Pertenece a una IPS: solicita camas y remite.",
  SPECIALIST:
    "Pertenece a una IPS: recibe remisiones, ve pacientes, cola y camas de su IPS.",
  EPS_COORDINATOR: "Pertenece a una EPS: asigna camas y aprueba limpiezas.",
  ACCOUNTANT: "Pertenece a una EPS: ve y cobra las facturas, sin datos clínicos.",
  PATIENT: "Se vincula a su ficha por el número de documento.",
};

// The server writes plain Spanish without accents and, for malformed fields, the
// pydantic text; both are turned into a sentence that says what to fix.
export function accountError(error: Error, role: Role): string {
  if (!(error instanceof ApiError)) return error.message;
  const text = error.message;
  if (error.status === 403) return "Solo la administración crea cuentas.";
  if (error.status === 409) {
    if (/ya existe una cuenta/i.test(text))
      return "Ese usuario ya existe. Elige otro nombre de usuario.";
    if (/ya tiene una cuenta/i.test(text))
      return "Ese paciente ya tiene una cuenta de acceso.";
    return text;
  }
  if (error.status === 422) {
    const need = text.match(/organizacion de tipo (EPS|IPS)/i)?.[1];
    if (need)
      return need.toUpperCase() === "IPS"
        ? `El rol ${roleLabel(role)} pertenece a una IPS: selecciona una IPS, no una EPS.`
        : `El rol ${roleLabel(role)} pertenece a una EPS: selecciona una EPS, no una IPS.`;
    if (/necesita una organizacion|organization_id/i.test(text))
      return role === "SPECIALIST" || role === "IPS_CLINICAL_OPERATOR"
        ? "Selecciona la IPS de esta cuenta."
        : "Selecciona la EPS de esta cuenta.";
    if (/no hay un paciente/i.test(text))
      return "No hay un paciente con ese documento. Regístralo primero o revisa el número.";
    if (/nombre completo/i.test(text))
      return "Escribe el nombre completo de la persona.";
    if (/username/i.test(text))
      return "El usuario admite de 3 a 80 caracteres: minúsculas, números, punto, guion y guion bajo.";
    if (/password/i.test(text))
      return "La clave debe tener entre 8 y 128 caracteres.";
    if (/email/i.test(text)) return "El correo no tiene un formato válido.";
    return `El servidor rechazó los datos de la cuenta: ${text}`;
  }
  return text;
}
const labels: Partial<Record<Role, string>> = {
  IPS_CLINICAL_OPERATOR: "médico",
  SPECIALIST: "especialista",
  EPS_COORDINATOR: "coordinador",
  ACCOUNTANT: "contable",
  PATIENT: "paciente",
  ADMIN: "administración",
};
const roleLabel = (role: Role) => labels[role] ?? "seleccionado";
