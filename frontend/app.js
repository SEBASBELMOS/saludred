/* SaludRed · interfaz de coordinacion de camas conectada a la API.
 *
 * Las rutas son relativas: nginx sirve este front y hace de proxy hacia la
 * API, asi que ambos comparten origen y no hace falta CORS.
 *
 * La interfaz nunca decide permisos. Muestra u oculta acciones segun el rol
 * para no ofrecer lo que no se puede hacer, pero quien autoriza es la API: si
 * algo se cuela, la respuesta es un 403 y se muestra tal cual.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  queue: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3" cy="6" r=".5"/><circle cx="3" cy="12" r=".5"/><circle cx="3" cy="18" r=".5"/>',
  bed: '<path d="M3 18V6m18 12v-8a2 2 0 0 0-2-2h-8v8M3 16h18M3 12h8M3 19v2m18-2v2"/><path d="M5 8h3v4H5z"/>',
  people: '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-17a3 3 0 0 1 0 6m3 4a5 5 0 0 1 2 4v3"/>',
  hospital: '<path d="M5 21V4h14v17M2 21h20M9 21v-5h6v5M9 7h6m-3-3v6M8 12h1m6 0h1"/>',
  arrow: '<path d="M4 12h16m-5-5 5 5-5 5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  pulse: '<path d="M2 12h5l3-7 4 14 3-7h5"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  chart: '<path d="M4 20V10m6 10V4m6 16v-7m4 7H2"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.grid}</svg>`;

const statusNames = { AVAILABLE: "Disponible", OCCUPIED: "Ocupada", RESERVED: "Reservada", CLEANING: "En limpieza", BLOCKED: "Bloqueada", MAINTENANCE: "Mantenimiento" };
const priorities = { EMERGENCY: "Emergencia", URGENT: "Urgente", ROUTINE: "Rutina" };
const rank = { EMERGENCY: 0, URGENT: 1, ROUTINE: 2 };
const roleNames = { ADMIN: "Administración", EPS_COORDINATOR: "Coordinación EPS", IPS_CLINICAL_OPERATOR: "Operación IPS", PATIENT: "Paciente" };
const profileNames = { HEALTHY: "Sin comorbilidad", HYPERTENSIVE: "Hipertensión", DIABETIC: "Diabetes", CARDIAC: "Cardiopatía", RESPIRATORY: "Enfermedad respiratoria", ELDERLY_FRAIL: "Adulto mayor frágil", SIN_PERFIL: "Sin perfil" };
const encounterClass = { EMER: "Urgencias", IMP: "Hospitalización", AMB: "Ambulatorio" };
const encounterStatus = { planned: "Planeada", arrived: "En admisión", "in-progress": "En curso", finished: "Finalizada", cancelled: "Cancelada" };
const documentTypes = { CC: "Cédula de ciudadanía", TI: "Tarjeta de identidad", CE: "Cédula de extranjería", PA: "Pasaporte", RC: "Registro civil" };
const genders = { female: "Femenino", male: "Masculino", other: "Otro", unknown: "Sin dato" };
const modalities = { CR: "Radiografía", DX: "Radiografía digital", CT: "Tomografía", MR: "Resonancia", US: "Ecografía", XA: "Angiografía", NM: "Medicina nuclear" };
// LOINC dice que se midio; la unidad se muestra legible.
const vitals = [
  ["8480-6", "Presión sistólica", "mmHg"], ["8462-4", "Presión diastólica", "mmHg"],
  ["8867-4", "Frecuencia cardíaca", "lat/min"], ["9279-1", "Frecuencia respiratoria", "resp/min"],
  ["59408-5", "Saturación de oxígeno", "%"], ["8310-5", "Temperatura", "°C"],
  ["2339-0", "Glucosa", "mg/dL"], ["29463-7", "Peso", "kg"], ["8302-2", "Talla", "cm"],
];

// Unidad UCUM y rango fisiologicamente plausible de cada signo vital: los mismos
// que aplica la API. Validar aqui evita dejar una atencion a medio guardar; quien
// decide sigue siendo el servidor.
const vitalCatalog = {
  "8480-6": { ucum: "mm[Hg]", min: 50, max: 260 }, "8462-4": { ucum: "mm[Hg]", min: 30, max: 150 },
  "8867-4": { ucum: "/min", min: 30, max: 220 }, "9279-1": { ucum: "/min", min: 6, max: 60 },
  "59408-5": { ucum: "%", min: 50, max: 100 }, "8310-5": { ucum: "Cel", min: 32, max: 42 },
  "2339-0": { ucum: "mg/dL", min: 20, max: 800 }, "29463-7": { ucum: "kg", min: 1, max: 400 },
  "8302-2": { ucum: "cm", min: 30, max: 250 },
};
const actionNames = {
  LOGIN: "Ingreso", LOGIN_FAILED: "Ingreso fallido", ACCOUNT_LOCKED: "Cuenta bloqueada", ACCOUNT_UNLOCKED: "Cuenta desbloqueada",
  CREATE: "Creación", SOFT_EDIT: "Edición", SOFT_DELETE: "Borrado lógico", RESTORE: "Restauración", FHIR_SYNC: "Envío a FHIR",
};
const entityNames = {
  patients: "Paciente", users: "Cuenta", encounters: "Atención", observations: "Medición", imaging_studies: "Estudio de imagen",
  bed_requests: "Solicitud de cama", bed_assignments: "Asignación de cama", organizations: "Organización", locations: "Ubicación",
};

const TOKEN_KEY = "saludred.token";
const store = {
  get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* almacenamiento bloqueado */ } },
  remove: (k) => { try { sessionStorage.removeItem(k); } catch { /* almacenamiento bloqueado */ } },
};

const emptyData = () => ({ network: null, orgs: [], beds: [], queue: [], patients: [], patientsTotal: 0, users: [], analytics: null, portal: null, eps: null });
const state = { me: null, view: "capacity", search: "", org: "", service: "", status: "", priority: "", onlyLocked: false, clusterK: "", updated: null, loading: false, error: "", loaded: {}, queueLoaded: false, data: emptyData() };

/* ------------------------------------------------------------------ API */

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function detailOf(data, status) {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length) return d.map((e) => `${(e.loc || []).slice(1).join(".") || "dato"}: ${e.msg}`).join(" · ");
  return `La API respondió con el código ${status}.`;
}

async function api(path, { method = "GET", body } = {}) {
  const token = store.get(TOKEN_KEY);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "No hay conexión con la API. Revisa que el servicio esté encendido.");
  }
  if (res.status === 401 && token && path !== "/api/v1/auth/login") {
    endSession("Tu sesión terminó. Vuelve a iniciar sesión.");
    throw new ApiError(401, "Sesión expirada");
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailOf(data, res.status));
  return data;
}
const items = (payload) => (Array.isArray(payload) ? payload : payload?.items ?? []);

// Para lo que no es JSON: subir un archivo tal cual y traer una imagen. Una
// etiqueta <img> no puede mandar el token, asi que la vista previa se pide con
// fetch y se muestra desde un blob local.
async function apiRaw(path, { method = "GET", body, contentType } = {}) {
  const token = store.get(TOKEN_KEY);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { ...(contentType ? { "Content-Type": contentType } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body,
    });
  } catch {
    throw new ApiError(0, "No hay conexión con la API. Revisa que el servicio esté encendido.");
  }
  if (res.status === 401 && token) {
    endSession("Tu sesión terminó. Vuelve a iniciar sesión.");
    throw new ApiError(401, "Sesión expirada");
  }
  if (!res.ok) throw new ApiError(res.status, detailOf(await res.json().catch(() => null), res.status));
  return res;
}

/* ------------------------------------------------------------------ roles */

const role = () => state.me?.role;
const canAssign = () => ["ADMIN", "EPS_COORDINATOR"].includes(role());
const canCreate = () => ["ADMIN", "IPS_CLINICAL_OPERATOR"].includes(role());
const canChangeBeds = () => ["ADMIN", "EPS_COORDINATOR", "IPS_CLINICAL_OPERATOR"].includes(role());
const allowedViews = () => ({
  ADMIN: ["capacity", "queue", "beds", "patients", "analysis", "accounts"],
  EPS_COORDINATOR: ["capacity", "queue", "beds", "patients", "analysis"],
  IPS_CLINICAL_OPERATOR: ["capacity", "queue", "beds", "patients"],
  PATIENT: ["portal"],
}[role()] || []);

/* ------------------------------------------------------------------ formato */

