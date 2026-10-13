import { api, queryString, request } from "./client";
import { saveResponse } from "./download";
import type {
  EligibleClinician,
  Encounter,
  Page,
  Patient,
  PatientInput,
  Observation,
} from "./types";
export const patientsApi = {
  search: (
    key: "name" | "document_number",
    value: string,
    signal?: AbortSignal,
  ) =>
    api<Page<Patient>>(
      `/patients?${queryString({ [key]: value, page_size: 10 })}`,
      { signal },
    ),
  get: (id: string, signal?: AbortSignal) =>
    api<Patient>(`/patients/${id}`, { signal }),
  create: (body: PatientInput) =>
    api<Patient>("/patients", { method: "POST", body }),
  update: (id: string, body: Partial<PatientInput>) =>
    api<Patient>(`/patients/${id}`, { method: "PUT", body }),
  setResponsible: (id: string, clinicianId: string) =>
    api<Patient>(`/patients/${id}/responsible-clinician`, {
      method: "PUT",
      body: { clinician_id: clinicianId },
    }),
  eligibleClinicians: (id: string, signal?: AbortSignal) =>
    api<EligibleClinician[]>(`/patients/${id}/eligible-clinicians`, { signal }),
  // Same auth-header constraint as the PDF: fetched as a blob, saved from memory.
  downloadFhirBundle: async (id: string, fallbackName: string) =>
    saveResponse(await request(`/patients/${id}/fhir-bundle`), fallbackName),
  encounters: (id: string, signal?: AbortSignal) =>
    api<Encounter[]>(`/patients/${id}/encounters`, { signal }),
  observations: (id: string, signal?: AbortSignal) =>
    api<Page<Observation>>(
      `/observations?${queryString({ patient_id: id, page_size: 100 })}`,
      { signal },
    ),
};
