import { ApiError } from "../../api/client";
import type {
  CleaningDisinfectant,
  CleaningIsolation,
  CleaningStatus,
} from "../../api/cleaning";

// The 11 surfaces of the CDC terminal-cleaning checklist, in inspection order.
// The keys are the contract with the API; every one must be ticked.
export const checklistItems: [string, string][] = [
  ["barandas", "Barandas de la cama"],
  ["colchon", "Colchón"],
  ["mesa_auxiliar", "Mesa auxiliar"],
  ["timbre_y_controles", "Timbre y controles de la cama"],
  ["poste_de_suero", "Poste de suero"],
  ["manijas_e_interruptores", "Manijas de puertas e interruptores"],
  ["cortinas", "Cortinas"],
  ["bano", "Baño"],
  ["piso", "Piso"],
  ["residuos", "Retiro de residuos"],
  ["ropa_de_cama", "Cambio de ropa de cama"],
];
export const isolations: Record<CleaningIsolation, string> = {
  NONE: "Sin aislamiento",
  CONTACT: "Contacto",
  DROPLET: "Gotas",
  SPORES: "Esporas",
};
export const disinfectants: Record<CleaningDisinfectant, string> = {
  CHLORINE_5000PPM: "Cloro a 5000 ppm",
  QUATERNARY_AMMONIUM: "Amonio cuaternario",
  SPORICIDAL: "Esporicida",
  OTHER: "Otro",
};
export const statusLabels: Record<CleaningStatus, string> = {
  PENDING_REVIEW: "Pendiente de revisión",
  APPROVED: "Aprobada",
  REJECTED: "Rechazada",
};
// Chip colours. Pending keeps the cleaning ochre, approved the available green and
// rejected the urgent orange: red stays reserved for critical situations.
export const statusClasses: Record<CleaningStatus, string> = {
  PENDING_REVIEW: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  APPROVED: "bg-bed-available-tint text-bed-available-ink",
  REJECTED: "bg-urgent-tint text-urgent-ink",
};
export const statusSymbols: Record<CleaningStatus, string> = {
  PENDING_REVIEW: "↻",
  APPROVED: "+",
  REJECTED: "×",
};

export interface CleaningDraft {
  checklist: Record<string, boolean>;
  isolation: CleaningIsolation;
  disinfectant: CleaningDisinfectant;
  curtainsChanged: boolean;
  startedAt: string;
  finishedAt: string;
  hasPhoto: boolean;
}
// The server repeats these rules; checking them here lets the form say what is
// missing before anything is sent.
export function cleaningProblems(draft: CleaningDraft, now = new Date()) {
  const problems: string[] = [];
  const missing = checklistItems.filter(([key]) => !draft.checklist[key]);
  if (missing.length)
    problems.push(
      `Falta marcar ${missing.length} de ${checklistItems.length} puntos de la lista.`,
    );
  if (draft.isolation !== "NONE" && !draft.curtainsChanged)
    problems.push("Una habitación con aislamiento exige cambio de cortinas.");
  if (draft.isolation === "SPORES" && draft.disinfectant !== "SPORICIDAL")
    problems.push("El aislamiento por esporas exige desinfectante esporicida.");
  const started = new Date(draft.startedAt).getTime();
  const finished = new Date(draft.finishedAt).getTime();
  if (Number.isNaN(started) || Number.isNaN(finished))
    problems.push("Indica la hora de inicio y de fin de la limpieza.");
  else {
    if (finished < started)
      problems.push("La limpieza no puede terminar antes de empezar.");
    if (finished > now.getTime() + 60_000)
      problems.push("La hora de fin no puede ser futura.");
  }
  if (!draft.hasPhoto)
    problems.push("Agrega la foto de la cama limpia: sin ella no se puede aprobar.");
  return problems;
}

const photoTypes = ["image/png", "image/jpeg"];
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024;
export const MAX_PHOTO_SIDE = 4096;
export function photoProblem(file: { type: string; size: number }) {
  if (!photoTypes.includes(file.type))
    return "Solo se aceptan fotos PNG o JPEG.";
  return null;
}
// A phone camera often produces a picture larger than the server accepts (5 MB or
// 4096 px a side). Shrink it before uploading; if the browser cannot, send it as is.
export async function preparePhoto(file: File): Promise<Blob> {
  if (typeof createImageBitmap !== "function") return file;
  try {
    const bitmap = await createImageBitmap(file);
    const longest = Math.max(bitmap.width, bitmap.height);
    if (file.size <= MAX_PHOTO_BYTES && longest <= MAX_PHOTO_SIDE) {
      bitmap.close();
      return file;
    }
    const scale = Math.min(1, 2560 / longest);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((done) =>
      canvas.toBlob(done, "image/jpeg", 0.85),
    );
    return blob ?? file;
  } catch {
    return file;
  }
}

export const percent = (value: number) =>
  `${Math.round(value * 100).toLocaleString("es-CO")} %`;

// What the server says, in the words the screen uses. Server messages are plain
// Spanish without accents; the known ones are rewritten with the next step.
export function cleaningError(error: Error): string {
  if (error instanceof ApiError) {
    const text = error.message;
    if (error.status === 403 && /no puede aprobarla/i.test(text))
      return "No puedes aprobar una limpieza que registraste tú. La debe revisar otra persona de la coordinación.";
    if (error.status === 403)
      return "Tu rol no puede realizar esta acción sobre la limpieza.";
    if (error.status === 409 && /foto/i.test(text) && /falta/i.test(text))
      return "Falta la foto de evidencia. Sube una foto de la cama limpia y vuelve a aprobar.";
    if (error.status === 409 && /ya fue revisado/i.test(text))
      return "Otra persona ya revisó este registro. Actualizamos la lista.";
    if (error.status === 409 && /solo se registra limpieza/i.test(text))
      return "La cama ya no está en limpieza, así que no se puede registrar otra limpieza.";
    if (error.status === 409 && /solo se puede cambiar la foto/i.test(text))
      return "La foto solo se cambia mientras el registro está pendiente de revisión.";
    if (error.status === 422 && /motivo/i.test(text))
      return "Para rechazar un registro escribe el motivo: quien lo registró lo leerá.";
    if (/archivo supera|5 MB/i.test(text))
      return "La foto supera el máximo de 5 MB. Toma otra con menos resolución.";
    if (/4096/.test(text))
      return "La foto es demasiado grande: debe medir como máximo 4096 px por lado.";
    if (/png o jpeg/i.test(text)) return "Solo se aceptan fotos PNG o JPEG.";
  }
  return error.message;
}

// A bed cannot leave cleaning without an approved record when the server enforces
// the evidence rule (REQUIRE_CLEANING_EVIDENCE). The server answers 409.
export function bedBlockedByEvidence(error: Error | null): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    /evidencia de limpieza/i.test(error.message)
  );
}
export const evidenceBlockedMessage =
  "Esta cama no puede quedar disponible todavía: falta una limpieza aprobada. Registra la limpieza con su foto y pide a otra persona de la coordinación que la apruebe.";