const badge = (key, label) => `<span class="badge ${escapeHTML(key)}">${escapeHTML(label || statusNames[key] || priorities[key] || key)}</span>`;
function minutes(n) {
  const m = Math.max(0, Math.round(Number(n) || 0));
  if (m >= 1440) return `${Math.floor(m / 1440)} d ${Math.floor((m % 1440) / 60)} h`;
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}
// Las fechas de calendario no se convierten a UTC: se muestran tal cual.
const dateOnly = (value) => (value ? String(value).slice(0, 10).split("-").reverse().join("/") : "—");
const dateTime = (value) => {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("es-CO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};
function age(birth) {
  if (!birth) return "";
  const [y, m, d] = String(birth).split("-").map(Number);
  const now = new Date();
  let years = now.getFullYear() - y;
  if (now.getMonth() + 1 < m || (now.getMonth() + 1 === m && now.getDate() < d)) years -= 1;
  return years;
}
const documentLabel = (value) => String(value ?? "").replace(/^([A-Z]{2})-/, "$1 ");
const fullName = (p) => `${p.first_name} ${p.last_name}`;
const initials = (name) => String(name || "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "··";
const normalize = (value) => String(value).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const matches = (...values) => normalize(values.join(" ")).includes(normalize(state.search));
const options = (list, current) => list.map(([value, label]) => `<option value="${escapeHTML(value)}" ${value === current ? "selected" : ""}>${escapeHTML(label)}</option>`).join("");

/* ------------------------------------------------------------------ datos */

const toOrg = (o) => ({ id: o.organization_id, name: o.organization_name, code: o.organization_code, pending: o.pending_requests });
const toBed = (b) => ({ id: b.id, code: b.code, name: b.name, org: b.organization_id, status: b.status, service: b.service || "Sin servicio" });
const toRequest = (r) => ({
  id: r.id, patientId: r.patient_id, name: r.patient_name, document: documentLabel(r.patient_document),
  org: r.target_organization_id || r.requesting_organization_id, target: r.target_organization_id,
  service: r.required_service, priority: r.priority, minutes: r.waiting_minutes, position: r.queue_position, requestedAt: r.requested_at,
});
const orgFor = (id) => state.data.orgs.find((o) => o.id === id) || { name: "Institución", code: "" };
const services = () => [...new Set(state.data.beds.map((b) => b.service))].sort((a, b) => a.localeCompare(b, "es"));
const queue = () => state.data.queue;
const orgOptions = () => state.data.orgs.map((o) => [o.id, o.name]);

async function loadNetwork() {
  const network = await api("/api/v1/network/capacity");
  const orgs = (network.organizations || []).map(toOrg);
  // A failed IPS request is not an empty institution. Keep the previous
  // snapshot intact and let the view offer a retry instead of showing zeros.
  const lists = await Promise.all(orgs.map((o) => api(`/api/v1/organizations/${o.id}/beds`)));
  state.data.network = network;
  state.data.orgs = orgs;
  state.data.beds = lists.flat().map(toBed);
}
async function loadQueue() {
  state.data.queue = (await api("/api/v1/bed-requests/queue")).map(toRequest);
  state.queueLoaded = true;
}
// Los pacientes se buscan, no se listan: la historia clinica no se hojea. La API
// exige el criterio y devuelve como mucho diez; aqui solo se evita pedirle algo
// que igual rechazaria.
const MIN_DOCUMENT = 6;
const MIN_NAME = 3;
function searchCriterion(term) {
  const value = String(term || "").trim();
  if (!value) return null;
  const isDocument = /^\d+$/.test(value);
  if (isDocument && value.length < MIN_DOCUMENT) return { error: `Escribe el documento completo: al menos ${MIN_DOCUMENT} dígitos.` };
  if (!isDocument && value.length < MIN_NAME) return { error: `Escribe al menos ${MIN_NAME} letras del nombre o apellido.` };
  return { key: isDocument ? "document_number" : "name", value };
}

async function loadPatients() {
  const criterion = searchCriterion(state.search);
  state.data.patientsCriterion = criterion;
  if (!criterion || criterion.error) {
    state.data.patients = [];
    state.data.patientsTotal = 0;
    return;
  }
  // El documento se busca exacto; el nombre, por coincidencia parcial.
  const params = new URLSearchParams({ page_size: "10", [criterion.key]: criterion.value });
  const page = await api(`/api/v1/patients?${params}`);
  state.data.patients = items(page);
  state.data.patientsTotal = page.total ?? state.data.patients.length;
}
async function loadUsers() {
  const params = new URLSearchParams({ page_size: "100" });
  if (state.onlyLocked) params.set("only_locked", "true");
  const [users, audit] = await Promise.all([
    api(`/api/v1/admin/users?${params}`),
    api("/api/v1/audit-logs?page_size=20").catch(() => null),
  ]);
  state.data.users = items(users);
  state.data.audit = audit ? items(audit) : null;
}
async function loadAnalytics() {
  const [waits, decision, cohorts, turnaround, clusters] = await Promise.allSettled([
    api("/api/v1/analytics/wait-times"),
    api("/api/v1/analytics/decision-summary"),
    api("/api/v1/analytics/patient-cohorts"),
    api("/api/v1/analytics/bed-turnaround"),
    api(`/api/v1/analytics/patient-clusters${state.clusterK ? `?k=${state.clusterK}` : ""}`),
  ]);
  state.data.analytics = { waits, decision, cohorts, turnaround, clusters };
}
async function loadPortal() {
  const [patient, encounters, observations] = await Promise.all([
    api("/api/v1/me/patient"), api("/api/v1/me/encounters"), api("/api/v1/me/observations"),
  ]);
  let studies = [];
  try { studies = items(await api("/api/v1/me/imaging-studies")); } catch { studies = null; }
  state.data.portal = { patient, encounters: items(encounters), observations: items(observations), studies };
}

const loaders = {
  capacity: () => Promise.all([loadNetwork(), loadQueue()]),
  queue: () => Promise.all([loadNetwork(), loadQueue()]),
  beds: () => Promise.all([loadNetwork(), loadQueue()]),
  patients: loadPatients,
  analysis: loadAnalytics,
  accounts: loadUsers,
  portal: loadPortal,
};

/* ------------------------------------------------------------------ vistas */

const titles = {
  capacity: ["Resumen de la red", "La red, de un vistazo.", "Disponibilidad, demanda y atención en un mismo lugar."],
  queue: ["Cola de atención", "Cada minuto cuenta.", "Solicitudes ordenadas por prioridad clínica y tiempo de espera."],
  beds: ["Gestión de camas", "Un lugar para cuidar.", "Consulta la disponibilidad y el estado de cada cama."],
  patients: ["Pacientes", "Personas, antes que registros.", "Encuentra una ficha y da continuidad a la atención."],
  analysis: ["Análisis de la red", "Decidir con datos.", "Tiempos de espera, alistamiento de camas y grupos de pacientes."],
  accounts: ["Cuentas", "Acceso seguro, sin atajos.", "Una cuenta se bloquea al tercer intento fallido y solo la administración puede liberarla."],
  portal: ["Mi información", "Tu atención, más cerca.", "Consulta tus datos, atenciones y mediciones en un solo lugar."],
};

function toast(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  $("toasts").replaceChildren(el);
  setTimeout(() => el.remove(), 5000);
}
function resetFilters() { state.search = ""; state.org = ""; state.service = ""; state.status = ""; state.priority = ""; }

async function navigate(view, filters = {}) {
  if (!allowedViews().includes(view)) view = allowedViews()[0];
  resetFilters();
  Object.assign(state, filters);
  state.view = view;
  history.replaceState(null, "", `#${view}`);
  await loadAndRender();
  $("main").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "instant" });
}

async function loadAndRender() {
  const view = state.view;
  state.loading = true;
  state.error = "";
  render();
  try {
    await loaders[view]();
    state.loaded[view] = true;
    state.updated = new Date();
  } catch (err) {
    if (err.status === 401) return;
    state.error = err.message;
  } finally {
    state.loading = false;
  }
  if (state.view === view && state.me) render();
}

function renderNav() {
  const defs = {
    capacity: ["Resumen de la red", "grid"], queue: ["Cola de atención", "queue"], beds: ["Gestión de camas", "bed"],
    patients: ["Pacientes", "people"], analysis: ["Análisis", "chart"], accounts: ["Cuentas", "lock"], portal: ["Mi información", "people"],
  };
  $("navigation").innerHTML = allowedViews().map((id) => {
    const [label, symbol] = defs[id];
    const count = id === "queue" && state.queueLoaded ? `<span class="nav-count">${queue().length}</span>` : "";
    return `<a class="nav-item" href="#${id}" data-view="${id}" ${id === state.view ? 'aria-current="page"' : ""}>${icon(symbol)}${label}${count}</a>`;
  }).join("");
}

function ownOrgName() {
  return state.data.orgs.find((o) => o.id === state.me?.organization_id)?.name || "Mi IPS";
}

function render() {
  if (!state.me) return;
  renderNav();
  const [crumb, title, description] = titles[state.view];
  const operator = role() === "IPS_CLINICAL_OPERATOR";
  $("breadcrumb").textContent = operator && state.view === "capacity" ? "Resumen de mi IPS" : crumb;
  $("page-title").textContent = operator && state.view === "capacity" ? "Tu institución, al día." : title;
  $("page-description").textContent = description;
  $("eyebrow").textContent = role() === "PATIENT" ? "TU ESPACIO DE CUIDADO" : operator ? `${ownOrgName().toUpperCase()} · OPERACIÓN IPS` : "VISIBILIDAD PARA COORDINAR MEJOR";
  $("workspace-scope").textContent = role() === "PATIENT" ? "Mi espacio" : operator ? ownOrgName() : "Toda la red";
  $("updated").textContent = state.loading ? "Actualizando…" : state.updated ? `Actualizado · ${state.updated.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" })}` : "";
  $("refresh").innerHTML = `${icon("refresh")} Actualizar vista`;
  $("refresh").disabled = state.loading;

  if (state.error) {
    $("page-content").innerHTML = `<section class="panel"><div class="empty"><strong>No se pudo cargar esta vista</strong><p>${escapeHTML(state.error)}</p><button class="button secondary" data-action="retry">Reintentar</button></div></section>`;
    return;
  }
  if (!state.loaded[state.view]) {
    $("page-content").innerHTML = `<section class="panel"><div class="loading-state">Cargando información…</div></section>`;
    return;
  }
  const renderers = { capacity: renderCapacity, queue: renderQueue, beds: renderBeds, patients: renderPatients, analysis: renderAnalysis, accounts: renderAccounts, portal: renderPortal };
  $("page-content").innerHTML = renderers[state.view]();
  bindFilters();
}

function summary(list) {
  return {
    total: list.length,
    available: list.filter((b) => b.status === "AVAILABLE").length,
    occupied: list.filter((b) => b.status === "OCCUPIED").length,
    cleaning: list.filter((b) => b.status === "CLEANING").length,
  };
}
const rate = (s) => (s.total ? Math.round((s.occupied / s.total) * 100) : 0);

function renderMetrics() {
  const stats = summary(state.data.beds);
  const pending = queue();
  const emergency = pending.filter((r) => r.priority === "EMERGENCY").length;
  return `<div class="metrics">
  <a class="metric emphasis" href="#beds" data-view="beds" data-status="AVAILABLE"><div class="metric-label">Camas disponibles ${icon("bed")}</div><div class="metric-value">${stats.available}<small> / ${stats.total}</small></div><div class="metric-note">Listas para una nueva atención</div></a>
  <a class="metric" href="#beds" data-view="beds" data-status="OCCUPIED"><div class="metric-label">Ocupación actual ${icon("pulse")}</div><div class="metric-value">${rate(stats)}<small> %</small></div><div class="metric-note">${stats.occupied} camas ocupadas</div></a>
  <a class="metric" href="#queue" data-view="queue"><div class="metric-label">En espera ${icon("people")}</div><div class="metric-value">${pending.length}<small> personas</small></div><div class="metric-note">${emergency} ${emergency === 1 ? "solicitud de emergencia" : "solicitudes de emergencia"}</div></a>
  <a class="metric" href="#beds" data-view="beds" data-status="CLEANING"><div class="metric-label">En preparación ${icon("refresh")}</div><div class="metric-value">${stats.cleaning}<small> camas</small></div><div class="metric-note">Limpieza en curso</div></a>
  </div>`;
}

function renderCapacity() {
  const orgs = state.data.orgs;
  const pending = queue();
  const order = Object.keys(statusNames);
  const orgRows = orgs.map((org) => {
    const list = state.data.beds.filter((b) => b.org === org.id);
    const stats = summary(list);
    const strip = list.slice().sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status))
      .map((b) => `<span class="${b.status}" title="${escapeHTML(b.name)}: ${statusNames[b.status]}"></span>`).join("");
    return `<article class="org-row"><div class="org-title"><div class="org-identity"><span class="hospital-icon">${icon("hospital")}</span><div><h3>${escapeHTML(org.name)}</h3><p>${escapeHTML(org.code)} · ${stats.total} camas</p></div></div><div class="availability ${stats.available ? "" : "zero"}"><strong>${String(stats.available).padStart(2, "0")}</strong>disponibles</div></div><div class="bed-strip" aria-hidden="true">${strip}</div><div class="org-footer"><span><strong>${rate(stats)} %</strong> de ocupación · ${stats.occupied} ocupadas · ${org.pending} en espera</span><a class="text-link" href="#beds" data-view="beds" data-org="${org.id}">Ver camas ${icon("arrow")}</a></div></article>`;
  }).join("") || '<div class="empty">No hay instituciones visibles para tu cuenta.</div>';

  const preview = pending.slice(0, 3).map((r) => `<article class="queue-preview-item"><div class="request-top">${badge(r.priority)}<span class="wait">${icon("clock")}${minutes(r.minutes)}</span></div><div class="patient-line"><div><strong>${escapeHTML(r.name)}</strong><p>${escapeHTML(r.service)} · ${escapeHTML(orgFor(r.org).code)}</p></div><button class="button secondary small" data-action="${canAssign() ? "assign" : "patient"}" data-id="${canAssign() ? r.id : r.patientId}">${canAssign() ? "Asignar" : "Ver ficha"}</button></div></article>`).join("") || '<div class="empty">No hay solicitudes pendientes.</div>';

  const saturated = orgs.find((o) => !state.data.beds.some((b) => b.org === o.id && b.status === "AVAILABLE"));
  return `${renderMetrics()}<div class="dashboard-grid"><section class="panel"><div class="panel-header"><div><h2>Disponibilidad por institución</h2><p>Una marca, una cama. Toda la capacidad visible.</p></div><span class="badge">${orgs.length} IPS</span></div><div class="legend"><span><i class="available"></i>Disponible</span><span><i></i>Ocupada</span><span><i class="other"></i>Otros estados</span></div>${orgRows}<div class="panel-footer"><span>Capacidad física registrada</span><span>${state.data.beds.length} camas en total</span></div></section><div><section class="panel"><div class="panel-header"><div><h2>Atención prioritaria</h2><p>Las próximas solicitudes de la cola</p></div><span class="urgent-count">${pending.filter((r) => r.priority === "EMERGENCY").length}</span></div><div class="queue-preview">${preview}</div><div class="panel-footer"><span>${pending.length} solicitudes pendientes</span><a class="text-link" href="#queue" data-view="queue">Ver cola completa ${icon("arrow")}</a></div></section><div class="insight">${icon("hospital")}<div><h3>${saturated ? `${escapeHTML(saturated.name)} requiere apoyo de la red` : "Capacidad disponible para coordinar"}</h3><p>${saturated ? "No tiene camas disponibles. Consulta las alternativas por servicio en las otras instituciones." : "Revisa el servicio solicitado antes de elegir una cama para cada paciente."}</p><a class="text-link" href="#beds" data-view="beds" data-status="AVAILABLE">Explorar disponibilidad ${icon("arrow")}</a></div></div></div></div>${renderServices()}`;
}

