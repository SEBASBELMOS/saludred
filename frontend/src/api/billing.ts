import { api, queryString, request } from "./client";
import { saveResponse } from "./download";

export type Servicio =
  "analisis_ia" | "dia_cama" | "remision" | "consulta" | "medicamento";
export type EstadoPago = "pendiente" | "pagada" | "anulada";

export interface HistorialEntry {
  estado: EstadoPago;
  fecha: string;
  usuario: string;
  nota?: string | null;
}
export interface Factura {
  id: string;
  paciente_id: string;
  eps_id?: string | null;
  ips_id?: string | null;
  servicio: Servicio;
  descripcion: string;
  cantidad: number;
  valor_unitario: number;
  valor: number;
  fecha: string;
  estado_pago: EstadoPago;
  origen: string;
  creado_por: string;
  reporte_id?: string | null;
  solicitud_cama_id?: string | null;
  cama_id?: string | null;
  remision_id?: string | null;
  historial: HistorialEntry[];
}
export interface Total {
  cantidad: number;
  valor: number;
}
export interface Resumen {
  total_facturado: Total;
  por_estado: Record<string, Total>;
  por_servicio: Record<string, Total>;
}
export interface ConciliacionGrupo {
  revisados: number;
  creadas: number;
  ya_facturados: number;
}
export interface Conciliacion {
  analisis_ia: ConciliacionGrupo;
  dia_cama: ConciliacionGrupo;
  total_creadas: number;
}
export interface FacturaFilters {
  estado_pago?: string;
  servicio?: string;
  desde?: string;
  hasta?: string;
}
export interface FacturaInput {
  paciente_id: string;
  servicio: "consulta" | "medicamento";
  descripcion: string;
  cantidad: number;
  valor_unitario?: number;
}

export const billingApi = {
  list: (filters: FacturaFilters, signal?: AbortSignal) =>
    api<Factura[]>(`/facturas?${queryString({ ...filters, limit: 200 })}`, {
      signal,
    }),
  summary: (signal?: AbortSignal) =>
    api<Resumen>("/facturas/resumen", { signal }),
  get: (id: string, signal?: AbortSignal) =>
    api<Factura>(`/facturas/${id}`, { signal }),
  create: (body: FacturaInput) =>
    api<Factura>("/facturas", { method: "POST", body }),
  pay: (id: string, nota?: string) =>
    api<Factura>(`/facturas/${id}/pagar`, {
      method: "POST",
      body: nota ? { nota } : {},
    }),
  void: (id: string, motivo: string) =>
    api<Factura>(`/facturas/${id}/anular`, {
      method: "POST",
      body: { motivo },
    }),
  reconcile: () =>
    api<Conciliacion>("/facturas/reconciliar", { method: "POST" }),
  // The receipt needs the auth header: fetched as a blob, saved from memory.
  downloadReceipt: async (id: string) =>
    saveResponse(
      await request(`/facturas/${id}/recibo.pdf`),
      `recibo-${id}.pdf`,
    ),
};
