import type { Role, User, Patient } from "../../api/types";
export const roleNames: Record<Role, string> = {
  ADMIN: "Administración",
  EPS_COORDINATOR: "Coordinación EPS",
  IPS_CLINICAL_OPERATOR: "Operación IPS",
  SPECIALIST: "Especialista",
  ACCOUNTANT: "Contable",
  PATIENT: "Paciente",
};
const operation: Role[] = ["ADMIN", "EPS_COORDINATOR", "IPS_CLINICAL_OPERATOR"];
const network: Role[] = ["ADMIN", "EPS_COORDINATOR"];
// Invoices live in MongoDB; only these roles read or operate them.
const billing: Role[] = ["ADMIN", "ACCOUNTANT"];
// Clinical staff of one IPS who read the queue, the beds and the patients.
const ipsStaff: Role[] = [...operation, "SPECIALIST"];
// A medico sends referrals, an especialista answers them, the admin reads all.
const referrals: Role[] = ["ADMIN", "IPS_CLINICAL_OPERATOR", "SPECIALIST"];
// Labels name the task, not the object.
export const navigation = [
  {
    path: "/capacity",
    label: "Centro de mando",
    icon: "pi-th-large",
    roles: operation,
  },
  {
    path: "/queue",
    label: "Cola de asignación",
    icon: "pi-list",
    roles: ipsStaff,
  },
  {
    path: "/beds",
    label: "Pizarra de camas",
    icon: "pi-table",
    roles: ipsStaff,
  },
  {
    path: "/patients",
    label: "Pacientes",
    icon: "pi-users",
    roles: ipsStaff,
    // Reviewers see how many AI reports wait for their signature.
    reviewBadge: true,
  },
  {
    path: "/referrals",
    label: "Remisiones",
    icon: "pi-send",
    roles: referrals,
    // The especialista sees how many referrals wait for an answer.
    referralBadge: true,
  },
  {
    path: "/analysis",
    label: "Indicadores",
    icon: "pi-chart-bar",
    roles: network,
  },
  {
    path: "/accounts",
    label: "Cuentas y auditoría",
    icon: "pi-lock",
    roles: ["ADMIN"] as Role[],
  },
  {
    path: "/organizations",
    label: "Red de atención",
    icon: "pi-sitemap",
    roles: ["ADMIN"] as Role[],
  },
  {
    path: "/billing",
    label: "Contabilidad",
    icon: "pi-wallet",
    roles: billing,
  },
  {
    path: "/portal",
    label: "Mi información",
    icon: "pi-user",
    roles: ["PATIENT"] as Role[],
  },
];
export const menuFor = (role: Role) =>
  navigation.filter((item) => item.roles.includes(role));
// Where each role lands after login and when a route is not theirs.
const homes: Record<Role, string> = {
  ADMIN: "/capacity",
  EPS_COORDINATOR: "/capacity",
  IPS_CLINICAL_OPERATOR: "/beds",
  // What an especialista starts the day with: the referrals waiting for an answer.
  SPECIALIST: "/referrals",
  ACCOUNTANT: "/billing",
  PATIENT: "/portal",
};
export const homeFor = (role: Role) => homes[role];
export const canAssign = (role: Role) => network.includes(role);
export const canCreate = (role: Role) =>
  role === "ADMIN" || role === "IPS_CLINICAL_OPERATOR";
export const canChangeBed = (role: Role) => operation.includes(role);
// Cleaning evidence: the IPS operator (and the admin) register it, the network
// coordinator (and the admin) approve it. The API refuses a self-approval.
export const canRegisterCleaning = (role: Role) =>
  role === "ADMIN" || role === "IPS_CLINICAL_OPERATOR";
export const canReviewCleaning = (role: Role) =>
  role === "ADMIN" || role === "EPS_COORDINATOR";
// Encounters and measurements: any clinician; the API scopes the specialist to their IPS.
export const canRegisterEncounter = (role: Role) =>
  role === "ADMIN" ||
  role === "IPS_CLINICAL_OPERATOR" ||
  role === "SPECIALIST";
// Roles bound to a single IPS: they never choose another institution.
export const isIpsScoped = (role: Role) =>
  role === "IPS_CLINICAL_OPERATOR" || role === "SPECIALIST";
export const canEditPatient = (user: User, patient: Patient) =>
  user.role === "ADMIN" ||
  (user.role === "IPS_CLINICAL_OPERATOR" && patient.created_by === user.id);
// The AI suggests and a clinician decides: only these roles request and sign reports.
export const canReviewAi = (role: Role) =>
  role === "IPS_CLINICAL_OPERATOR" || role === "SPECIALIST";
// Only a medico refers a patient (R08); the API answers 403 to anybody else.
export const canRefer = (role: Role) => role === "IPS_CLINICAL_OPERATOR";
// The especialista answers the referrals addressed to them.
export const canAnswerReferral = (role: Role) => role === "SPECIALIST";
export const canSeeReferrals = (role: Role) => referrals.includes(role);
// The persistent critical-alert banner (R20.2) is for the clinicians who answer for the patient.
export const canSeeCriticalAlerts = (role: Role) =>
  role === "IPS_CLINICAL_OPERATOR" || role === "SPECIALIST";
// Roles that read the alerts endpoint: the banner for clinicians, the bed alerts for the command center.
export const canReadAlerts = (role: Role) =>
  role === "ADMIN" ||
  role === "EPS_COORDINATOR" ||
  role === "IPS_CLINICAL_OPERATOR" ||
  role === "SPECIALIST";
// Staff within their scope and the patient on their own page. The accountant gets no clinical data.
export const canDownloadRecord = (role: Role) => role !== "ACCOUNTANT";
// The inbox links to the patient record through the bed board, which only these roles have.
export const canOpenPatientFromInbox = (role: Role) =>
  menuFor(role).some((item) => item.path === "/beds");
// The API lists eligible colleagues (GET /patients/{id}/eligible-clinicians) for the
// admin and for a medico or especialista of the patient's IPS.
export const canChangeResponsible = (role: Role) =>
  role === "ADMIN" || role === "IPS_CLINICAL_OPERATOR" || role === "SPECIALIST";
// Only the admin can search patients among the billing roles; the accountant works with ids.
export const canResolvePatients = (role: Role) => role === "ADMIN";