function renderServices() {
  const rows = services().map((service) => {
    const s = summary(state.data.beds.filter((b) => b.service === service));
    return `<tr><td><strong>${escapeHTML(service)}</strong></td><td class="numeric">${s.total}</td><td class="numeric available-number">${s.available}</td><td class="numeric">${s.occupied}</td><td class="numeric"><span class="occupancy-bar" aria-hidden="true"><span style="width:${rate(s)}%"></span></span>${rate(s)} %</td><td class="numeric"><a class="table-action" href="#beds" data-view="beds" data-service="${escapeHTML(service)}">Ver camas</a></td></tr>`;
  }).join("");
  return `<section class="panel service-panel"><div class="panel-header"><div><h2>Capacidad por servicio</h2><p>Encuentra disponibilidad según la atención requerida.</p></div></div><div class="table-scroll"><table><caption class="sr-only">Disponibilidad y ocupación por servicio</caption><thead><tr><th scope="col">Servicio</th><th scope="col" class="numeric">Total</th><th scope="col" class="numeric">Disponibles</th><th scope="col" class="numeric">Ocupadas</th><th scope="col" class="numeric">Ocupación</th><th scope="col"><span class="sr-only">Acciones</span></th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function search(placeholder) {
  return `<label class="search-field">${icon("search")}<span class="sr-only">${placeholder}</span><input id="search" type="search" placeholder="${placeholder}" value="${escapeHTML(state.search)}" autocomplete="off"></label>`;
}
function filter(key, label, list) {
  return `<label class="filter-field"><span>${label}</span><select id="filter-${key}" data-filter="${key}" aria-label="${label}">${options([["", label], ...list], state[key])}</select></label>`;
}
function empty(message) {
  return `<div class="empty"><strong>Sin resultados por aquí</strong><p>${message}</p><button class="button secondary" data-action="clear">Limpiar filtros</button></div>`;
}

function renderQueue() {
  const list = queue().filter((r) => (!state.org || r.org === state.org) && (!state.service || r.service === state.service) && (!state.priority || r.priority === state.priority) && matches(r.name, r.document));
  const toolbar = `<div class="toolbar">${search("Buscar paciente o documento")}${state.data.orgs.length > 1 ? filter("org", "Institución", orgOptions()) : ""}${filter("priority", "Prioridad", Object.entries(priorities))}${filter("service", "Servicio", services().map((s) => [s, s]))}${canCreate() ? `<button class="button" data-action="request">${icon("plus")} Nueva solicitud</button>` : ""}</div>`;
  const rows = list.map((r) => `<tr><td>${badge(r.priority)}<small>Puesto ${r.position}</small></td><td><strong>${escapeHTML(r.name)}</strong><small>${escapeHTML(r.document)}</small></td><td>${escapeHTML(r.service)}<small>${escapeHTML(orgFor(r.org).name)}</small></td><td>${minutes(r.minutes)}</td><td class="numeric"><button class="button ${canAssign() ? "" : "secondary"} small" data-action="${canAssign() ? "assign" : "patient"}" data-id="${canAssign() ? r.id : r.patientId}">${canAssign() ? "Asignar cama" : "Ver ficha"}</button></td></tr>`).join("");
  return `${toolbar}<div class="scope-note">${icon("shield")}Primero la prioridad clínica; a igual prioridad, quien lleva más tiempo esperando. Saltar el orden exige registrar un motivo.</div><p class="result-count">${list.length} solicitudes encontradas</p><section class="panel">${list.length ? `<div class="table-scroll"><table><thead><tr><th scope="col">Prioridad</th><th scope="col">Paciente</th><th scope="col">Servicio e institución</th><th scope="col">Espera</th><th scope="col" class="numeric">Acción</th></tr></thead><tbody>${rows}</tbody></table></div>` : queue().length ? empty("Prueba otro nombre, institución o prioridad.") : '<div class="empty"><strong>Nadie espera cama</strong><p>No hay solicitudes pendientes en este momento.</p></div>'}</section>`;
}

function renderBeds() {
  const list = state.data.beds.filter((b) => (!state.org || b.org === state.org) && (!state.service || b.service === state.service) && (!state.status || b.status === state.status) && matches(b.name, b.code, b.service, orgFor(b.org).name));
  const cards = list.map((b) => `<button class="bed-card ${b.status}" data-action="bed" data-id="${b.id}">${icon("bed")}<span class="bed-code">${escapeHTML(b.name)}</span><span class="bed-service">${escapeHTML(b.service)}</span>${badge(b.status)}<span class="bed-org">${escapeHTML(orgFor(b.org).name)} · <span class="mono">${escapeHTML(b.code)}</span></span></button>`).join("");
  return `<div class="toolbar">${search("Buscar cama o servicio")}${state.data.orgs.length > 1 ? filter("org", "Institución", orgOptions()) : ""}${filter("status", "Estado", Object.entries(statusNames))}${filter("service", "Servicio", services().map((s) => [s, s]))}</div><p class="result-count">${list.length} camas · selecciona una para consultar o cambiar su estado</p>${list.length ? `<div class="bed-grid">${cards}</div>` : `<div class="panel">${empty("No hay camas que coincidan con estos filtros.")}</div>`}`;
}

function renderPatients() {
  const list = state.data.patients;
  const criterion = state.data.patientsCriterion;
  const toolbar = `<div class="toolbar">${search("Documento completo o nombre")}${canCreate() ? `<button class="button" data-action="new-patient">${icon("plus")} Nuevo paciente</button>` : ""}</div>`;
  if (!criterion || criterion.error) {
    return `${toolbar}<section class="panel"><div class="search-guide">${icon("search")}<strong>${criterion?.error ? escapeHTML(criterion.error) : "Busca a un paciente"}</strong><p>Escribe su número de documento completo (${MIN_DOCUMENT} dígitos o más) o al menos ${MIN_NAME} letras de su nombre o apellido. Por privacidad, los pacientes no se listan: se buscan.</p></div></section>`;
  }
  const total = state.data.patientsTotal;
  const count = total > list.length
    ? `${total} coincidencias · se muestran las ${list.length} primeras. Afina la búsqueda para encontrar a la persona.`
    : `${total} ${total === 1 ? "coincidencia" : "coincidencias"}`;
  const rows = list.map((p) => `<tr><td><strong>${escapeHTML(fullName(p))}</strong></td><td>${escapeHTML(p.document_type)} ${escapeHTML(p.document_number)}</td><td>${dateOnly(p.birth_date)}<small>${age(p.birth_date)} años</small></td><td>${p.clinical_profile ? escapeHTML(profileNames[p.clinical_profile] || p.clinical_profile) : "—"}</td><td class="numeric"><button class="table-action" data-action="patient" data-id="${p.id}">Ver paciente</button></td></tr>`).join("");
  return `${toolbar}<p class="result-count">${count}</p><section class="panel">${list.length ? `<div class="table-scroll"><table><thead><tr><th scope="col">Paciente</th><th scope="col">Documento</th><th scope="col">Nacimiento</th><th scope="col">Perfil clínico</th><th scope="col" class="numeric">Ficha</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("No encontramos pacientes con esta búsqueda. El documento se busca completo.")}</section>`;
}

function patientDetails(p) {
  return `<dl class="detail-list"><div><dt>Documento</dt><dd>${escapeHTML(p.document_type)} ${escapeHTML(p.document_number)}</dd></div><div><dt>Fecha de nacimiento</dt><dd>${dateOnly(p.birth_date)} · ${age(p.birth_date)} años</dd></div><div><dt>Sexo</dt><dd>${genders[p.gender] || "—"}</dd></div><div><dt>Teléfono de contacto</dt><dd>${escapeHTML(p.phone || "Sin registrar")}</dd></div><div><dt>Correo</dt><dd>${escapeHTML(p.email || "Sin registrar")}</dd></div>${p.clinical_profile ? `<div><dt>Perfil clínico</dt><dd>${escapeHTML(profileNames[p.clinical_profile] || p.clinical_profile)}</dd></div>` : ""}</dl>`;
}

function latestVitals(observations) {
  const latest = {};
  for (const o of observations) {
    if (o.value_numeric === null || o.value_numeric === undefined) continue;
    if (!latest[o.code] || new Date(o.observed_at) > new Date(latest[o.code].observed_at)) latest[o.code] = o;
  }
  return vitals.filter(([code]) => latest[code]).map(([code, name, unit]) => `<tr><td>${name}<small class="mono">LOINC ${code}</small></td><td><strong>${Number(latest[code].value_numeric).toLocaleString("es-CO")} ${unit}</strong></td><td>${dateTime(latest[code].observed_at)}</td></tr>`).join("");
}

function encounterTimeline(encounters) {
  return [...encounters].sort((a, b) => new Date(b.started_at) - new Date(a.started_at)).map((e) => `<article class="timeline-item"><time datetime="${escapeHTML(e.started_at)}">${dateTime(e.started_at)}</time><h3>${encounterClass[e.encounter_class] || escapeHTML(e.encounter_class)} ${badge(e.priority)}</h3><p>${escapeHTML(e.reason_text || "Sin motivo registrado")} · ${encounterStatus[e.status] || escapeHTML(e.status)}</p></article>`).join("");
}

function renderPortal() {
  const { patient: p, encounters, observations, studies } = state.data.portal;
  const vitalRows = latestVitals(observations);
  const studyRows = (studies || []).map((s) => `<tr><td>${dateOnly(s.started_at)}</td><td><strong>${escapeHTML(s.description || modalities[s.modality] || s.modality)}</strong><small>${escapeHTML(modalities[s.modality] || s.modality)} · ${escapeHTML(s.body_site)}</small></td><td class="numeric">${s.pacs_study_id ? `<button class="table-action" type="button" data-action="images" data-id="${s.id}" data-title="${escapeHTML(s.description || "")}">Ver imágenes</button>` : '<span class="muted-inline">Sin imágenes</span>'}</td></tr>`).join("");
  return `<div class="portal-layout"><section class="panel patient-profile"><span class="avatar">${escapeHTML(initials(fullName(p)))}</span><h2>${escapeHTML(fullName(p))}</h2><p>Tu información personal</p>${patientDetails(p)}</section><div><section class="panel"><div class="panel-header"><div><h2>Mis atenciones</h2><p>Tu recorrido de atención, en orden.</p></div>${icon("pulse")}</div>${encounters.length ? `<div class="timeline">${encounterTimeline(encounters)}</div>` : '<div class="empty">Aún no tienes atenciones registradas.</div>'}</section><section class="panel service-panel"><div class="panel-header"><div><h2>Mis mediciones</h2><p>El último valor registrado de cada medición.</p></div></div>${vitalRows ? `<div class="table-scroll"><table><thead><tr><th scope="col">Medición</th><th scope="col">Resultado</th><th scope="col">Fecha</th></tr></thead><tbody>${vitalRows}</tbody></table></div>` : '<div class="empty">Sin mediciones registradas.</div>'}</section>${studies === null ? "" : `<section class="panel service-panel"><div class="panel-header"><div><h2>Mis estudios de imagen</h2><p>Estudios registrados en tus atenciones.</p></div></div>${studyRows ? `<div class="table-scroll"><table><thead><tr><th scope="col">Fecha</th><th scope="col">Estudio</th><th scope="col" class="numeric">Imágenes</th></tr></thead><tbody>${studyRows}</tbody></table></div>` : '<div class="empty">No tienes estudios de imagen.</div>'}</section>`}<div class="insight">${icon("shield")}<div><h3>Un espacio para tu información</h3><p>Solo tú y el personal autorizado de la red pueden verla. Las mediciones se presentan sin interpretación clínica.</p></div></div></div></div>`;
}

function renderAnalysis() {
  const { waits, decision, cohorts, turnaround, clusters } = state.data.analytics;
  const failed = (r) => `<div class="error-box">${escapeHTML(r.reason?.message || "No disponible")}</div>`;
  const verdicts = { recibir: ["ok", "Puede recibir"], "al limite": ["warn", "Al límite"], saturada: ["bad", "Saturada"] };

  const decisionRows = decision.status === "fulfilled" ? (decision.value.organizations || []).map((r) => {
    const [tone, label] = verdicts[r.recommendation] || ["", r.recommendation];
    return `<tr><td><strong>${escapeHTML(r.organization_name)}</strong></td><td class="numeric">${r.available_beds}</td><td class="numeric">${r.waiting_requests}</td><td class="numeric">${r.capacity_index > 0 ? "+" : ""}${r.capacity_index}</td><td>${badge(tone, label)}</td></tr>`;
  }).join("") : "";

  const waitList = waits.status === "fulfilled" ? waits.value.by_priority || [] : [];
  const scale = Math.max(1, ...waitList.map((r) => r.p90_minutes || 0));
  const waitRows = waitList.map((r) => `<tr><td>${badge(r.priority)}</td><td class="numeric">${r.assignments}</td><td><span class="stat-bar ${r.priority}" aria-hidden="true"><span style="width:${(r.median_minutes / scale) * 100}%"></span></span>${minutes(r.median_minutes)}</td><td class="numeric">${minutes(r.p90_minutes)}</td><td class="numeric">${minutes(r.max_minutes)}</td></tr>`).join("");

  const vital = (c, key) => (c.vitals?.[key]?.median ? Math.round(c.vitals[key].median) : "—");
  const cohortRows = cohorts.status === "fulfilled" ? (cohorts.value.cohorts || []).map((c) => `<tr><td><strong>${escapeHTML(profileNames[c.profile] || c.profile)}</strong></td><td class="numeric">${c.patients}</td><td class="numeric">${Math.round(c.avg_age)}</td><td class="numeric">${vital(c, "sistolica")}</td><td class="numeric">${vital(c, "glucosa")}</td><td class="numeric">${vital(c, "saturacion")}</td><td class="numeric">${vital(c, "frecuencia_cardiaca")}</td></tr>`).join("") : "";

  const turnRows = turnaround.status === "fulfilled" ? (turnaround.value.by_organization || []).map((r) => `<tr><td><strong>${escapeHTML(r.organization_name)}</strong></td><td class="numeric">${r.cycles}</td><td class="numeric">${minutes(r.median_minutes)}</td><td class="numeric">${minutes(r.max_minutes)}</td></tr>`).join("") : "";

  const table = (head, rows) => `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;

  const clusterPanel = (() => {
    if (clusters.status !== "fulfilled") return failed(clusters);
    const c = clusters.value;
    if (!c.clusters) return `<div class="empty">${escapeHTML(c.message || "Sin datos para agrupar.")}</div>`;
    const best = Math.max(...c.silhouette_by_k.map((x) => x.silhouette));
    const kRows = c.silhouette_by_k.map((x) => `<tr><td>${x.k} grupos${x.k === c.k ? " · <strong>elegido</strong>" : ""}</td><td><span class="stat-bar" aria-hidden="true"><span style="width:${Math.max(0, (x.silhouette / best) * 100)}%"></span></span>${x.silhouette.toLocaleString("es-CO")}</td></tr>`).join("");
    const v = (cl, key, digits = 0) => Number(cl.centroid[key]).toLocaleString("es-CO", { maximumFractionDigits: digits });
    const rows = c.clusters.map((cl) => `<tr><td><strong>Grupo ${cl.cluster}</strong></td><td class="numeric">${cl.patients}</td><td>${cl.dominant_profile ? `${escapeHTML(profileNames[cl.dominant_profile] || cl.dominant_profile)}<small>${Math.round(cl.dominant_share * 100)} % del grupo</small>` : "—"}</td><td class="numeric">${v(cl, "sistolica")}</td><td class="numeric">${v(cl, "glucosa")}</td><td class="numeric">${v(cl, "saturacion")}</td><td class="numeric">${v(cl, "frecuencia_cardiaca")}</td><td class="numeric">${v(cl, "imc", 1)}</td><td class="numeric">${v(cl, "edad")}</td></tr>`).join("");
    const kOptions = [["", "Automático (silueta)"], ...[2, 3, 4, 5, 6, 7, 8].map((k) => [String(k), `${k} grupos`])];
    return `<div class="toolbar" style="padding:0 24px"><label class="filter-field"><span>Número de grupos</span><select id="cluster-k" aria-label="Número de grupos">${options(kOptions, state.clusterK)}</select></label></div>
      <div class="scope-note" style="margin:0 24px 16px">${icon("chart")}<span>${c.k_chosen_by === "silhouette" ? `El algoritmo eligió <strong>${c.k} grupos</strong> porque es donde los pacientes quedan mejor separados (silueta ${c.silhouette.toLocaleString("es-CO")}).` : `Agrupamiento con <strong>${c.k} grupos</strong>, fijado a mano (silueta ${c.silhouette.toLocaleString("es-CO")}).`} Coincidencia con el perfil clínico real: índice de Rand ajustado <strong>${c.adjusted_rand_index === null ? "—" : c.adjusted_rand_index.toLocaleString("es-CO")}</strong> · pureza <strong>${Math.round(c.purity * 100)} %</strong>.</span></div>
      ${table('<th scope="col">Grupo</th><th scope="col" class="numeric">Pacientes</th><th scope="col">Perfil que predomina</th><th scope="col" class="numeric">PA sistólica</th><th scope="col" class="numeric">Glucosa</th><th scope="col" class="numeric">Saturación</th><th scope="col" class="numeric">Frec. cardíaca</th><th scope="col" class="numeric">IMC</th><th scope="col" class="numeric">Edad</th>', rows)}
      <details style="padding:16px 24px"><summary>Cómo se eligió el número de grupos</summary>${table('<th scope="col">Opción</th><th scope="col">Silueta (más alto, mejor separados)</th>', kRows)}</details>`;
  })();

  return `<div class="stack">
    <section class="panel"><div class="panel-header"><div><h2>Dónde enviar al próximo paciente</h2><p>Índice = camas libres − solicitudes en espera. Si es negativo, esa IPS no debería recibir traslados.</p></div>${icon("hospital")}</div>${decision.status === "fulfilled" ? table('<th scope="col">Institución</th><th scope="col" class="numeric">Libres</th><th scope="col" class="numeric">En espera</th><th scope="col" class="numeric">Índice</th><th scope="col">Recomendación</th>', decisionRows) : failed(decision)}</section>
    <section class="panel"><div class="panel-header"><div><h2>Espera por una cama</h2><p>De la solicitud a la asignación. La barra es la mediana; se usa en lugar del promedio porque pocos casos muy largos lo inflan.</p></div>${icon("clock")}</div>${waits.status === "fulfilled" ? (waitRows ? table('<th scope="col">Prioridad</th><th scope="col" class="numeric">Asignaciones</th><th scope="col">Mediana</th><th scope="col" class="numeric">Percentil 90</th><th scope="col" class="numeric">Máximo</th>', waitRows) : '<div class="empty">Aún no hay asignaciones para medir.</div>') : failed(waits)}</section>
    <section class="panel"><div class="panel-header"><div><h2>Grupos por perfil clínico</h2><p>Medianas por grupo (mmHg, mg/dL, %, lat/min). El perfil sirve de referencia para comprobar si un agrupamiento automático encuentra los mismos grupos.</p></div>${icon("people")}</div>${cohorts.status === "fulfilled" ? table('<th scope="col">Perfil</th><th scope="col" class="numeric">Pacientes</th><th scope="col" class="numeric">Edad media</th><th scope="col" class="numeric">PA sistólica</th><th scope="col" class="numeric">Glucosa</th><th scope="col" class="numeric">Saturación</th><th scope="col" class="numeric">Frec. cardíaca</th>', cohortRows) : failed(cohorts)}</section>
    <section class="panel"><div class="panel-header"><div><h2>Agrupamiento automático de pacientes</h2><p>k-means sobre presión sistólica, glucosa, saturación, frecuencia cardíaca, IMC y edad. El algoritmo no ve el perfil clínico: el perfil solo se usa después, para calificar qué tan bien lo recuperó.</p></div>${icon("people")}</div>${clusterPanel}</section>
    <section class="panel"><div class="panel-header"><div><h2>Alistamiento de camas</h2><p>Del egreso del paciente a la cama lista para el siguiente.</p></div>${icon("refresh")}</div>${turnaround.status === "fulfilled" ? (turnRows ? table('<th scope="col">Institución</th><th scope="col" class="numeric">Ciclos</th><th scope="col" class="numeric">Mediana</th><th scope="col" class="numeric">Máximo</th>', turnRows) : '<div class="empty">Aún no hay ciclos de limpieza completos.</div>') : failed(turnaround)}</section>
  </div>`;
}

function renderAccounts() {
  const list = state.data.users;
  const locked = list.filter((u) => u.is_locked).length;
  const attempts = (n) => `<span class="attempts" aria-label="${n} de 3 intentos fallidos">${[0, 1, 2].map((k) => `<i class="${k < n ? "used" : ""}"></i>`).join("")}</span>`;
  const rows = list.map((u) => {
    const self = u.id === state.me.id;
    const action = self ? '<span class="muted-inline">Tu cuenta</span>' : `<button class="button ${u.is_locked ? "" : "secondary"} small" data-action="account" data-id="${u.id}" data-op="${u.is_locked ? "unlock" : "lock"}">${u.is_locked ? "Desbloquear" : "Bloquear"}</button>`;
    const status = u.is_locked ? `${badge("bad", "Bloqueada")}<small>desde ${dateTime(u.locked_at)}</small>` : u.is_active ? badge("ok", "Activa") : badge("", "Inactiva");
    return `<tr><td><strong>${escapeHTML(u.full_name)}</strong><small class="mono">${escapeHTML(u.username)}</small></td><td>${roleNames[u.role] || escapeHTML(u.role)}</td><td>${attempts(Math.min(3, u.failed_login_attempts))}</td><td>${status}</td><td>${dateTime(u.last_login_at)}</td><td class="numeric">${action}</td></tr>`;
  }).join("");
  const auditRows = (state.data.audit || []).map((e) => {
    const meta = e.metadata_json ? Object.entries(e.metadata_json).filter(([k]) => ["username", "reason", "outcome", "role", "fhir_resource", "failed_attempts"].includes(k)).map(([k, v]) => `${k}: ${v}`).join(" · ") : "";
    return `<tr><td>${dateTime(e.created_at)}</td><td>${badge(e.action === "LOGIN_FAILED" || e.action === "ACCOUNT_LOCKED" ? "bad" : "", actionNames[e.action] || e.action)}</td><td>${escapeHTML(entityNames[e.entity_type] || e.entity_type)}</td><td class="mono">${escapeHTML(e.username || "anónimo")}</td><td class="audit-meta">${escapeHTML(meta.slice(0, 120))}</td></tr>`;
  }).join("");
  const auditPanel = state.data.audit === null ? "" : `<section class="panel service-panel"><div class="panel-header"><div><h2>Actividad reciente</h2><p>Los últimos 20 registros de la auditoría. Solo la administración los ve.</p></div>${icon("shield")}</div>${auditRows ? `<div class="table-scroll"><table><thead><tr><th scope="col">Fecha</th><th scope="col">Acción</th><th scope="col">Sobre</th><th scope="col">Quién</th><th scope="col">Detalle</th></tr></thead><tbody>${auditRows}</tbody></table></div>` : '<div class="empty">Sin actividad registrada.</div>'}</section>`;
  return `<div class="toolbar"><label class="check"><input type="checkbox" id="only-locked" ${state.onlyLocked ? "checked" : ""}> Solo cuentas bloqueadas</label><button class="button" type="button" data-action="new-user">${icon("plus")} Nueva cuenta</button></div><div class="scope-note">${icon("lock")}Cada bloqueo y desbloqueo queda en la auditoría con quién lo hizo y por qué.</div><p class="result-count">${list.length} cuentas · ${locked} ${locked === 1 ? "bloqueada" : "bloqueadas"}</p><section class="panel">${list.length ? `<div class="table-scroll"><table><thead><tr><th scope="col">Cuenta</th><th scope="col">Rol</th><th scope="col">Intentos fallidos</th><th scope="col">Estado</th><th scope="col">Último ingreso</th><th scope="col" class="numeric">Acción</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty"><strong>Ninguna cuenta bloqueada</strong><p>No hay cuentas esperando desbloqueo.</p></div>'}</section>${auditPanel}`;
}

/* ------------------------------------------------------------------ filtros */

let searchTimer = null;
function bindFilters() {
  $("search")?.addEventListener("input", (event) => {
    const position = event.target.selectionStart;
    state.search = event.target.value;
    if (state.view === "patients") {
      // La busqueda de pacientes va al servidor: se espera a que termine de escribir.
      clearTimeout(searchTimer);
      searchTimer = setTimeout(async () => {
        try { await loadPatients(); } catch (err) { if (err.status !== 401) toast(err.message); return; }
        render();
        $("search")?.focus({ preventScroll: true });
      }, 350);
      return;
    }
    render();
    $("search").focus({ preventScroll: true });
    try { $("search").setSelectionRange(position, position); } catch { /* no todos los navegadores lo permiten en type=search */ }
  });
  document.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("change", () => {
    const key = el.dataset.filter;
    state[key] = el.value;
    render();
    $(`filter-${key}`).focus({ preventScroll: true });
  }));
  $("cluster-k")?.addEventListener("change", async (event) => {
    state.clusterK = event.target.value;
    await loadAndRender();
  });
  $("only-locked")?.addEventListener("change", async (event) => {
    state.onlyLocked = event.target.checked;
    await loadAndRender();
  });
}

/* ------------------------------------------------------------------ dialogos */

let dialogTrigger = null;
function openDialog(title, html, kicker = "SALUDRED") {
  dialogTrigger = document.activeElement;
  $("dialog-title").textContent = title;
  $("dialog-kicker").textContent = kicker;
  $("dialog-content").innerHTML = html;
  if (!$("dialog").open) $("dialog").showModal();
}
function closeDialog() { if ($("dialog").open) $("dialog").close(); }
function actions(label) { return `<div class="modal-actions"><button class="button secondary" type="button" data-action="close">Cancelar</button><button class="button" type="submit">${label}</button></div>`; }
const closeOnly = (label = "Cerrar") => `<div class="modal-actions"><button class="button secondary" type="button" data-action="close">${label}</button></div>`;

function showFormError(form, message) {
  let box = form.querySelector(".error-box");
  if (!box) {
    box = document.createElement("div");
    box.className = "error-box";
    box.setAttribute("role", "alert");
    form.prepend(box);
  }
  box.textContent = message;
}

// El manejador puede lanzar: el error se muestra dentro del formulario y el
// boton se vuelve a habilitar para corregir y reintentar.
function formSubmit(id, handler) {
  $(id).addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    if (!form.reportValidity()) return;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await handler(new FormData(form), form);
    } catch (err) {
      if (err?.status !== 401 && form.isConnected) showFormError(form, err.message);
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  });
}

