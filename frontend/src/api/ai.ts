import { api, queryString, request } from "./client";
import { saveResponse } from "./download";
export type AiReportStatus = "pendiente" | "aprobado" | "rechazado";
export interface AiFeature {
  feature: string;
  value: number | string;
  contribution: number;
  imputed: boolean;
}
export interface AiReport {
  id: string;
  patient_id: string;
  kind: string;
  estado: AiReportStatus;
  pseudonym: string;
  probability: number;
  nivel: string;
  critico: boolean;
  threshold: number | null;
  model_version: string;
  imputed: string[];
  top_features: AiFeature[];
  requested_by: string;
  reviewed_by: string | null;
  firmado_por: string | null;
  reviewed_at: string | null;
  observaciones: string | null;
  motivo_rechazo: string | null;
  solicitud_cama_id: string | null;
  created_at: string;
}
export interface ClinicalRecordReport {
  id: string;
  kind: string;
  estado: AiReportStatus;
  probability: number;
  nivel: string;
  model_version: string;
  top_features: AiFeature[];
  observaciones: string | null;
  firmado_por: string | null;
  reviewed_at: string | null;
  created_at: string;
}
export interface ClinicalRecord {
  reportes_ia: ClinicalRecordReport[];
  generado_en: string;
}
export interface Rates {
  recall: number | null;
  precision: number | null;
  specificity: number | null;
  f1: number | null;
}
export interface Subgroup extends Rates {
  n: number;
  n_positive: number;
  positive_rate: number;
  auc: number | null;
  flagged_rate: number;
}
export interface ComparedModel {
  id: string;
  nombre: string;
  familia: string;
  auc: { mean: number; std: number } | null;
  threshold: number;
  f1: number | null;
  f1_se: number;
  precision: number | null;
  recall: number | null;
  specificity: number | null;
  confusion: { tn: number; fp: number; fn: number; tp: number };
  meets_recall_target: boolean;
  fit_seconds: number;
  winner: boolean;
}
export interface ImportanceItem {
  feature: string;
  label: string;
  importance: number;
}
export interface OddsRatioItem {
  feature: string;
  label: string;
  coefficient: number;
  odds_ratio: number;
  reading: string;
}
export interface Comparison {
  protocol: { repeats: number; folds: number; target_recall: number };
  baseline_flag_everyone: { recall: number; precision: number; f1: number };
  models: ComparedModel[];
  winner: string;
  rule: string;
  decision: string;
  importance: {
    method: string;
    reading: string;
    shown_model: string;
    shown_model_name: string;
    by_model: Record<string, ImportanceItem[]>;
  };
  odds_ratios: { model_name: string; note: string; items: OddsRatioItem[] };
}
export interface ModelMetrics {
  model_version: string;
  winner?: string;
  comparacion?: Comparison;
  data: { real_rows?: number; rows?: number; positive_rate: number };
  validation: {
    scheme: string;
    evaluated_on_synthetic_rows: boolean;
    note?: string;
  };
  evaluation: {
    auc: { mean: number; std: number } | null;
    threshold: number;
    target_recall: number;
    at_threshold: Rates;
    flagged_rate: number;
    baseline_flag_everyone: { recall: number; precision: number };
  };
  ablation?: {
    model?: string;
    auc_with_augmentation: number;
    auc_without_augmentation: number;
  };
  bias_by_subgroup: Record<string, Subgroup>;
}
export type AiModels = Record<string, ModelMetrics | number>;
export const aiApi = {
  reports: (
    filter: { patient_id?: string; estado?: AiReportStatus },
    signal?: AbortSignal,
  ) => api<AiReport[]>(`/ai-reports?${queryString(filter)}`, { signal }),
  analyze: (patientId: string, triageLevel?: number) =>
    api<AiReport>(`/patients/${patientId}/ai-analyses`, {
      method: "POST",
      body: triageLevel ? { triage_level: triageLevel } : {},
    }),
  payload: (reportId: string, signal?: AbortSignal) =>
    api<{ payload: Record<string, unknown> }>(
      `/ai-reports/${reportId}/payload`,
      { signal },
    ),
  approve: (reportId: string, observaciones: string) =>
    api<AiReport>(`/ai-reports/${reportId}/approve`, {
      method: "POST",
      body: observaciones ? { observaciones } : {},
    }),
  reject: (reportId: string, motivo: string, observaciones: string) =>
    api<AiReport>(`/ai-reports/${reportId}/reject`, {
      method: "POST",
      body: { motivo, ...(observaciones ? { observaciones } : {}) },
    }),
  models: (signal?: AbortSignal) => api<AiModels>("/ai/models", { signal }),
  record: (patientId: string, signal?: AbortSignal) =>
    api<ClinicalRecord>(`/patients/${patientId}/clinical-record`, { signal }),
  // The PDF needs the auth header, so it is fetched as a blob and saved from memory.
  downloadRecord: async (patientId: string, fallbackName: string) =>
    saveResponse(
      await request(`/patients/${patientId}/clinical-record.pdf`),
      fallbackName,
    ),
};
