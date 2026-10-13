import { api } from "./client";

export type ReferralState =
  | "pendiente"
  | "aceptada"
  | "rechazada"
  | "completada";

export interface Referral {
  id: string;
  paciente_id: string;
  paciente_nombre: string;
  motivo: string;
  estado: ReferralState;
  remitente_id: string;
  remitente_nombre: string;
  especialista_id: string;
  especialista_nombre: string;
  especialista_organizacion: string | null;
  motivo_rechazo: string | null;
  informe: string | null;
  creada_en: string;
  aceptada_en: string | null;
  rechazada_en: string | null;
  completada_en: string | null;
}

export interface Specialist {
  id: string;
  nombre: string;
  organizacion: string | null;
}

export const referralsApi = {
  // The API picks the side by role: recibidas for the especialista, enviadas
  // for the medico, everything for the admin.
  list: (signal?: AbortSignal) => api<Referral[]>("/referrals", { signal }),
  specialists: (signal?: AbortSignal) =>
    api<Specialist[]>("/referrals/specialists", { signal }),
  create: (body: {
    paciente_id: string;
    especialista_id: string;
    motivo: string;
  }) => api<Referral>("/referrals", { method: "POST", body }),
  accept: (id: string) =>
    api<Referral>(`/referrals/${id}/accept`, { method: "POST" }),
  reject: (id: string, motivo: string) =>
    api<Referral>(`/referrals/${id}/reject`, { method: "POST", body: { motivo } }),
  report: (id: string, informe: string) =>
    api<Referral>(`/referrals/${id}/report`, {
      method: "POST",
      body: { informe },
    }),
};
