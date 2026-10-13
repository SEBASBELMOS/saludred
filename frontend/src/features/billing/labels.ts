import { ApiError } from "../../api/client";
import type { EstadoPago, Servicio } from "../../api/billing";

export const servicioNames: Record<Servicio, string> = {
  analisis_ia: "Análisis de IA",
  dia_cama: "Día cama",
  remision: "Remisión",
  consulta: "Consulta",
  medicamento: "Medicamento",
};
export const estadoNames: Record<EstadoPago, string> = {
  pendiente: "Pendiente",
  pagada: "Pagada",
  anulada: "Anulada",
};
export const estadoPlural: Record<EstadoPago, string> = {
  pendiente: "pendientes",
  pagada: "pagadas",
  anulada: "anuladas",
};
// Chip tones reuse the state palette: amber waits, green is settled, grey is void.
export const estadoClasses: Record<EstadoPago, string> = {
  pendiente: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  pagada: "bg-bed-available-tint text-bed-available-ink",
  anulada: "bg-bed-blocked-tint text-bed-blocked-ink",
};
// A symbol next to the word, so the state does not rely on colour alone.
export const estadoSymbols: Record<EstadoPago, string> = {
  pendiente: "●",
  pagada: "✓",
  anulada: "×",
};
export const BILLING_UNAVAILABLE =
  "La contabilidad no está disponible en este momento. Las facturas automáticas se crearán al conciliar.";
export const shortId = (id: string) => id.slice(0, 8);
export const isConflict = (error: unknown) =>
  error instanceof ApiError && error.status === 409;
export const isUnavailable = (error: unknown) =>
  error instanceof ApiError && error.status === 503;
// The API message is already in Spanish, except the 503 which gets the product wording.
export const billingMessage = (error: Error) =>
  isUnavailable(error) ? BILLING_UNAVAILABLE : error.message;
