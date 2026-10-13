import { api } from "./client";
import type {
  Assignment,
  BedRequest,
  Candidate,
  Priority,
  QueuedRequest,
} from "./types";
export const queueApi = {
  list: (signal?: AbortSignal) =>
    api<QueuedRequest[]>("/bed-requests/queue", { signal }),
  candidates: (id: string, signal?: AbortSignal) =>
    api<Candidate[]>(`/bed-requests/${id}/candidates`, { signal }),
  assign: (
    id: string,
    body: {
      location_id: string;
      override_priority: boolean;
      reason: string | null;
    },
  ) => api<Assignment>(`/bed-requests/${id}/assign`, { method: "POST", body }),
  create: (body: {
    encounter_id: string;
    required_service: string;
    priority: Priority;
  }) => api<BedRequest>("/bed-requests", { method: "POST", body }),
};
