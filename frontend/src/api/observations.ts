import { api } from "./client";
import type { Observation, ObservationInput } from "./types";
export const createObservation = (body: ObservationInput) =>
  api<Observation>("/observations", { method: "POST", body });
