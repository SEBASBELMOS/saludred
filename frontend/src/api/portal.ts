import { api } from "./client";
import type { Patient, Encounter, Observation, Study } from "./types";
export const portalApi = {
  patient: (signal?: AbortSignal) => api<Patient>("/me/patient", { signal }),
  encounters: (signal?: AbortSignal) =>
    api<Encounter[]>("/me/encounters", { signal }),
  observations: (signal?: AbortSignal) =>
    api<Observation[]>("/me/observations", { signal }),
  studies: (signal?: AbortSignal) =>
    api<Study[]>("/me/imaging-studies", { signal }),
};
