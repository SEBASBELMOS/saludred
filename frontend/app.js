// SaludRed web UI — plain JavaScript, no dependencies.
//
// The API address is resolved at startup. In normal operation the UI is served
// by its own container, whose reverse proxy forwards /api to the backend, so
// the same origin ("") answers and the browser never crosses origins. The
// local development server is kept as a fallback for opening the files
// directly. The first base whose /health answers wins.
const API_BASE_CANDIDATES = [
  "",
  "http://localhost:8000",
];

let API_BASE = API_BASE_CANDIDATES[0];

async function resolveApiBase() {
  for (const candidate of API_BASE_CANDIDATES) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2500);
      const response = await fetch(candidate + "/health", {
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (response.ok) {
        API_BASE = candidate;
        return;
      }
    } catch {
      // Unreachable candidate, try the next one.
    }
  }
  // Every candidate failed: keep the first one and let api() surface the
  // connection error when the user tries to log in.
}

// ---------------------------------------------------------------------------
// Session state (token lives in memory only, never persisted)
// ---------------------------------------------------------------------------

const state = {
  token: null,
  user: null, // { id, username, full_name, role, organization_id, patient_id }
  currentView: null,
  patientsPage: 1,
  patientsPageSize: 20,
  selectedCandidateId: null,
};

const CAN_ASSIGN = ["ADMIN", "EPS_COORDINATOR"];
const CAN_CREATE_PATIENT = ["ADMIN", "IPS_CLINICAL_OPERATOR"];

const ROLE_LABELS = {
  ADMIN: "Administrador",
  EPS_COORDINATOR: "Coordinador EPS",
  IPS_CLINICAL_OPERATOR: "Operador clínico IPS",
  PATIENT: "Paciente",
};

const PRIORITY_LABELS = {
  EMERGENCY: "Emergencia",
  URGENT: "Urgente",
  ROUTINE: "Rutina",
};

const PRIORITY_BADGES = {
  EMERGENCY: "badge-emergency",
  URGENT: "badge-urgent",
  ROUTINE: "badge-routine",
};

const PRIORITY_ROWS = {
  EMERGENCY: "row-emergency",
  URGENT: "row-urgent",
  ROUTINE: "",
};

const BED_STATUS_LABELS = {
  AVAILABLE: "Disponible",
  OCCUPIED: "Ocupada",
  RESERVED: "Reservada",
  CLEANING: "Limpieza",
  BLOCKED: "Bloqueada",
  MAINTENANCE: "Mantenimiento",
};

const BED_STATUS_BADGES = {
  AVAILABLE: "badge-available",
  OCCUPIED: "badge-occupied",
  RESERVED: "badge-reserved",
  CLEANING: "badge-cleaning",
  BLOCKED: "badge-blocked",
  MAINTENANCE: "badge-maintenance",
};

// Statuses offered when changing a bed by hand. OCCUPIED is never an option:
// a bed gets occupied by assigning a request from the queue.
const MANUAL_BED_STATUSES = ["AVAILABLE", "RESERVED", "CLEANING", "BLOCKED", "MAINTENANCE"];

const GENDER_LABELS = {
  male: "Masculino",
  female: "Femenino",
  other: "Otro",
  unknown: "Desconocido",
};

const ENCOUNTER_CLASS_LABELS = {
  IMP: "Hospitalización",
  AMB: "Ambulatorio",
  EMER: "Emergencia",
};

const ENCOUNTER_STATUS_LABELS = {
  planned: "Planificado",
  arrived: "Llegó",
  "in-progress": "En curso",
  finished: "Finalizado",
  cancelled: "Cancelado",
};

const OBSERVATION_STATUS_LABELS = {
  registered: "Registrada",
  preliminary: "Preliminar",
  final: "Final",
  amended: "Enmendada",
  cancelled: "Cancelada",
};

const NAV_ITEMS = {
  ADMIN: [
    { id: "capacity", label: "Capacidad" },
    { id: "queue", label: "Cola de espera" },
    { id: "beds", label: "Camas" },
    { id: "patients", label: "Pacientes" },
  ],
  EPS_COORDINATOR: [
    { id: "capacity", label: "Capacidad" },
    { id: "queue", label: "Cola de espera" },
    { id: "beds", label: "Camas" },
    { id: "patients", label: "Pacientes" },
  ],
  IPS_CLINICAL_OPERATOR: [
    { id: "capacity", label: "Capacidad" },
    { id: "queue", label: "Cola de espera" },
    { id: "beds", label: "Camas" },
    { id: "patients", label: "Pacientes" },
  ],
  PATIENT: [{ id: "portal", label: "Mi información" }],
};

