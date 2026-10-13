import { api, queryString } from "./client";

export type AlertKind = "paciente_critico" | "cama_sin_limpiar";
export type AlertState = "activa" | "reconocida";

export interface Alert {
  id: string;
  paciente_id: string | null;
  // "critico" or "alto".
  nivel: string;
  estado: AlertState;
  kind: AlertKind;
  mensaje: string;
  created_at: string;
  reporte_id?: string | null;
  cama_id?: string | null;
  organizacion_id?: string | null;
  destinatario_id?: string | null;
  escalated_at?: string | null;
  escalated_to?: string[] | null;
  reconocida_por?: string | null;
  reconocida_por_nombre?: string | null;
  reconocida_en?: string | null;
  nota_reconocimiento?: string | null;
  datos?: Record<string, unknown> | null;
}

// The three counters of R18, for the signed-in user.
export interface Pending {
  reportes_por_revisar: number;
  remisiones_recibidas: number;
  alertas_activas: number;
}

export const alertsApi = {
  list: (estado: AlertState, signal?: AbortSignal) =>
    api<Alert[]>(`/alerts?${queryString({ estado })}`, { signal }),
  acknowledge: (id: string, nota: string) =>
    api<Alert>(`/alerts/${id}/acknowledge`, {
      method: "POST",
      body: { nota },
    }),
  pending: (signal?: AbortSignal) => api<Pending>("/pendientes", { signal }),
};