// La API es la autoridad sobre el orden; esto solo anticipa su respuesta para
// avisar antes de que el coordinador elija una cama.
function priorRequest(request) {
  return queue().find((r) => r.id !== request.id && r.service === request.service && (!request.target || r.target === request.target)
    && (rank[r.priority] < rank[request.priority] || (r.priority === request.priority && new Date(r.requestedAt) < new Date(request.requestedAt))));
}

async function openAssign(id, { exception = false, message = "" } = {}) {
  const request = queue().find((r) => r.id === id);
  if (!request || !canAssign()) return;
  const prior = priorRequest(request);
  if (prior && !exception) {
    openDialog("Hay una solicitud anterior", `<div class="warning-box">${escapeHTML(prior.name)} (${priorities[prior.priority].toLowerCase()}) espera por ${escapeHTML(request.service)} con mayor prioridad o desde antes.</div><p class="dialog-intro">El orden de la cola se respeta por defecto. Si hay un motivo clínico o logístico para atender esta solicitud primero, puedes asignarla como excepción: el motivo queda en la auditoría.</p><div class="modal-actions"><button class="button secondary" type="button" data-action="close">Volver</button><button class="button secondary" type="button" data-action="assign-exception" data-id="${request.id}">Asignar como excepción</button><button class="button" type="button" data-action="next-request" data-id="${prior.id}">Ver solicitud anterior</button></div>`, "COORDINACIÓN DE LA ATENCIÓN");
    return;
  }

  openDialog("Encontrar una cama", '<div class="loading-state">Buscando camas disponibles…</div>', "COORDINACIÓN DE LA ATENCIÓN");
  let candidates;
  try {
    candidates = await api(`/api/v1/bed-requests/${id}/candidates`);
  } catch (err) {
    if (err.status !== 401) $("dialog-content").innerHTML = `<div class="error-box">${escapeHTML(err.message)}</div>${closeOnly()}`;
    return;
  }
  const shown = candidates.slice(0, 8);
  const identity = `<div class="identity-box"><div><strong>${escapeHTML(request.name)}</strong><small>${escapeHTML(request.document)} · ${escapeHTML(request.service)} · ${minutes(request.minutes)} en espera</small></div>${badge(request.priority)}</div>`;
  const list = shown.map((c) => `<label class="candidate"><input type="radio" name="bed" value="${c.location_id}" required><span><strong>${escapeHTML(c.name)} · ${escapeHTML(c.service || "")}</strong><small>${escapeHTML(c.organization_name)}</small></span>${c.same_organization ? badge("AVAILABLE", "Misma IPS") : ""}${c.same_service ? "" : badge("RESERVED", "Otro servicio")}</label>`).join("");
  $("dialog-content").innerHTML = `${identity}${message ? `<div class="warning-box">${escapeHTML(message)}</div>` : ""}${shown.length ? `<form id="assign-form"><fieldset class="candidate-list"><legend>Elige una cama. Primero las del servicio solicitado.</legend>${list}</fieldset>${exception ? '<label class="form-field" style="margin-top:16px">Motivo de la excepción<textarea name="reason" rows="2" maxlength="300" required placeholder="Por ejemplo: traslado ya coordinado con la UCI receptora"></textarea></label>' : ""}<p class="form-hint">Se muestran hasta 8 alternativas de ${candidates.length} disponibles.</p>${actions(exception ? "Asignar como excepción" : "Confirmar asignación")}</form>` : `<div class="empty"><strong>No hay camas disponibles</strong><p>La solicitud sigue en la cola y se atenderá cuando se libere una cama.</p></div>${closeOnly("Volver a la cola")}`}`;
  if (!shown.length) return;

  formSubmit("assign-form", async (form) => {
    const bed = shown.find((c) => c.location_id === form.get("bed"));
    try {
      await api(`/api/v1/bed-requests/${id}/assign`, {
        method: "POST",
        body: { location_id: form.get("bed"), override_priority: exception, reason: exception ? String(form.get("reason")).trim() : null },
      });
    } catch (err) {
      // La API tiene la ultima palabra sobre el orden. Si detecto una
      // solicitud anterior que la cola local no mostraba, se ofrece la
      // excepcion en lugar de un error sin salida.
      if (err.status === 409 && !exception && /atenderse primero/i.test(err.message)) {
        await openAssign(id, { exception: true, message: "Hay otra solicitud que debe atenderse primero. Para continuar con esta, registra el motivo de la excepción." });
        return;
      }
      throw err;
    }
    closeDialog();
    toast(`${bed ? bed.name : "Cama"} asignada a ${request.name}`);
    await loadAndRender();
  });
}

