export type Role =
  | "ADMIN"
  | "EPS_COORDINATOR"
  | "IPS_CLINICAL_OPERATOR"
  | "SPECIALIST"
  | "ACCOUNTANT"
  | "PATIENT";
export type Priority = "EMERGENCY" | "URGENT" | "ROUTINE";
export type BedStatus =
  | "AVAILABLE"
  | "OCCUPIED"
  | "RESERVED"
  | "CLEANING"
  | "BLOCKED"
  | "MAINTENANCE";
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface User {
  id: string;
  username: string;
  full_name: string;
  role: Role;
  organization_id: string | null;
  patient_id: string | null;
}
export interface Token {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
  role: Role;
}
export interface Account {
  id: string;
  username: string;
  full_name: string;
  email: string | null;
  role: Role;
  is_active: boolean;
  failed_login_attempts: number;
  is_locked: boolean;
  locked_at: string | null;
  last_login_at: string | null;
}
export interface AccountCreate {
  username: string;
  full_name: string | null;
  email: string | null;
  role: Role;
  password: string;
  organization_id?: string | null;
  patient_document?: string | null;
}
export interface AuditLog {
  id: string;
  user_id: string | null;
  username: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
}
export interface Organization {
  id: string;
  code: string;
  name: string;
  organization_type: "EPS" | "IPS";
  parent_organization_id: string | null;
  created_at: string;
  updated_at: string;
}
export interface OverdueCleaningBed {
  location_id: string;
  code: string;
  name: string;
  service: string | null;
  organization_id: string;
  cleaning_since: string;
  minutes_in_cleaning: number;
  pending_review_records: number;
  predicted_delay_probability?: number | null;
  predicted_delay?: boolean | null;
}
export interface Bed {
  id: string;
  code: string;
  name: string;
  service: string | null;
  status: BedStatus;
  organization_id: string;
  parent_location_id: string | null;
}
export interface ServiceCapacity {
  service: string;
  total: number;
  available: number;
  occupied: number;
  reserved: number;
  cleaning: number;
  blocked: number;
  maintenance: number;
}
export interface OrganizationCapacity extends Omit<ServiceCapacity, "service"> {
  organization_id: string;
  organization_code: string;
  organization_name: string;
  occupancy_rate: number;
  pending_requests: number;
  services: ServiceCapacity[];
}
export interface NetworkCapacity {
  total: number;
  available: number;
  occupied: number;
  occupancy_rate: number;
  pending_requests: number;
  organizations: OrganizationCapacity[];
}
export interface BedRequest {
  id: string;
  encounter_id: string;
  requesting_organization_id: string;
  target_organization_id: string | null;
  required_service: string;
  priority: Priority;
  status: "PENDING" | "IN_REVIEW" | "ASSIGNED" | "REJECTED" | "CANCELLED";
  notes: string | null;
  requested_at: string;
  resolved_at: string | null;
}
export interface QueuedRequest extends BedRequest {
  // Patient criticality from the AI service, 0 to 1; null until a report is approved.
  criticality_score?: number | null;
  queue_position: number;
  patient_id: string;
  patient_name: string;
  patient_document: string;
  waiting_minutes: number;
}
export interface Candidate {
  location_id: string;
  code: string;
  name: string;
  service: string | null;
  organization_id: string;
  organization_name: string;
  same_service: boolean;
  same_organization: boolean;
}
export interface Assignment {
  id: string;
  bed_request_id: string;
  location_id: string;
  status: "ACTIVE" | "RELEASED" | "CANCELLED";
  assigned_at: string;
  released_at: string | null;
}
export interface BedEvent {
  id: string;
  location_id: string;
  encounter_id: string | null;
  previous_status: BedStatus | null;
  new_status: BedStatus;
  reason: string | null;
  event_at: string;
  changed_by: string | null;
  // AI guess made when the bed entered cleaning (null when the service was down).
  predicted_delay_probability?: number | null;
  predicted_delay?: boolean | null;
  delay_model_version?: string | null;
}
export interface PatientInput {
  document_type: "CC" | "TI" | "CE" | "PA" | "RC";
  document_number: string;
  first_name: string;
  last_name: string;
  birth_date: string;
  gender: "female" | "male" | "other" | "unknown";
  phone: string | null;
  email: string | null;
  address?: string | null;
  eps_organization_id: string;
}
export interface Patient extends PatientInput {
  id: string;
  clinical_profile: string | null;
  created_by: string | null;
  responsible_clinician_id?: string | null;
  responsible_clinician_name?: string | null;
  created_at: string;
  updated_at: string;
}
export interface EncounterInput {
  patient_id: string;
  organization_id: string;
  location_id?: string | null;
  encounter_class: "EMER" | "IMP" | "AMB";
  status: "planned" | "arrived" | "in-progress" | "finished" | "cancelled";
  priority: Priority;
  reason_text: string | null;
  started_at: string;
  ended_at?: string | null;
}
export interface Encounter extends EncounterInput {
  id: string;
  created_at: string;
  updated_at: string;
}
export interface ObservationInput {
  patient_id: string;
  encounter_id: string;
  status: "registered" | "preliminary" | "final" | "amended" | "cancelled";
  code_system?: string;
  code: string;
  display: string;
  value_numeric?: number | null;
  value_text?: string | null;
  unit?: string | null;
  unit_system?: string;
  observed_at: string;
}
export interface Observation extends ObservationInput {
  id: string;
  // "fhir" rows arrived from the FHIR server through the Subscription.
  origin?: "local" | "fhir";
  created_at: string;
  updated_at: string;
}
export interface Study {
  id: string;
  patient_id: string;
  encounter_id: string | null;
  organization_id: string;
  study_instance_uid: string;
  accession_number: string;
  modality: "CR" | "DX" | "CT" | "MR" | "US" | "XA" | "NM";
  status: "registered" | "available" | "cancelled";
  body_site: string;
  description: string | null;
  series_count: number;
  instance_count: number;
  started_at: string;
  pacs_study_id: string | null;
  created_at: string;
  updated_at: string;
}
export interface StudyImages {
  study_id: string;
  images: { instance_id: string; number: number | null }[];
}
export interface WaitTimes {
  by_priority: {
    priority: Priority;
    assignments: number;
    avg_minutes: number;
    median_minutes: number;
    p90_minutes: number;
    min_minutes: number;
    max_minutes: number;
  }[];
}
export interface DecisionSummary {
  organizations: {
    organization_id: string;
    organization_name: string;
    available_beds: number;
    waiting_requests: number;
    capacity_index: number;
    recommendation: string;
  }[];
}
export interface Cohorts {
  cohorts: {
    profile: string;
    patients: number;
    avg_age: number;
    vitals: Record<string, { avg: number; median: number; samples: number }>;
  }[];
}
export interface Turnaround {
  by_organization: {
    organization_id: string;
    organization_name: string;
    cycles: number;
    avg_minutes: number;
    median_minutes: number;
    max_minutes: number;
  }[];
}
export interface Clusters {
  patients: number;
  message?: string;
  k?: number;
  k_chosen_by?: "silhouette" | "requested";
  silhouette?: number;
  adjusted_rand_index?: number | null;
  purity?: number;
  silhouette_by_k?: { k: number; silhouette: number }[];
  features?: { name: string; loinc: string | null; unit: string }[];
  clusters?: {
    cluster: number;
    patients: number;
    centroid: Record<string, number>;
    dominant_profile: string | null;
    dominant_share: number;
    profiles: Record<string, number>;
  }[];
}

// One group of the triage cohort (GET /cohort/clusters), real and synthetic rows.
export interface CohortCluster {
  cluster: number;
  zona: string;
  descripcion: string;
  n_reales: number;
  n_sinteticos: number;
  // Share of real stays that ended in admission, 0 to 1.
  tasa_ingreso: number;
  // Mean silhouette of the group, -1 to 1.
  silueta: number | null;
}

// One inbox entry. The live channel sends the same shape.
export interface AppNotification {
  id: string;
  tipo: string;
  mensaje: string;
  leida: boolean;
  fecha: string;
  paciente_id?: string | null;
  datos?: Record<string, unknown> | null;
}

export interface EligibleClinician {
  id: string;
  nombre: string;
  rol: string;
  organizacion: string;
}
