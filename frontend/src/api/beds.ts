import { api } from "./client";
import type {
  Assignment,
  Bed,
  BedEvent,
  BedStatus,
  OverdueCleaningBed,
} from "./types";
export const bedsApi = {
  overdueCleaning: (minutes: number, signal?: AbortSignal) =>
    api<OverdueCleaningBed[]>(`/beds/cleaning/overdue?minutes=${minutes}`, {
      signal,
    }),
  history: (id: string, signal?: AbortSignal) =>
    api<BedEvent[]>(`/beds/${id}/history`, { signal }),
  change: (id: string, new_status: BedStatus, reason: string) =>
    api<Bed>(`/beds/${id}/status`, {
      method: "POST",
      body: { new_status, reason },
    }),
  release: (id: string) =>
    api<Assignment>(`/beds/${id}/release`, { method: "POST" }),
};