async function openBed(id) {
  const bed = state.data.beds.find((b) => b.id === id);
  if (!bed) return;
  const occupied = bed.status === "OCCUPIED";
  const identity = `<div class="identity-box"><div><strong>${escapeHTML(orgFor(bed.org).name)}</strong><small>${escapeHTML(bed.service)} · <span class="mono">${escapeHTML(bed.code)}</span></small></div>${badge(bed.status)}</div>`;
  let body;
  if (occupied) {
    body = `<p class="dialog-intro">Cama con un paciente asignado. Al liberarla pasa a limpieza; no queda disponible hasta que se registre el alistamiento.</p><div class="modal-actions"><button class="button secondary" type="button" data-action="close">Cerrar</button>${canAssign() ? `<button class="button" type="button" data-action="release" data-id="${bed.id}">Liberar y enviar a limpieza</button>` : '<p class="form-hint">La liberación corresponde a la coordinación EPS.</p>'}</div>`;
  } else if (canChangeBeds()) {
    body = `<form id="bed-form"><label class="form-field">Nuevo estado<select name="status" required><option value="">Selecciona un estado</option>${options(Object.entries(statusNames).filter(([key]) => key !== "OCCUPIED" && key !== bed.status), "")}</select></label><label class="form-field" style="margin-top:16px">Motivo del cambio<textarea name="reason" rows="2" maxlength="300" required placeholder="Por ejemplo: limpieza finalizada"></textarea></label><p class="form-hint">Una cama se ocupa asignando una solicitud desde la cola, no cambiando su estado.</p>${actions("Guardar cambio")}</form>`;
  } else {
    body = closeOnly();
  }
  openDialog(bed.name, `${identity}${body}<div id="bed-history"></div>`, "ESTADO OPERATIVO");

  if (!occupied && canChangeBeds()) {
    formSubmit("bed-form", async (form) => {
      await api(`/api/v1/beds/${id}/status`, { method: "POST", body: { new_status: form.get("status"), reason: String(form.get("reason")).trim() } });
      closeDialog();
      toast(`${bed.name}: ${statusNames[form.get("status")]}`);
      await loadAndRender();
    });
  }

  try {
    const history = await api(`/api/v1/beds/${id}/history`);
    const recent = [...history].sort((a, b) => new Date(b.event_at) - new Date(a.event_at)).slice(0, 4);
    if (recent.length && $("bed-history")) {
      $("bed-history").innerHTML = `<div class="insight"><div><h3>Últimos movimientos</h3><ul class="history-list">${recent.map((e) => `<li><time>${dateTime(e.event_at)}</time><span>${statusNames[e.previous_status] || "Registro inicial"} → <strong>${statusNames[e.new_status]}</strong>${e.reason ? ` · ${escapeHTML(e.reason)}` : ""}</span></li>`).join("")}</ul></div></div>`;
    }
  } catch { /* el historial es complementario: si falla, el dialogo sigue siendo util */ }
}

