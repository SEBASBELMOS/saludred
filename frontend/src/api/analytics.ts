import { api } from "./client";
import type {
  Clusters,
  Cohorts,
  DecisionSummary,
  Turnaround,
  WaitTimes,
} from "./types";
export const analyticsApi = {
  decision: (signal?: AbortSignal) =>
    api<DecisionSummary>("/analytics/decision-summary", { signal }),
  waits: (signal?: AbortSignal) =>
    api<WaitTimes>("/analytics/wait-times", { signal }),
  cohorts: (signal?: AbortSignal) =>
    api<Cohorts>("/analytics/patient-cohorts", { signal }),
  turnaround: (signal?: AbortSignal) =>
    api<Turnaround>("/analytics/bed-turnaround", { signal }),
  clusters: (k: string, signal?: AbortSignal) =>
    api<Clusters>(`/analytics/patient-clusters${k ? `?k=${k}` : ""}`, {
      signal,
    }),
};
export const occupancyTimeline = (signal?: AbortSignal) =>
  api<{
    days: number;
    timeline: { date: string; admissions: number; discharges: number }[];
  }>("/analytics/occupancy-timeline", { signal });
