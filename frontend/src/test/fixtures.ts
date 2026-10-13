import type {
  Account,
  Bed,
  Candidate,
  Encounter,
  Patient,
  QueuedRequest,
  User,
} from "../api/types";
export const user: User = {
  id: "user-1",
  username: "registro.local",
  full_name: "Registro de prueba",
  role: "ADMIN",
  organization_id: null,
  patient_id: null,
};
export const patient: Patient = {
  id: "patient-1",
  document_type: "CC",
  document_number: "12345678",
  first_name: "María",
  last_name: "Pérez",
  birth_date: "1985-01-01",
  gender: "female",
  phone: null,
  email: null,
  address: null,
  eps_organization_id: "eps-1",
  clinical_profile: "HEALTHY",
  created_by: "user-1",
  created_at: "2026-01-01",
  updated_at: "2026-01-01",
};
export const bed: Bed = {
  id: "bed-1",
  code: "N-01",
  name: "Cama Norte 01",
  service: "UCI",
  status: "AVAILABLE",
  organization_id: "ips-1",
  parent_location_id: null,
};
export const candidate: Candidate = {
  location_id: "bed-1",
  code: bed.code,
  name: bed.name,
  service: bed.service,
  organization_id: "ips-1",
  organization_name: "Clínica Norte",
  same_organization: true,
  same_service: true,
};
export const request: QueuedRequest = {
  id: "request-1",
  encounter_id: "encounter-1",
  requesting_organization_id: "ips-1",
  target_organization_id: null,
  required_service: "UCI",
  priority: "ROUTINE",
  status: "PENDING",
  notes: null,
  requested_at: "2026-09-29T15:00:00Z",
  resolved_at: null,
  queue_position: 1,
  patient_id: patient.id,
  patient_name: "María Pérez",
  patient_document: "CC-12345678",
  waiting_minutes: 25,
};
export const encounter: Encounter = {
  id: "encounter-1",
  patient_id: patient.id,
  organization_id: "ips-1",
  encounter_class: "IMP",
  status: "in-progress",
  priority: "ROUTINE",
  reason_text: "Atención de prueba",
  started_at: "2026-09-29T14:00:00Z",
  created_at: "2026-09-29T14:00:00Z",
  updated_at: "2026-09-29T14:00:00Z",
};
export const account: Account = {
  id: "user-2",
  username: "operacion.local",
  full_name: "Operación de prueba",
  role: "IPS_CLINICAL_OPERATOR",
  email: null,
  is_active: true,
  is_locked: false,
  failed_login_attempts: 0,
  locked_at: null,
  last_login_at: null,
};
export const page = <T>(items: T[], total = items.length) => ({
  items,
  total,
  page: 1,
  page_size: 10,
});
export const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