async function releaseBed(id) {
  const bed = state.data.beds.find((b) => b.id === id);
  try {
    await api(`/api/v1/beds/${id}/release`, { method: "POST" });
  } catch (err) {
    if (err.status !== 401) toast(err.message);
    return;
  }
  closeDialog();
  toast(`${bed ? bed.name : "Cama"} enviada a limpieza`);
  await loadAndRender();
}

async function openPatient(id) {
  if (!id) return;
  openDialog("Ficha del paciente", '<div class="loading-state">Cargando ficha…</div>', "FICHA DEL PACIENTE");
  let patient, encounters, observations, studies;
  try {
    [patient, encounters, observations, studies] = await Promise.all([
      api(`/api/v1/patients/${id}`),
      api(`/api/v1/patients/${id}/encounters`).then(items),
      api(`/api/v1/observations?patient_id=${id}&page_size=100`).then(items),
      api(`/api/v1/patients/${id}/imaging-studies?page_size=100`).then(items).catch(() => null),
    ]);
  } catch (err) {
    if (err.status !== 401) $("dialog-content").innerHTML = `<div class="error-box">${escapeHTML(err.message)}</div>${closeOnly()}`;
    return;
  }
  const vitalRows = latestVitals(observations);
  $("dialog-title").textContent = fullName(patient);
  $("dialog-content").innerHTML = `${patientDetails(patient)}${vitalRows ? `<h3 style="margin:24px 0 8px">Últimas mediciones</h3><div class="table-scroll"><table><tbody>${vitalRows}</tbody></table></div>` : ""}${encounters.length ? `<h3 style="margin:24px 0 8px">Atenciones</h3><div class="timeline" style="padding:5px 0 0">${encounterTimeline(encounters.slice(0, 4))}</div>` : ""}${studyTable(studies, patient.id)}<div class="modal-actions"><button class="button secondary" type="button" data-action="close">Cerrar</button>${canEditPatient(patient) ? `<button class="button secondary" type="button" data-action="edit-patient" data-id="${patient.id}">Editar datos</button>` : ""}${canCreate() ? `<button class="button secondary" type="button" data-action="new-encounter" data-id="${patient.id}">Registrar atención</button><button class="button" type="button" data-action="patient-request" data-id="${patient.id}">Solicitar cama</button>` : ""}</div>`;
}

function studyTable(studies, patientId) {
  if (!studies || !studies.length) return "";
  const rows = [...studies].sort((a, b) => new Date(b.started_at) - new Date(a.started_at)).map((s) => {
    const title = s.description || modalities[s.modality] || s.modality;
    const view = s.pacs_study_id ? `<button class="table-action" type="button" data-action="images" data-id="${s.id}" data-patient="${patientId || ""}" data-title="${escapeHTML(title)}">Ver imágenes</button>` : '<span class="muted-inline">Sin imágenes</span>';
    const upload = canCreate() && patientId ? ` <button class="table-action" type="button" data-action="upload-image" data-id="${s.id}" data-patient="${patientId}">Subir imagen</button>` : "";
    return `<tr><td>${dateOnly(s.started_at)}</td><td><strong>${escapeHTML(title)}</strong><small>${escapeHTML(modalities[s.modality] || s.modality)} · ${escapeHTML(s.body_site)}</small></td><td class="numeric">${view}${upload}</td></tr>`;
  }).join("");
  return `<h3 style="margin:24px 0 8px">Estudios de imagen</h3><div class="table-scroll"><table><tbody>${rows}</tbody></table></div>`;
}

// Las vistas previas se sirven como blobs locales; se liberan al salir del visor
// para no acumular memoria con cada imagen abierta.
let viewerUrls = [];
function releaseViewer() {
  viewerUrls.forEach((url) => URL.revokeObjectURL(url));
  viewerUrls = [];
}

async function showInstance(instanceId) {
  const img = $("viewer-img");
  if (!img) return;
  img.alt = "Cargando imagen…";
  try {
    const blob = await (await apiRaw(`/api/v1/imaging/instances/${instanceId}/preview`)).blob();
    releaseViewer();
    const url = URL.createObjectURL(blob);
    viewerUrls.push(url);
    img.src = url;
    img.alt = "Vista previa del estudio";
    document.querySelectorAll("[data-instance]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.instance === instanceId)));
  } catch (err) {
    if (err.status !== 401) $("viewer-status").textContent = err.message;
  }
}

async function openImages(studyId, patientId, title) {
  openDialog(title || "Imágenes del estudio", '<div class="loading-state">Buscando imágenes en el servidor de imágenes…</div>', "VISOR DE IMÁGENES · BORRADOR");
  let images;
  try {
    ({ images } = await api(`/api/v1/imaging-studies/${studyId}/images`));
  } catch (err) {
    if (err.status !== 401) $("dialog-content").innerHTML = `<div class="error-box">${escapeHTML(err.message)}</div>${closeOnly()}`;
    return;
  }
  const back = patientId ? `<button class="button secondary" type="button" data-action="patient" data-id="${patientId}">Volver a la ficha</button>` : "";
  if (!images.length) {
    $("dialog-content").innerHTML = `<div class="empty"><strong>Este estudio no tiene imágenes cargadas</strong><p>Los metadatos existen, pero todavía no se subieron los píxeles al servidor de imágenes.</p></div><div class="modal-actions">${back}<button class="button secondary" type="button" data-action="close">Cerrar</button></div>`;
    return;
  }
  const thumbs = images.length > 1 ? `<div class="viewer-thumbs">${images.map((im, k) => `<button class="button secondary small" type="button" data-instance="${im.instance_id}" aria-pressed="${k === 0}">Imagen ${im.number ?? k + 1}</button>`).join("")}</div>` : "";
  $("dialog-content").innerHTML = `<div class="viewer" id="viewer"><img id="viewer-img" alt="Cargando imagen…"></div>${thumbs}
    <div class="viewer-controls">
      <label class="form-field">Brillo <input type="range" id="viewer-brightness" min="40" max="250" value="100"></label>
      <label class="form-field">Contraste <input type="range" id="viewer-contrast" min="40" max="300" value="100"></label>
      <label class="form-field">Zoom <input type="range" id="viewer-zoom" min="100" max="400" value="100"></label>
      <label class="check"><input type="checkbox" id="viewer-invert"> Invertir</label>
    </div>
    <p class="form-hint" id="viewer-status">Vista previa de 8 bits generada por el PACS: sirve para revisar el flujo, no para diagnóstico. Un estudio real se lee con ventaneo sobre los 12–16 bits originales.</p>
    <div class="modal-actions">${back}<button class="button secondary" type="button" id="viewer-reset">Restablecer</button><button class="button secondary" type="button" data-action="close">Cerrar</button></div>`;

  const apply = () => {
    const img = $("viewer-img");
    const b = $("viewer-brightness").value, c = $("viewer-contrast").value, z = $("viewer-zoom").value;
    img.style.filter = `brightness(${b}%) contrast(${c}%)${$("viewer-invert").checked ? " invert(1)" : ""}`;
    img.style.width = `${z}%`;
  };
  ["viewer-brightness", "viewer-contrast", "viewer-zoom", "viewer-invert"].forEach((idName) => $(idName).addEventListener("input", apply));
  $("viewer-reset").addEventListener("click", () => {
    $("viewer-brightness").value = 100; $("viewer-contrast").value = 100; $("viewer-zoom").value = 100; $("viewer-invert").checked = false;
    apply();
  });
  document.querySelectorAll("[data-instance]").forEach((b) => b.addEventListener("click", () => showInstance(b.dataset.instance)));
  apply();
  await showInstance(images[0].instance_id);
}

function uploadImage(studyId, patientId) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".dcm,.dicom,application/dicom,image/png,image/jpeg";
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    toast(`Subiendo ${file.name}…`);
    try {
      const res = await apiRaw(`/api/v1/imaging-studies/${studyId}/images`, {
        method: "POST", body: file, contentType: file.type || "application/dicom",
      });
      const result = await res.json();
      toast(`Imagen guardada en el PACS · el estudio tiene ${result.images} ${result.images === 1 ? "imagen" : "imágenes"}`);
      await openPatient(patientId);
    } catch (err) {
      if (err.status !== 401) toast(err.message);
    }
  });
  input.click();
}