// ---------------------------------------------------------------------------
// Tiny helpers
// ---------------------------------------------------------------------------

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function $id(id) {
  return document.getElementById(id);
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// API wrapper: auth header, error normalization, 401 session handling
// ---------------------------------------------------------------------------

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;

  let response;
  try {
    response = await fetch(API_BASE + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(
      0,
      "No se pudo conectar con el servidor. Verificá que la API esté en marcha."
    );
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  // A 401 with an active session means the token expired: drop everything and
  // go back to the login screen. During login itself there is no session yet,
  // so the 401 is just "wrong credentials" and is shown as such.
  if (response.status === 401 && state.token) {
    handleSessionExpired();
    throw new ApiError(401, "Tu sesión venció. Volvé a iniciar sesión.");
  }

  if (!response.ok) {
    throw new ApiError(response.status, extractDetail(data, response.status));
  }

  return data;
}

function extractDetail(data, status) {
  if (data && typeof data.detail === "string") return data.detail;
  if (data && Array.isArray(data.detail)) {
    return data.detail.map((item) => item.msg).filter(Boolean).join("; ");
  }
  if (data && typeof data.detail === "object" && data.detail !== null) {
    return JSON.stringify(data.detail);
  }
  return `Error del servidor (${status})`;
}

// ---------------------------------------------------------------------------
// Toast and status banners
// ---------------------------------------------------------------------------

function showToast(message, kind = "info") {
  const container = $id("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 5200);
}

function setStatus(elementId, { loading = false, error = null } = {}) {
  const el = $id(elementId);
  if (!el) return;
  if (loading) {
    el.className = "status-message loading";
    el.innerHTML = '<span class="spinner"></span>Cargando…';
    return;
  }
  if (error) {
    el.className = "status-message error";
    el.textContent = error;
    return;
  }
  el.className = "";
  el.textContent = "";
}

function statusFor(viewId) {
  return `status-${viewId}`;
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function formatMinutes(total) {
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const mins = total % 60;
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${mins} min`;
  return `${mins} min`;
}

function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatPercent(rate) {
  return `${Math.round(rate * 100)}%`;
}

// ---------------------------------------------------------------------------
// Login / logout / session
// ---------------------------------------------------------------------------

function showLogin(message = null) {
  $id("app-shell").classList.add("hidden");
  $id("view-login").classList.remove("hidden");
  const errorEl = $id("login-error");
  if (message) {
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
  } else {
    errorEl.classList.add("hidden");
  }
}

function handleSessionExpired() {
  state.token = null;
  state.user = null;
  state.currentView = null;
  closeModal();
  showLogin("Tu sesión venció. Volvé a iniciar sesión.");
}

function logout() {
  state.token = null;
  state.user = null;
  state.currentView = null;
  state.patientsPage = 1;
  closeModal();
  showLogin();
}

async function submitLogin(event) {
  event.preventDefault();
  const username = $id("login-username").value.trim();
  const password = $id("login-password").value;
  const errorEl = $id("login-error");
  const submitBtn = $id("login-submit");

  errorEl.classList.add("hidden");
  submitBtn.disabled = true;
  submitBtn.textContent = "Ingresando…";

  try {
    const loginData = await api("/api/v1/auth/login", {
      method: "POST",
      body: { username, password },
    });
    state.token = loginData.access_token;

    const me = await api("/api/v1/auth/me");
    state.user = me;

    enterApp();
  } catch (error) {
    errorEl.textContent = error.message;
    errorEl.classList.remove("hidden");
    state.token = null;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Iniciar sesión";
  }
}

function enterApp() {
  $id("view-login").classList.add("hidden");
  $id("app-shell").classList.remove("hidden");
  renderNav();
  renderUserInfo();
  const firstView = state.user.role === "PATIENT" ? "portal" : "capacity";
  showView(firstView);
}

function renderNav() {
  const nav = $id("nav");
  nav.innerHTML = "";
  const items = NAV_ITEMS[state.user.role] || [];
  items.forEach((item) => {
    const button = document.createElement("button");
    button.className = "nav-item";
    button.dataset.view = item.id;
    button.textContent = item.label;
    button.addEventListener("click", () => showView(item.id));
    nav.appendChild(button);
  });
}

function renderUserInfo() {
  const user = state.user;
  $id("user-info").innerHTML =
    `<strong>${esc(user.full_name)}</strong>` +
    `${esc(ROLE_LABELS[user.role] || user.role)}`;
}

// ---------------------------------------------------------------------------
// View switching
// ---------------------------------------------------------------------------

const LOADERS = {
  capacity: loadCapacity,
  queue: loadQueue,
  beds: loadBedsView,
  patients: loadPatients,
  portal: loadPortal,
};

function showView(viewId) {
  document.querySelectorAll(".view").forEach((view) => view.classList.add("hidden"));
  const section = $id(`view-${viewId}`);
  if (section) section.classList.remove("hidden");

  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === viewId);
  });

  state.currentView = viewId;
  state.selectedCandidateId = null;

  const loader = LOADERS[viewId];
  if (loader) loader();
}

function refreshCurrentView() {
  const loader = LOADERS[state.currentView];
  if (loader) loader();
}

// ---------------------------------------------------------------------------
// View 1: capacity dashboard
// ---------------------------------------------------------------------------

async function loadCapacity() {
  const statusId = statusFor("capacity");
  setStatus(statusId, { loading: true });
  try {
    const data = await api("/api/v1/network/capacity");
    renderCapacity(data);
    setStatus(statusId);
  } catch (error) {
    setStatus(statusId, { error: error.message });
    $id("capacity-summary").innerHTML = "";
    $id("capacity-orgs").innerHTML = "";
  }
}

function renderCapacity(data) {
  const summary = $id("capacity-summary");
  summary.innerHTML = `
    <div class="summary-stat">
      <div class="stat-value mono">${data.total}</div>
      <div class="stat-label">Camas en la red</div>
    </div>
    <div class="summary-stat stat-available">
      <div class="stat-value mono">${data.available}</div>
      <div class="stat-label">Camas disponibles</div>
    </div>
    <div class="summary-stat">
      <div class="stat-value mono">${data.occupied}</div>
      <div class="stat-label">Camas ocupadas</div>
    </div>
    <div class="summary-stat ${data.available === 0 ? "stat-critical" : ""}">
      <div class="stat-value mono">${formatPercent(data.occupancy_rate)}</div>
      <div class="stat-label">Ocupación de la red</div>
    </div>
    <div class="summary-stat">
      <div class="stat-value mono">${data.pending_requests}</div>
      <div class="stat-label">Solicitudes pendientes</div>
    </div>
  `;

  const orgsEl = $id("capacity-orgs");
  orgsEl.innerHTML = "";
  if (!data.organizations || data.organizations.length === 0) {
    orgsEl.innerHTML =
      '<div class="card empty-state">No hay instituciones con camas registradas.</div>';
    return;
  }
  data.organizations.forEach((org) => orgsEl.appendChild(renderOrgCard(org)));
}

function renderOrgCard(org) {
  const card = document.createElement("div");
  card.className = "card" + (org.available === 0 ? " card-critical" : "");

  const occupancyClass =
    org.occupancy_rate >= 0.9 ? "full" : org.occupancy_rate >= 0.7 ? "high" : "";

  card.innerHTML = `
    <div class="card-header">
      <h3>${esc(org.organization_name)}</h3>
      <span class="muted mono">${esc(org.organization_code)}</span>
    </div>
    ${org.available === 0 ? '<div class="no-beds-alert">Sin camas disponibles</div>' : ""}
    <div class="chips">
      <span class="chip chip-available">Disponibles <strong>${org.available}</strong></span>
      <span class="chip chip-occupied">Ocupadas <strong>${org.occupied}</strong></span>
      <span class="chip chip-reserved">Reservadas <strong>${org.reserved}</strong></span>
      <span class="chip chip-cleaning">Limpieza <strong>${org.cleaning}</strong></span>
      <span class="chip chip-blocked">Bloqueadas <strong>${org.blocked}</strong></span>
      <span class="chip chip-maintenance">Mantenimiento <strong>${org.maintenance}</strong></span>
      <span class="chip chip-pending">Pendientes <strong>${org.pending_requests}</strong></span>
    </div>
    <div class="progress-track"><div class="progress-fill ${occupancyClass}" style="width: ${Math.round(org.occupancy_rate * 100)}%"></div></div>
    <div class="occupancy-label">Ocupación: ${formatPercent(org.occupancy_rate)} de ${org.total} camas</div>
    ${renderServicesTable(org.services)}
  `;
  return card;
}

function renderServicesTable(services) {
  if (!services || services.length === 0) return "";
  const rows = services
    .map(
      (service) => `
    <tr>
      <td>${esc(service.service)}</td>
      <td class="num mono">${service.total}</td>
      <td class="num mono">${service.available}</td>
      <td class="num mono">${service.occupied}</td>
      <td class="num mono">${service.reserved}</td>
      <td class="num mono">${service.cleaning}</td>
      <td class="num mono">${service.blocked}</td>
      <td class="num mono">${service.maintenance}</td>
    </tr>`
    )
    .join("");
  return `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Servicio</th>
            <th class="num">Total</th>
            <th class="num">Disp.</th>
            <th class="num">Ocup.</th>
            <th class="num">Reserv.</th>
            <th class="num">Limp.</th>
            <th class="num">Bloq.</th>
            <th class="num">Mant.</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

// ---------------------------------------------------------------------------
// View 2: waiting queue
// ---------------------------------------------------------------------------

async function loadQueue() {
  const statusId = statusFor("queue");
  setStatus(statusId, { loading: true });
  try {
    const queue = await api("/api/v1/bed-requests/queue");
    renderQueue(queue);
    setStatus(statusId);
  } catch (error) {
    setStatus(statusId, { error: error.message });
    $id("queue-table-wrap").innerHTML = "";
  }
}

function renderQueue(queue) {
  const wrap = $id("queue-table-wrap");
  if (!queue || queue.length === 0) {
    wrap.innerHTML =
      '<div class="card empty-state">No hay solicitudes de cama pendientes.</div>';
    return;
  }

  const canAssign = CAN_ASSIGN.includes(state.user.role);

  const rows = queue
    .map((request) => {
      const priorityBadge = PRIORITY_BADGES[request.priority] || "badge-routine";
      const rowClass = PRIORITY_ROWS[request.priority] || "";
      const action = canAssign
        ? `<button class="btn btn-primary btn-sm" data-assign="${esc(request.id)}">Asignar</button>`
        : "";
      return `
      <tr class="${rowClass}">
        <td class="num mono">${request.queue_position}</td>
        <td><span class="badge ${priorityBadge}">${esc(PRIORITY_LABELS[request.priority] || request.priority)}</span></td>
        <td>${esc(request.patient_name)}</td>
        <td class="mono muted">${esc(request.patient_document)}</td>
        <td>${esc(request.required_service)}</td>
        <td class="mono">${formatMinutes(request.waiting_minutes)}</td>
        ${canAssign ? `<td>${action}</td>` : ""}
      </tr>`;
    })
    .join("");

  wrap.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="num">Posición</th>
            <th>Prioridad</th>
            <th>Paciente</th>
            <th>Documento</th>
            <th>Servicio requerido</th>
            <th>Tiempo de espera</th>
            ${canAssign ? "<th>Acción</th>" : ""}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  if (canAssign) {
    wrap.querySelectorAll("[data-assign]").forEach((button) => {
      button.addEventListener("click", () => {
        const request = queue.find((item) => item.id === button.dataset.assign);
        openAssignModal(request);
      });
    });
  }
}

// ---------------------------------------------------------------------------
// Assign flow (candidates + priority override on 409)
// ---------------------------------------------------------------------------

function openAssignModal(request) {
  state.selectedCandidateId = null;
  openModal(
    `Asignar cama — ${request.patient_name}`,
    `
    <div class="detail-grid" style="margin-bottom: 12px">
      <div class="detail-item">
        <div class="detail-label">Prioridad</div>
        <div class="detail-value">${esc(PRIORITY_LABELS[request.priority] || request.priority)}</div>
      </div>
      <div class="detail-item">
        <div class="detail-label">Servicio requerido</div>
        <div class="detail-value">${esc(request.required_service)}</div>
      </div>
      <div class="detail-item">
        <div class="detail-label">Espera</div>
        <div class="detail-value">${formatMinutes(request.waiting_minutes)}</div>
      </div>
    </div>
    <div id="assign-error"></div>
    <div id="candidates-box"><div class="status-message loading"><span class="spinner"></span>Cargando camas candidatas…</div></div>
    `
  );
  loadCandidates(request);
}

async function loadCandidates(request) {
  const box = $id("candidates-box");
  if (!box) return;
  try {
    const candidates = await api(`/api/v1/bed-requests/${request.id}/candidates`);
    renderCandidates(candidates, request);
  } catch (error) {
    box.innerHTML = `<div class="status-message error">${esc(error.message)}</div>`;
  }
}

function renderCandidates(candidates, request) {
  const box = $id("candidates-box");
  if (!box) return;

  if (!candidates || candidates.length === 0) {
    box.innerHTML =
      '<div class="empty-state">No hay camas candidatas disponibles para esta solicitud.</div>';
    return;
  }

  state.selectedCandidateId = candidates[0].location_id;

  const items = candidates
    .map((candidate) => {
      const flags = [];
      if (candidate.same_service) {
        flags.push('<span class="badge badge-same-service">Mismo servicio</span>');
      }
      if (candidate.same_organization) {
        flags.push('<span class="badge badge-same-org">Misma institución</span>');
      }
      return `
      <div class="candidate ${candidate.location_id === state.selectedCandidateId ? "selected" : ""}"
           data-candidate="${esc(candidate.location_id)}" role="button" tabindex="0">
        <input type="radio" name="candidate" value="${esc(candidate.location_id)}"
               ${candidate.location_id === state.selectedCandidateId ? "checked" : ""}>
        <div class="candidate-main">
          <div class="candidate-title">${esc(candidate.name)} <span class="muted mono">${esc(candidate.code)}</span></div>
          <div class="candidate-sub">${esc(candidate.service || "Sin servicio")} · ${esc(candidate.organization_name)}</div>
        </div>
        <div class="candidate-flags">${flags.join("")}</div>
      </div>`;
    })
    .join("");

  box.innerHTML = `
    <p class="muted" style="margin: 0; font-size: 14px">
      Ordenadas por conveniencia: primero las camas del servicio pedido.
      Elegí una y confirmá la asignación.
    </p>
    <div class="candidate-list">${items}</div>
    <div class="modal-actions">
      <button type="button" class="btn btn-outline" data-close>Cancelar</button>
      <button type="button" class="btn btn-primary" id="assign-confirm">Asignar cama seleccionada</button>
    </div>`;

  box.querySelectorAll(".candidate").forEach((row) => {
    row.addEventListener("click", () => selectCandidate(row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectCandidate(row);
      }
    });
  });

  box.querySelector("[data-close]").addEventListener("click", closeModal);
  $id("assign-confirm").addEventListener("click", () => {
    const locationId = state.selectedCandidateId;
    if (!locationId) return;
    submitAssign(request, locationId, false, null);
  });
}

function selectCandidate(row) {
  state.selectedCandidateId = row.dataset.candidate;
  document.querySelectorAll(".candidate").forEach((candidate) => {
    candidate.classList.toggle("selected", candidate === row);
  });
  row.querySelector('input[type="radio"]').checked = true;
}

async function submitAssign(request, locationId, override, reason) {
  const errorBox = $id("assign-error");
  const confirmBtn = $id("assign-confirm");
  if (errorBox) errorBox.innerHTML = "";
  if (confirmBtn) confirmBtn.disabled = true;

  try {
    await api(`/api/v1/bed-requests/${request.id}/assign`, {
      method: "POST",
      body: {
        location_id: locationId,
        override_priority: override,
        reason: override ? reason : null,
      },
    });
    closeModal();
    showToast(`Cama asignada a ${request.patient_name}`, "success");
    refreshCurrentView();
  } catch (error) {
    if (confirmBtn) confirmBtn.disabled = false;
    if (error.status === 409 && !override) {
      renderOverridePanel(error.message, request, locationId);
    } else if (errorBox) {
      errorBox.innerHTML = `<div class="status-message error">${esc(error.message)}</div>`;
    }
  }
}

// When the backend refuses because another request is more urgent, that is the
// priority rule working — offer a deliberate, audited way to skip it.
function renderOverridePanel(detail, request, locationId) {
  const errorBox = $id("assign-error");
  errorBox.innerHTML = `
    <div class="override-panel">
      <div class="override-title">El sistema no permite esta asignación todavía</div>
      <p>${esc(detail)}</p>
      <p><strong>¿Saltar el orden de prioridad?</strong> Esta decisión queda registrada en la auditoría.</p>
      <div class="override-reason">
        <label class="field">
          <span class="field-label">Justificación (obligatoria)</span>
          <textarea id="override-reason" maxlength="300" rows="2" placeholder="Motivo por el que esta solicitud se atiende antes que las más urgentes"></textarea>
        </label>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn btn-outline" id="override-cancel">Volver</button>
        <button type="button" class="btn btn-danger" id="override-confirm">Asignar igualmente</button>
      </div>
    </div>`;

  const confirmBtn = $id("assign-confirm");
  if (confirmBtn) confirmBtn.style.display = "none";

  $id("override-cancel").addEventListener("click", () => {
    errorBox.innerHTML = "";
    if (confirmBtn) confirmBtn.style.display = "";
  });

  $id("override-confirm").addEventListener("click", () => {
    const reason = $id("override-reason").value.trim();
    if (!reason) {
      showToast("Escribí una justificación para saltar la prioridad", "error");
      return;
    }
    submitAssign(request, locationId, true, reason);
  });
}

// ---------------------------------------------------------------------------
// View 3: beds per institution
// ---------------------------------------------------------------------------

let bedsOrganizations = [];

async function loadBedsView() {
  const statusId = statusFor("beds");
  setStatus(statusId, { loading: true });
  try {
    const page = await api("/api/v1/organizations?organization_type=IPS&page_size=100");
    const items = page.items || [];
    if (state.user.role === "IPS_CLINICAL_OPERATOR") {
      bedsOrganizations = items.filter((org) => org.id === state.user.organization_id);
    } else {
      bedsOrganizations = items;
    }
    renderBedsOrgSelect();
    setStatus(statusId);
  } catch (error) {
    setStatus(statusId, { error: error.message });
  }
}

function renderBedsOrgSelect() {
  const select = $id("beds-org-select");
  select.innerHTML = "";
  bedsOrganizations.forEach((org) => {
    const option = document.createElement("option");
    option.value = org.id;
    option.textContent = org.name;
    select.appendChild(option);
  });
  if (state.user.role === "IPS_CLINICAL_OPERATOR") {
    select.disabled = bedsOrganizations.length <= 1;
    select.title = "Tu rol solo puede ver las camas de tu institución";
  }
  loadBeds();
}

async function loadBeds() {
  const statusId = statusFor("beds");
  const orgId = $id("beds-org-select").value;
  if (!orgId) {
    $id("beds-table-wrap").innerHTML =
      '<div class="card empty-state">No hay instituciones disponibles.</div>';
    setStatus(statusId);
    return;
  }
  setStatus(statusId, { loading: true });
  try {
    const statusFilter = $id("beds-status-filter").value;
    const serviceFilter = $id("beds-service-filter").value.trim();
    const query = new URLSearchParams();
    if (statusFilter) query.set("status", statusFilter);
    if (serviceFilter) query.set("service", serviceFilter);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    const beds = await api(`/api/v1/organizations/${orgId}/beds${suffix}`);
    renderBeds(beds);
    setStatus(statusId);
  } catch (error) {
    setStatus(statusId, { error: error.message });
    $id("beds-table-wrap").innerHTML = "";
  }
}

function renderBeds(beds) {
  const wrap = $id("beds-table-wrap");
  if (!beds || beds.length === 0) {
    wrap.innerHTML =
      '<div class="card empty-state">No hay camas que coincidan con el filtro.</div>';
    return;
  }

  const rows = beds
    .map(
      (bed) => `
    <tr>
      <td class="mono">${esc(bed.code)}</td>
      <td>${esc(bed.name)}</td>
      <td>${esc(bed.service || "—")}</td>
      <td><span class="badge ${BED_STATUS_BADGES[bed.status] || "badge-blocked"}">${esc(BED_STATUS_LABELS[bed.status] || bed.status)}</span></td>
      <td><button class="btn btn-outline btn-sm" data-bed-status="${esc(bed.id)}" data-bed-name="${esc(bed.name)}">Cambiar estado</button></td>
    </tr>`
    )
    .join("");

  wrap.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Código</th>
            <th>Nombre</th>
            <th>Servicio</th>
            <th>Estado</th>
            <th>Acción</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  wrap.querySelectorAll("[data-bed-status]").forEach((button) => {
    button.addEventListener("click", () => {
      openBedStatusModal(button.dataset.bedStatus, button.dataset.bedName);
    });
  });
}

function openBedStatusModal(bedId, bedName) {
  const options = MANUAL_BED_STATUSES.map(
    (status) => `<option value="${status}">${BED_STATUS_LABELS[status]}</option>`
  ).join("");

  openModal(
    `Cambiar estado — ${bedName}`,
    `
    <p class="muted" style="margin: 0 0 14px; font-size: 14px">
      Una cama se ocupa asignando una solicitud desde la cola de espera, no desde acá.
    </p>
    <label class="field">
      <span class="field-label">Nuevo estado</span>
      <select id="bed-new-status">${options}</select>
    </label>
    <label class="field">
      <span class="field-label">Motivo</span>
      <input id="bed-status-reason" type="text" maxlength="300" placeholder="p. ej. Limpieza terminada">
    </label>
    <div id="bed-status-error"></div>
    <div class="modal-actions">
      <button type="button" class="btn btn-outline" data-close>Cancelar</button>
      <button type="button" class="btn btn-primary" id="bed-status-confirm">Guardar</button>
    </div>`
  );

  $id("modal-body").querySelector("[data-close]").addEventListener("click", closeModal);
  $id("bed-status-confirm").addEventListener("click", async () => {
    const newStatus = $id("bed-new-status").value;
    const reason = $id("bed-status-reason").value.trim();
    const errorBox = $id("bed-status-error");
    errorBox.innerHTML = "";
    try {
      await api(`/api/v1/beds/${bedId}/status`, {
        method: "POST",
        body: { new_status: newStatus, reason: reason || null },
      });
      closeModal();
      showToast("Estado de la cama actualizado", "success");
      loadBeds();
    } catch (error) {
      errorBox.innerHTML = `<div class="status-message error">${esc(error.message)}</div>`;
    }
  });
}

// ---------------------------------------------------------------------------
// View 4: patients
// ---------------------------------------------------------------------------

async function loadPatients() {
  const statusId = statusFor("patients");
  setStatus(statusId, { loading: true });
  try {
    const page = await api(
      `/api/v1/patients?page=${state.patientsPage}&page_size=${state.patientsPageSize}`
    );
    renderPatients(page);
    setStatus(statusId);
  } catch (error) {
    setStatus(statusId, { error: error.message });
    $id("patients-table-wrap").innerHTML = "";
    $id("patients-pagination").innerHTML = "";
  }
}

function renderPatients(page) {
  const wrap = $id("patients-table-wrap");
  const canCreate = CAN_CREATE_PATIENT.includes(state.user.role);
  $id("new-patient-btn").classList.toggle("hidden", !canCreate);

  if (!page.items || page.items.length === 0) {
    wrap.innerHTML = '<div class="card empty-state">No hay pacientes registrados.</div>';
  } else {
    const rows = page.items
      .map(
        (patient) => `
      <tr>
        <td class="mono">${esc(patient.document_type)}-${esc(patient.document_number)}</td>
        <td>${esc(patient.first_name)} ${esc(patient.last_name)}</td>
        <td>${esc(GENDER_LABELS[patient.gender] || patient.gender)}</td>
        <td class="mono">${formatDate(patient.birth_date)}</td>
        <td class="mono muted">${esc(patient.phone || "—")}</td>
      </tr>`
      )
      .join("");
    wrap.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Documento</th>
              <th>Nombre</th>
              <th>Sexo</th>
              <th>Nacimiento</th>
              <th>Teléfono</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  renderPagination(page);
}

function renderPagination(page) {
  const totalPages = Math.max(1, Math.ceil(page.total / page.page_size));
  const el = $id("patients-pagination");
  el.innerHTML = `
    <button class="btn btn-outline btn-sm" id="patients-prev" ${state.patientsPage <= 1 ? "disabled" : ""}>← Anterior</button>
    <span>Página ${state.patientsPage} de ${totalPages} (${page.total} pacientes)</span>
    <button class="btn btn-outline btn-sm" id="patients-next" ${state.patientsPage >= totalPages ? "disabled" : ""}>Siguiente →</button>`;

  const prev = $id("patients-prev");
  const next = $id("patients-next");
  if (prev && !prev.disabled) {
    prev.addEventListener("click", () => {
      state.patientsPage -= 1;
      loadPatients();
    });
  }
  if (next && !next.disabled) {
    next.addEventListener("click", () => {
      state.patientsPage += 1;
      loadPatients();
    });
  }
}

async function openNewPatientModal() {
  openModal(
    "Nuevo paciente",
    `
    <div id="new-patient-body"><div class="status-message loading"><span class="spinner"></span>Cargando instituciones…</div></div>`
  );

  let epsOptions = "";
  try {
    const page = await api("/api/v1/organizations?organization_type=EPS&page_size=100");
    const epsList = page.items || [];
    epsOptions = epsList
      .map((org) => `<option value="${esc(org.id)}">${esc(org.name)}</option>`)
      .join("");
    if (epsList.length === 0) {
      throw new ApiError(409, "No hay EPS registrada para asignar al paciente");
    }
  } catch (error) {
    $id("new-patient-body").innerHTML =
      `<div class="status-message error">${esc(error.message)}</div>`;
    return;
  }

  $id("new-patient-body").innerHTML = `
    <div class="detail-grid">
      <label class="field">
        <span class="field-label">Tipo de documento</span>
        <select id="np-document-type">
          <option value="CC">CC</option>
          <option value="TI">TI</option>
          <option value="CE">CE</option>
          <option value="PA">PA</option>
          <option value="RC">RC</option>
        </select>
      </label>
      <label class="field">
        <span class="field-label">Número de documento</span>
        <input id="np-document-number" type="text" minlength="4" maxlength="32" required>
      </label>
      <label class="field">
        <span class="field-label">Nombres</span>
        <input id="np-first-name" type="text" maxlength="120" required>
      </label>
      <label class="field">
        <span class="field-label">Apellidos</span>
        <input id="np-last-name" type="text" maxlength="120" required>
      </label>
      <label class="field">
        <span class="field-label">Fecha de nacimiento</span>
        <input id="np-birth-date" type="date" required>
      </label>
      <label class="field">
        <span class="field-label">Sexo</span>
        <select id="np-gender">
          <option value="male">Masculino</option>
          <option value="female">Femenino</option>
          <option value="other">Otro</option>
          <option value="unknown">Desconocido</option>
        </select>
      </label>
      <label class="field">
        <span class="field-label">EPS</span>
        <select id="np-eps">${epsOptions}</select>
      </label>
      <label class="field">
        <span class="field-label">Teléfono (opcional)</span>
        <input id="np-phone" type="text" maxlength="40">
      </label>
    </div>
    <div id="new-patient-error"></div>
    <div class="modal-actions">
      <button type="button" class="btn btn-outline" data-close>Cancelar</button>
      <button type="button" class="btn btn-primary" id="new-patient-confirm">Guardar paciente</button>
    </div>`;

  $id("new-patient-body").querySelector("[data-close]").addEventListener("click", closeModal);
  $id("new-patient-confirm").addEventListener("click", submitNewPatient);
}

async function submitNewPatient() {
  const errorBox = $id("new-patient-error");
  errorBox.innerHTML = "";

  const payload = {
    document_type: $id("np-document-type").value,
    document_number: $id("np-document-number").value.trim(),
    first_name: $id("np-first-name").value.trim(),
    last_name: $id("np-last-name").value.trim(),
    birth_date: $id("np-birth-date").value,
    gender: $id("np-gender").value,
    eps_organization_id: $id("np-eps").value,
  };
  const phone = $id("np-phone").value.trim();
  if (phone) payload.phone = phone;

  if (
    !payload.document_number ||
    !payload.first_name ||
    !payload.last_name ||
    !payload.birth_date
  ) {
    errorBox.innerHTML =
      '<div class="status-message error">Completá los campos obligatorios.</div>';
    return;
  }

  try {
    await api("/api/v1/patients", { method: "POST", body: payload });
    closeModal();
    showToast("Paciente creado correctamente", "success");
    loadPatients();
  } catch (error) {
    errorBox.innerHTML = `<div class="status-message error">${esc(error.message)}</div>`;
  }
}

// ---------------------------------------------------------------------------
// View 5: patient portal (PATIENT role only)
// ---------------------------------------------------------------------------

async function loadPortal() {
  const statusId = statusFor("portal");
  setStatus(statusId, { loading: true });
  try {
    const [patient, encounters, observations] = await Promise.all([
      api("/api/v1/me/patient"),
      api("/api/v1/me/encounters"),
      api("/api/v1/me/observations"),
    ]);
    renderPortal(patient, encounters, observations);
    setStatus(statusId);
  } catch (error) {
    setStatus(statusId, { error: error.message });
    $id("portal-content").innerHTML = "";
  }
}

function renderPortal(patient, encounters, observations) {
  const content = $id("portal-content");

  const encounterRows = (encounters || [])
    .map(
      (encounter) => `
    <tr>
      <td class="mono">${formatDateTime(encounter.started_at)}</td>
      <td>${esc(ENCOUNTER_CLASS_LABELS[encounter.encounter_class] || encounter.encounter_class)}</td>
      <td><span class="badge badge-routine">${esc(PRIORITY_LABELS[encounter.priority] || encounter.priority)}</span></td>
      <td>${esc(ENCOUNTER_STATUS_LABELS[encounter.status] || encounter.status)}</td>
      <td>${esc(encounter.reason_text || "—")}</td>
    </tr>`
    )
    .join("");

  const observationRows = (observations || [])
    .map((observation) => {
      const value = observation.value_numeric !== null && observation.value_numeric !== undefined
        ? `${observation.value_numeric} ${esc(observation.unit || "")}`
        : esc(observation.value_text || "—");
      return `
    <tr>
      <td class="mono">${formatDateTime(observation.observed_at)}</td>
      <td>${esc(observation.display || observation.code)}</td>
      <td class="mono">${value}</td>
      <td>${esc(OBSERVATION_STATUS_LABELS[observation.status] || observation.status)}</td>
    </tr>`;
    })
    .join("");

  content.innerHTML = `
    <div class="portal-grid">
      <div class="card">
        <div class="card-header"><h3>Mi ficha</h3></div>
        <div class="detail-grid">
          <div class="detail-item">
            <div class="detail-label">Documento</div>
            <div class="detail-value mono">${esc(patient.document_type)}-${esc(patient.document_number)}</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Nombre</div>
            <div class="detail-value">${esc(patient.first_name)} ${esc(patient.last_name)}</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Sexo</div>
            <div class="detail-value">${esc(GENDER_LABELS[patient.gender] || patient.gender)}</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Fecha de nacimiento</div>
            <div class="detail-value mono">${formatDate(patient.birth_date)}</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Teléfono</div>
            <div class="detail-value mono">${esc(patient.phone || "—")}</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Correo</div>
            <div class="detail-value">${esc(patient.email || "—")}</div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3>Mis encuentros</h3></div>
        ${
          encounterRows
            ? `<div class="table-wrap">
                 <table>
                   <thead>
                     <tr>
                       <th>Inicio</th>
                       <th>Tipo</th>
                       <th>Prioridad</th>
                       <th>Estado</th>
                       <th>Motivo</th>
                     </tr>
                   </thead>
                   <tbody>${encounterRows}</tbody>
                 </table>
               </div>`
            : '<div class="empty-state">No tenés encuentros registrados.</div>'
        }
      </div>

      <div class="card">
        <div class="card-header"><h3>Mis observaciones</h3></div>
        ${
          observationRows
            ? `<div class="table-wrap">
                 <table>
                   <thead>
                     <tr>
                       <th>Fecha</th>
                       <th>Medición</th>
                       <th>Valor</th>
                       <th>Estado</th>
                     </tr>
                   </thead>
                   <tbody>${observationRows}</tbody>
                 </table>
               </div>`
            : '<div class="empty-state">No tenés observaciones registradas.</div>'
        }
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Modal helpers
// ---------------------------------------------------------------------------

function openModal(title, bodyHtml) {
  $id("modal-title").textContent = title;
  $id("modal-body").innerHTML = bodyHtml;
  $id("modal-overlay").classList.remove("hidden");
}

function closeModal() {
  $id("modal-overlay").classList.add("hidden");
  $id("modal-body").innerHTML = "";
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  $id("login-form").addEventListener("submit", submitLogin);
  $id("logout-btn").addEventListener("click", logout);

  $id("toggle-password").addEventListener("click", () => {
    const input = $id("login-password");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $id("toggle-password").textContent = show ? "Ocultar" : "Mostrar";
  });

  $id("modal-close").addEventListener("click", closeModal);
  $id("modal-overlay").addEventListener("click", (event) => {
    if (event.target === $id("modal-overlay")) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  $id("beds-org-select").addEventListener("change", loadBeds);
  $id("beds-apply-btn").addEventListener("click", loadBeds);
  $id("beds-service-filter").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadBeds();
  });

  $id("new-patient-btn").addEventListener("click", openNewPatientModal);

  await resolveApiBase();
  showLogin();
}

document.addEventListener("DOMContentLoaded", init);