// La API permite editar al administrador y al operador que registro al paciente.
// Aqui solo se evita ofrecer un boton que terminaria en 403.
const canEditPatient = (p) => role() === "ADMIN" || (role() === "IPS_CLINICAL_OPERATOR" && p?.created_by === state.me?.id);

async function openEditPatient(id) {
  let p;
  try { p = await api(`/api/v1/patients/${id}`); } catch (err) { if (err.status !== 401) toast(err.message); return; }
  const today = new Date().toLocaleDateString("en-CA");
  const field = (label, name, attrs = "") => `<label class="form-field">${label}<input name="${name}" value="${escapeHTML(p[name] ?? "")}" ${attrs}></label>`;
  openDialog("Editar datos del paciente", `<div class="identity-box"><div><strong>${escapeHTML(fullName(p))}</strong><small>${escapeHTML(p.document_type)} ${escapeHTML(p.document_number)}</small></div></div><p class="dialog-intro">El documento identifica al paciente en la red y en FHIR, así que no se cambia aquí. La versión anterior de los datos queda guardada en el historial.</p><form id="edit-patient-form"><div class="form-grid">${field("Nombres", "first_name", 'required maxlength="120"')}${field("Apellidos", "last_name", 'required maxlength="120"')}<label class="form-field">Fecha de nacimiento<input name="birth_date" type="date" required max="${today}" value="${escapeHTML(p.birth_date)}"></label><label class="form-field">Sexo<select name="gender">${options(Object.entries(genders), p.gender)}</select></label>${field("Teléfono", "phone", 'type="tel" maxlength="40"')}${field("Correo", "email", 'type="email" maxlength="200"')}<label class="form-field full">Dirección<input name="address" value="${escapeHTML(p.address ?? "")}" maxlength="300"></label></div>${actions("Guardar cambios")}</form>`, "FICHA DEL PACIENTE");
  formSubmit("edit-patient-form", async (form) => {
    const changes = {};
    for (const key of ["first_name", "last_name", "birth_date", "gender", "phone", "email", "address"]) {
      const value = String(form.get(key) ?? "").trim();
      const before = p[key] ?? "";
      if (value !== before) changes[key] = value || null;
    }
    if (!Object.keys(changes).length) { closeDialog(); toast("No había cambios que guardar"); return; }
    await api(`/api/v1/patients/${id}`, { method: "PUT", body: changes });
    toast("Datos del paciente actualizados");
    await openPatient(id);
  });
}

async function openNewEncounter(patientId) {
  if (!canCreate()) return;
  if (!state.data.orgs.length) {
    try { await loadNetwork(); } catch (err) { if (err.status !== 401) toast(err.message); return; }
  }
  let p;
  try { p = await api(`/api/v1/patients/${patientId}`); } catch (err) { if (err.status !== 401) toast(err.message); return; }
  const operator = role() === "IPS_CLINICAL_OPERATOR";
  const now = new Date(); now.setSeconds(0, 0);
  const localNow = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const orgField = operator
    ? `<div class="form-field">Institución<p style="margin-top:14px;font-weight:400">${escapeHTML(ownOrgName())}</p></div>`
    : `<label class="form-field">Institución que atiende<select name="org" required><option value="">Selecciona la IPS</option>${options(orgOptions(), "")}</select></label>`;
  const vitalInputs = vitals.map(([code, name, unit]) => {
    const c = vitalCatalog[code];
    return `<label class="form-field">${name}<input name="v-${code}" type="number" step="any" inputmode="decimal" min="${c.min}" max="${c.max}"><small>${unit} · válido ${c.min}–${c.max}</small></label>`;
  }).join("");
  openDialog("Registrar atención", `<div class="identity-box"><div><strong>${escapeHTML(fullName(p))}</strong><small>${escapeHTML(p.document_type)} ${escapeHTML(p.document_number)}</small></div></div><form id="encounter-form"><div class="form-grid">${orgField}<label class="form-field">Tipo de atención<select name="encounter_class">${options(Object.entries(encounterClass), "AMB")}</select></label><label class="form-field">Prioridad<select name="priority">${options(Object.entries(priorities), "ROUTINE")}</select></label><label class="form-field">Fecha y hora<input name="started_at" type="datetime-local" required value="${localNow}" max="${localNow}"></label><label class="form-field full">Motivo de consulta<textarea name="reason" rows="2" maxlength="500" required placeholder="Por ejemplo: I10 - Control de hipertensión"></textarea></label></div><fieldset class="form-section"><legend>Signos vitales (opcionales)</legend><div class="vitals-grid">${vitalInputs}</div></fieldset><p class="form-hint">Cada medición se guarda con su código LOINC y su unidad UCUM. Un valor fuera del rango plausible no se acepta: suele ser un error de digitación.</p>${actions("Registrar atención")}</form>`, "NUEVA ATENCIÓN");

  formSubmit("encounter-form", async (form) => {
    const readings = [];
    for (const [code, name] of vitals) {
      const raw = String(form.get(`v-${code}`) ?? "").trim();
      if (!raw) continue;
      const value = Number(raw.replace(",", "."));
      const c = vitalCatalog[code];
      if (!Number.isFinite(value) || value < c.min || value > c.max) throw new ApiError(422, `${name}: ${raw} está fuera del rango válido (${c.min}–${c.max}).`);
      readings.push({ code, name, value, unit: c.ucum });
    }
    const sys = readings.find((r) => r.code === "8480-6"), dia = readings.find((r) => r.code === "8462-4");
    if (sys && dia && sys.value <= dia.value) throw new ApiError(422, "La presión sistólica debe ser mayor que la diastólica.");
    const orgId = operator ? state.me.organization_id : form.get("org");
    const startedAt = new Date(String(form.get("started_at"))).toISOString();
    const encounter = await api("/api/v1/encounters", {
      method: "POST",
      body: {
        patient_id: patientId, organization_id: orgId, encounter_class: form.get("encounter_class"),
        status: "in-progress", priority: form.get("priority"), reason_text: String(form.get("reason")).trim(), started_at: startedAt,
      },
    });
    const failed = [];
    for (const r of readings) {
      try {
        await api("/api/v1/observations", {
          method: "POST",
          body: { patient_id: patientId, encounter_id: encounter.id, status: "final", code: r.code, display: r.name, value_numeric: r.value, unit: r.unit, observed_at: startedAt },
        });
      } catch (err) {
        if (err.status === 401) throw err;
        failed.push(`${r.name} (${err.message})`);
      }
    }
    if (failed.length) toast(`La atención quedó registrada, pero no se guardó: ${failed.join("; ")}`);
    else toast(`Atención registrada${readings.length ? ` con ${readings.length} ${readings.length === 1 ? "medición" : "mediciones"}` : ""}`);
    await openPatient(patientId);
  });
}

async function openNewUser() {
  if (role() !== "ADMIN") return;
  if (!state.data.orgs.length) {
    try { await loadNetwork(); } catch (err) { if (err.status !== 401) toast(err.message); return; }
  }
  let epsList = [];
  try { epsList = items(await api("/api/v1/organizations?organization_type=EPS&page_size=20")); } catch { epsList = []; }
  const roleOptions = [["IPS_CLINICAL_OPERATOR", "Operación IPS"], ["EPS_COORDINATOR", "Coordinación EPS"], ["PATIENT", "Paciente (portal)"], ["ADMIN", "Administración"]];
  openDialog("Nueva cuenta", `<p class="dialog-intro">La clave se guarda cifrada y no aparece en la auditoría. Entrégala a la persona por un canal seguro.</p><form id="user-form" autocomplete="off"><div class="form-grid"><label class="form-field">Rol<select name="role" id="user-role">${options(roleOptions, "IPS_CLINICAL_OPERATOR")}</select></label><label class="form-field">Usuario<input name="username" required minlength="3" maxlength="80" pattern="[a-z0-9._\-]+" placeholder="nombre.apellido" autocomplete="off"></label><label class="form-field full" data-for="IPS_CLINICAL_OPERATOR">IPS<select name="ips"><option value="">Selecciona la IPS</option>${options(orgOptions(), "")}</select></label><label class="form-field full" data-for="EPS_COORDINATOR" hidden>EPS<select name="eps">${options(epsList.map((e) => [e.id, e.name]), epsList[0]?.id || "")}</select></label><label class="form-field full" data-for="PATIENT" hidden>Documento del paciente<input name="patient_document" inputmode="numeric" maxlength="32" placeholder="Número completo; la cuenta queda vinculada a su ficha"></label><label class="form-field full" data-hide-for="PATIENT">Nombre completo<input name="full_name" maxlength="200"></label><label class="form-field">Correo (opcional)<input name="email" type="email" maxlength="200"></label><label class="form-field">Clave inicial<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label></div>${actions("Crear cuenta")}</form>`, "ADMINISTRACIÓN DE CUENTAS");
  const sync = () => {
    const selected = $("user-role").value;
    document.querySelectorAll("#user-form [data-for]").forEach((el) => { el.hidden = el.dataset.for !== selected; });
    document.querySelectorAll("#user-form [data-hide-for]").forEach((el) => { el.hidden = el.dataset.hideFor === selected; });
  };
  $("user-role").addEventListener("change", sync);
  sync();
  formSubmit("user-form", async (form) => {
    const selected = form.get("role");
    const clean = (key) => String(form.get(key) ?? "").trim() || null;
    const body = { role: selected, username: clean("username"), full_name: clean("full_name"), email: clean("email"), password: String(form.get("password") ?? "") };
    if (selected === "IPS_CLINICAL_OPERATOR") body.organization_id = clean("ips");
    if (selected === "EPS_COORDINATOR") body.organization_id = clean("eps");
    if (selected === "PATIENT") body.patient_document = clean("patient_document");
    const user = await api("/api/v1/admin/users", { method: "POST", body });
    closeDialog();
    toast(`Cuenta ${user.username} creada`);
    await loadAndRender();
  });
}

async function epsId() {
  if (state.data.eps) return state.data.eps;
  const page = await api("/api/v1/organizations?organization_type=EPS&page_size=5");
  const eps = items(page)[0];
  if (!eps) throw new ApiError(409, "No hay una EPS registrada para afiliar al paciente.");
  state.data.eps = eps.id;
  return eps.id;
}

function openNewPatient() {
  if (!canCreate()) return;
  const today = new Date().toLocaleDateString("en-CA");
  openDialog("Registrar paciente", `<p class="dialog-intro">El paciente queda afiliado a la EPS de la red. El documento no puede repetirse.</p><form id="patient-form"><div class="form-grid"><label class="form-field">Nombres<input name="first_name" required maxlength="120" autocomplete="off"></label><label class="form-field">Apellidos<input name="last_name" required maxlength="120" autocomplete="off"></label><label class="form-field">Tipo de documento<select name="document_type">${options(Object.entries(documentTypes), "CC")}</select></label><label class="form-field">Número de documento<input name="document_number" required minlength="4" maxlength="32" inputmode="numeric" autocomplete="off"></label><label class="form-field">Fecha de nacimiento<input name="birth_date" type="date" required max="${today}"></label><label class="form-field">Sexo<select name="gender" required>${options(Object.entries(genders), "")}</select></label><label class="form-field">Teléfono (opcional)<input name="phone" type="tel" maxlength="40" autocomplete="off"></label><label class="form-field">Correo (opcional)<input name="email" type="email" maxlength="200" autocomplete="off"></label></div>${actions("Guardar paciente")}</form>`, "NUEVO REGISTRO");
  formSubmit("patient-form", async (form) => {
    const clean = (key) => String(form.get(key) || "").trim() || null;
    const patient = await api("/api/v1/patients", {
      method: "POST",
      body: {
        first_name: clean("first_name"), last_name: clean("last_name"),
        document_type: form.get("document_type"), document_number: clean("document_number"),
        birth_date: form.get("birth_date"), gender: form.get("gender"),
        phone: clean("phone"), email: clean("email"), eps_organization_id: await epsId(),
      },
    });
    closeDialog();
    toast(`${fullName(patient)} quedó registrado`);
    await navigate("patients", { search: patient.document_number });
  });
}

async function openRequest(patientId = "") {
  if (!canCreate()) return;
  if (!state.data.orgs.length) {
    try { await loadNetwork(); } catch (err) { if (err.status !== 401) toast(err.message); return; }
  }
  let patient = null;
  if (patientId) {
    try { patient = await api(`/api/v1/patients/${patientId}`); } catch (err) { if (err.status !== 401) toast(err.message); return; }
  }
  const operator = role() === "IPS_CLINICAL_OPERATOR";
  const patientField = patient
    ? `<div class="identity-box form-field full"><div><strong>${escapeHTML(fullName(patient))}</strong><small>${escapeHTML(patient.document_type)} ${escapeHTML(patient.document_number)}</small></div></div>`
    : '<label class="form-field full">Documento del paciente<input name="document" required minlength="4" maxlength="32" inputmode="numeric" autocomplete="off" placeholder="Número completo, sin puntos"></label>';
  const orgField = operator
    ? `<div class="form-field"><span>Institución</span><p style="margin-top:14px;font-weight:400">${escapeHTML(ownOrgName())}</p></div>`
    : `<label class="form-field">Institución que atiende<select name="org" required><option value="">Selecciona la IPS</option>${options(orgOptions(), "")}</select></label>`;
  openDialog("Solicitar una cama", `<p class="dialog-intro">La solicitud entra a la cola según su prioridad. Si el paciente no tiene un ingreso abierto en la institución, se registra uno con el motivo indicado.</p><form id="request-form"><div class="form-grid">${patientField}${orgField}<label class="form-field">Servicio requerido<select name="service" required>${options(services().map((s) => [s, s]), "")}</select></label><label class="form-field">Prioridad<select name="priority" required><option value="">Selecciona la prioridad</option>${options(Object.entries(priorities), "")}</select></label><label class="form-field full">Motivo de ingreso<textarea name="reason" rows="2" maxlength="500" required placeholder="Por ejemplo: I50 - Insuficiencia cardiaca descompensada"></textarea></label></div>${actions("Crear solicitud")}</form>`, "COORDINACIÓN DE LA ATENCIÓN");

  formSubmit("request-form", async (form) => {
    let target = patient;
    if (!target) {
      const documentNumber = String(form.get("document")).trim();
      target = items(await api(`/api/v1/patients?document_number=${encodeURIComponent(documentNumber)}&page_size=1`))[0];
      if (!target) throw new ApiError(404, "No hay un paciente con ese documento. Regístralo primero desde Pacientes.");
    }
    const orgId = operator ? state.me.organization_id : form.get("org");
    const priority = form.get("priority");
    // La solicitud se abre sobre un encuentro en curso de esa institucion.
    const encounters = items(await api(`/api/v1/patients/${target.id}/encounters`));
    let encounter = encounters.find((e) => e.organization_id === orgId && e.status === "in-progress");
    if (!encounter) {
      encounter = await api("/api/v1/encounters", {
        method: "POST",
        body: {
          patient_id: target.id, organization_id: orgId,
          encounter_class: priority === "EMERGENCY" ? "EMER" : "IMP", status: "in-progress",
          priority, reason_text: String(form.get("reason")).trim(), started_at: new Date().toISOString(),
        },
      });
    }
    await api("/api/v1/bed-requests", { method: "POST", body: { encounter_id: encounter.id, required_service: form.get("service"), priority } });
    closeDialog();
    toast(`Solicitud de ${fullName(target)} añadida a la cola`);
    await navigate("queue");
  });
}

function openAccount(id, op) {
  const user = state.data.users.find((u) => u.id === id);
  if (!user) return;
  const unlock = op === "unlock";
  openDialog(unlock ? "Desbloquear cuenta" : "Bloquear cuenta", `<div class="identity-box"><div><strong>${escapeHTML(user.full_name)}</strong><small class="mono">${escapeHTML(user.username)} · ${roleNames[user.role] || ""}</small></div>${unlock ? badge("bad", "Bloqueada") : badge("ok", "Activa")}</div><p class="dialog-intro">${unlock ? "La cuenta vuelve a entrar y su contador de intentos fallidos se reinicia." : "La cuenta no podrá entrar hasta que la administración la desbloquee. Úsalo, por ejemplo, si una credencial quedó expuesta."}</p><form id="account-form"><label class="form-field">Motivo (queda en la auditoría)<textarea name="reason" rows="2" maxlength="300" required placeholder="${unlock ? "Por ejemplo: identidad verificada por teléfono" : "Por ejemplo: credencial expuesta"}"></textarea></label>${actions(unlock ? "Desbloquear" : "Bloquear")}</form>`, "ADMINISTRACIÓN DE CUENTAS");
  formSubmit("account-form", async (form) => {
    await api(`/api/v1/admin/users/${id}/${op}`, { method: "POST", body: { reason: String(form.get("reason")).trim() } });
    closeDialog();
    toast(unlock ? `${user.username} puede volver a entrar` : `${user.username} quedó bloqueada`);
    await loadAndRender();
  });
}

/* ------------------------------------------------------------------ sesion */

function showLogin(message = "", tone = "") {
  closeDialog();
  $("app-shell").hidden = true;
  $("login-screen").hidden = false;
  const box = $("login-message");
  box.hidden = !message;
  box.textContent = message;
  box.className = `login-message ${tone}`;
  $("login-password").value = "";
  $("login-username").focus();
}

async function startSession() {
  state.me = await api("/api/v1/auth/me");
  $("login-screen").hidden = true;
  $("app-shell").hidden = false;
  $("profile-name").textContent = state.me.full_name;
  $("profile-role").textContent = roleNames[state.me.role] || state.me.role;
  $("profile-avatar").textContent = initials(state.me.full_name);
  $("session-tag").textContent = `Conectado · ${roleNames[state.me.role] || state.me.role}`;
  const initial = location.hash.slice(1);
  await navigate(allowedViews().includes(initial) ? initial : allowedViews()[0]);
}

function endSession(message = "") {
  store.remove(TOKEN_KEY);
  state.me = null;
  state.data = emptyData();
  state.loaded = {};
  state.queueLoaded = false;
  state.updated = null;
  resetFilters();
  showLogin(message);
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = $("login-username").value.trim();
  const password = $("login-password").value;
  if (!username || !password) { showLogin("Escribe el usuario y la contraseña."); $("login-username").value = username; return; }
  const button = event.target.querySelector('[type="submit"]');
  button.disabled = true;
  button.textContent = "Verificando…";
  try {
    const token = await api("/api/v1/auth/login", { method: "POST", body: { username, password } });
    store.set(TOKEN_KEY, token.access_token);
    await startSession();
  } catch (err) {
    if (err.status === 401) showLogin("Usuario o contraseña incorrectos. Al tercer intento fallido la cuenta se bloquea.");
    else if (err.status === 423) showLogin(err.message, "locked");
    else showLogin(err.message);
    $("login-username").value = username;
    if (err.status === 401 || err.status === 423) $("login-password").focus();
  } finally {
    button.disabled = false;
    button.textContent = "Ingresar";
  }
});

/* ------------------------------------------------------------------ eventos */

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-view]");
  if (nav && state.me) {
    event.preventDefault();
    navigate(nav.dataset.view, { org: nav.dataset.org || "", status: nav.dataset.status || "", service: nav.dataset.service || "" });
    return;
  }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const { action, id, op } = target.dataset;
  if (action === "close") closeDialog();
  if (action === "clear") { resetFilters(); state.view === "patients" ? loadAndRender() : render(); }
  if (action === "retry") loadAndRender();
  if (action === "assign") openAssign(id);
  if (action === "assign-exception") openAssign(id, { exception: true });
  if (action === "next-request") openAssign(id);
  if (action === "bed") openBed(id);
  if (action === "release" && canAssign()) releaseBed(id);
  if (action === "patient") openPatient(id);
  if (action === "new-patient") openNewPatient();
  if (action === "request") openRequest();
  if (action === "patient-request") openRequest(id);
  if (action === "account") openAccount(id, op);
  if (action === "images") openImages(id, target.dataset.patient, target.dataset.title);
  if (action === "edit-patient") openEditPatient(id);
  if (action === "new-encounter") openNewEncounter(id);
  if (action === "new-user") openNewUser();
  if (action === "upload-image" && canCreate()) uploadImage(id, target.dataset.patient);
});
$("dialog-close").addEventListener("click", closeDialog);
$("dialog").addEventListener("close", () => {
  releaseViewer();
  if (dialogTrigger?.isConnected) dialogTrigger.focus({ preventScroll: true });
  else $("main").focus({ preventScroll: true });
});
$("refresh").addEventListener("click", async () => { await loadAndRender(); if (!state.error) toast("Datos actualizados"); });
$("logout").addEventListener("click", () => endSession());
window.addEventListener("hashchange", () => {
  const view = location.hash.slice(1);
  if (state.me && view !== state.view && allowedViews().includes(view)) navigate(view);
});

if (store.get(TOKEN_KEY)) {
  startSession().catch((err) => { if (err.status !== 401) endSession(err.message); });
} else {
  showLogin();
}
