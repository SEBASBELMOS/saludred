import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Alert, Pending } from "../../api/alerts";
import { sessionToken } from "../../api/client";
import type { AppNotification, Role } from "../../api/types";
import { RoleMenu } from "../../components/AppLayout";
import { ToastProvider } from "../../components/ui";
import { AuthProvider } from "../auth/AuthProvider";
import { LiveEventsProvider } from "../notifications/LiveEvents";
import { destinationFor } from "../notifications/NotificationBell";
import { FakeWebSocket } from "../../test/fakeSocket";
import { patient, response, user } from "../../test/fixtures";
import { renderUi } from "../../test/render";
import { staleAlertMessage } from "./AcknowledgeForm";
import { CriticalAlertBanner } from "./CriticalAlertBanner";

const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();
const critical: Alert = {
  id: "alert-1",
  paciente_id: patient.id,
  nivel: "critico",
  estado: "activa",
  kind: "paciente_critico",
  mensaje:
    "Alerta crítica: María Pérez fue clasificada como crítica por el modelo (probabilidad 99%).",
  created_at: minutesAgo(3),
};
const zero: Pending = {
  reportes_por_revisar: 0,
  remisiones_recibidas: 0,
  alertas_activas: 0,
};

interface Server {
  alerts: Alert[];
  pending: Pending;
  acknowledge?: () => Response;
  posts: { url: string; body: unknown }[];
}

// A tiny API: the alert list lives in `server.alerts`, acknowledging removes from it.
function stubApi(role: Role, server: Partial<Server> = {}) {
  sessionToken.set("test-session");
  const state: Server = {
    alerts: [critical],
    pending: zero,
    posts: [],
    ...server,
  };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/auth/me")) return response({ ...user, role });
    const ack = url.match(/\/alerts\/([^/]+)\/acknowledge$/);
    if (ack && method === "POST") {
      state.posts.push({ url, body: JSON.parse(String(init?.body)) });
      const answer = state.acknowledge?.();
      if (answer) return answer;
      state.alerts = state.alerts.filter((alert) => alert.id !== ack[1]);
      return response({ ...critical, estado: "reconocida" });
    }
    if (url.includes("/alerts?")) return response(state.alerts);
    if (url.endsWith("/pendientes")) return response(state.pending);
    if (url.includes("/patients/")) return response(patient);
    if (url.includes("/notifications")) return response([]);
    return response({}, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return { state, fetch };
}

const renderBanner = () =>
  renderUi(
    <AuthProvider>
      <ToastProvider>
        <LiveEventsProvider>
          <CriticalAlertBanner />
        </LiveEventsProvider>
      </ToastProvider>
    </AuthProvider>,
  );
const banner = () =>
  screen.findByRole("region", { name: "Alertas críticas activas" });
const calledAlerts = (fetch: ReturnType<typeof stubApi>["fetch"]) =>
  fetch.mock.calls.some(([url]) => String(url).includes("/alerts"));
const open = () => FakeWebSocket.last.open();
const live = (event: Partial<AppNotification>) =>
  act(() =>
    FakeWebSocket.last.receive({
      id: "n-1",
      mensaje: "",
      leida: false,
      fecha: "2026-10-03T15:31:30Z",
      ...event,
    }),
  );

describe("critical alert banner", () => {
  it.each(["IPS_CLINICAL_OPERATOR", "SPECIALIST"] as Role[])(
    "shows %s the active alert with patient, level, message and time",
    async (role) => {
      stubApi(role);
      renderBanner();
      const region = await banner();
      expect(await within(region).findByText("María Pérez")).toBeInTheDocument();
      expect(within(region).getByText("Crítico")).toBeInTheDocument();
      expect(within(region).getByText(/clasificada como crítica/)).toBeInTheDocument();
      expect(within(region).getByText("Hace 3 min")).toBeInTheDocument();
      expect(
        within(region).getByText("1 alerta crítica sin reconocer"),
      ).toBeInTheDocument();
    },
  );

  it.each(["PATIENT", "ACCOUNTANT", "ADMIN", "EPS_COORDINATOR"] as Role[])(
    "never shows %s the banner and does not even ask for the alerts",
    async (role) => {
      const { fetch } = stubApi(role);
      renderBanner();
      await waitFor(() => expect(fetch).toHaveBeenCalled());
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(
        screen.queryByRole("region", { name: "Alertas críticas activas" }),
      ).toBeNull();
      expect(calledAlerts(fetch)).toBe(false);
    },
  );

  it("does not show the bed alerts: they belong to the command center", async () => {
    const { fetch } = stubApi("IPS_CLINICAL_OPERATOR", {
      alerts: [
        { ...critical, id: "bed-alert", kind: "cama_sin_limpiar", paciente_id: null },
      ],
    });
    renderBanner();
    await waitFor(() => expect(calledAlerts(fetch)).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      screen.queryByRole("region", { name: "Alertas críticas activas" }),
    ).toBeNull();
  });

  it("says an alert was escalated and when", async () => {
    stubApi("IPS_CLINICAL_OPERATOR", {
      alerts: [{ ...critical, escalated_at: minutesAgo(1) }],
    });
    renderBanner();
    const region = await banner();
    expect(within(region).getByText("Escalada")).toBeInTheDocument();
  });

  it("cannot be dismissed: it has no close control and cancelling keeps the alert", async () => {
    stubApi("IPS_CLINICAL_OPERATOR");
    renderBanner();
    const region = await banner();
    await within(region).findByText("María Pérez");
    const names = within(region)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label") ?? button.textContent);
    expect(names).toEqual([
      "Ver paciente María Pérez",
      "Reconocer la alerta de María Pérez",
    ]);
    expect(within(region).queryByRole("button", { name: /cerrar|descartar|ocultar/i })).toBeNull();

    await userEvent.click(
      within(region).getByRole("button", { name: /Reconocer la alerta/ }),
    );
    await userEvent.click(within(region).getByRole("button", { name: "Cancelar" }));
    expect(within(region).getByText("María Pérez")).toBeInTheDocument();
  });

  it("requires a note: blank or spaces never enable the confirmation", async () => {
    const { state } = stubApi("IPS_CLINICAL_OPERATOR");
    renderBanner();
    const region = await banner();
    await userEvent.click(
      within(region).getByRole("button", { name: /Reconocer la alerta/ }),
    );
    const confirm = within(region).getByRole("button", {
      name: "Confirmar reconocimiento",
    });
    expect(confirm).toBeDisabled();
    const note = within(region).getByLabelText(/Nota de reconocimiento/);
    await userEvent.type(note, "   ");
    expect(confirm).toBeDisabled();
    expect(state.posts).toHaveLength(0);

    await userEvent.type(note, "Saturación en 86, inicio oxígeno");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0].url).toBe("/api/v1/alerts/alert-1/acknowledge");
    expect(state.posts[0].body).toEqual({
      nota: "Saturación en 86, inicio oxígeno",
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Alertas críticas activas" }),
      ).toBeNull(),
    );
  });

  it("refreshes the list when somebody else acknowledged it first (409)", async () => {
    const { state } = stubApi("IPS_CLINICAL_OPERATOR", {
      acknowledge: () => {
        // The colleague got there first: the server no longer lists it.
        state.alerts = [];
        return response({ detail: "La alerta ya fue reconocida" }, 409);
      },
    });
    renderBanner();
    const region = await banner();
    await userEvent.click(
      within(region).getByRole("button", { name: /Reconocer la alerta/ }),
    );
    await userEvent.type(
      within(region).getByLabelText(/Nota de reconocimiento/),
      "Lo atiendo yo",
    );
    await userEvent.click(
      within(region).getByRole("button", { name: "Confirmar reconocimiento" }),
    );
    expect(await screen.findByText(staleAlertMessage)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Alertas críticas activas" }),
      ).toBeNull(),
    );
  });

  it("keeps the form open and explains a 422", async () => {
    stubApi("IPS_CLINICAL_OPERATOR", {
      acknowledge: () => response({ detail: [{ loc: ["body", "nota"], msg: "x" }] }, 422),
    });
    renderBanner();
    const region = await banner();
    await userEvent.click(
      within(region).getByRole("button", { name: /Reconocer la alerta/ }),
    );
    await userEvent.type(
      within(region).getByLabelText(/Nota de reconocimiento/),
      "ok",
    );
    await userEvent.click(
      within(region).getByRole("button", { name: "Confirmar reconocimiento" }),
    );
    expect(
      await within(region).findByText(/La nota es obligatoria/),
    ).toBeInTheDocument();
    expect(within(region).getByLabelText(/Nota de reconocimiento/)).toBeInTheDocument();
  });

  it("appears live on alerta_critica and clears live on alerta_reconocida", async () => {
    const { state } = stubApi("IPS_CLINICAL_OPERATOR", { alerts: [] });
    renderBanner();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    open();
    expect(
      screen.queryByRole("region", { name: "Alertas críticas activas" }),
    ).toBeNull();

    // The server creates the alert, then tells this window.
    state.alerts = [critical];
    live({
      tipo: "alerta_critica",
      mensaje: critical.mensaje,
      paciente_id: patient.id,
      datos: { alerta_id: critical.id, nivel: "critico" },
    });
    const region = await banner();
    expect(await within(region).findByText("María Pérez")).toBeInTheDocument();
    // No toast on top of the banner: it would cover the "Reconocer" button.
    expect(screen.queryByText("Revisa este aviso")).toBeNull();

    // A colleague in another window acknowledges it: live only, no inbox row.
    state.alerts = [];
    live({
      id: undefined,
      tipo: "alerta_reconocida",
      mensaje: "Alerta reconocida por Dr. Ricardo Torres",
      paciente_id: patient.id,
      datos: { alerta_id: critical.id, estado: "reconocida" },
    });
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: "Alertas críticas activas" }),
      ).toBeNull(),
    );
  });
});

describe("pending counters (R18)", () => {
  const renderMenu = (role: Role) =>
    renderUi(
      <ToastProvider>
        <LiveEventsProvider>
          <RoleMenu role={role} />
        </LiveEventsProvider>
      </ToastProvider>,
    );

  it("reads GET /pendientes and repaints from pendientes_actualizados, without asking again", async () => {
    const { fetch, state } = stubApi("SPECIALIST", {
      pending: { reportes_por_revisar: 2, remisiones_recibidas: 1, alertas_activas: 0 },
    });
    renderMenu("SPECIALIST");
    await waitFor(() =>
      expect(screen.getByRole("link", { name: /Pacientes/ })).toHaveTextContent(
        "2 reportes de IA por revisar",
      ),
    );
    expect(screen.getByRole("link", { name: /Remisiones/ })).toHaveTextContent(
      "1 remisión por responder",
    );

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    open();
    // Opening the socket catches up once; let that read finish before measuring.
    await new Promise((resolve) => setTimeout(resolve, 50));
    const before = fetch.mock.calls.filter(([url]) =>
      String(url).endsWith("/pendientes"),
    ).length;
    state.pending = {
      reportes_por_revisar: 5,
      remisiones_recibidas: 0,
      alertas_activas: 1,
    };
    live({
      id: undefined,
      tipo: "pendientes_actualizados",
      mensaje: "Trabajo pendiente actualizado",
      datos: {
        reportes_por_revisar: 5,
        remisiones_recibidas: 0,
        alertas_activas: 1,
        motivo: "reporte_generado",
      },
    });
    await waitFor(() =>
      expect(screen.getByRole("link", { name: /Pacientes/ })).toHaveTextContent(
        "5 reportes de IA por revisar",
      ),
    );
    // The referrals badge disappears at zero: quiet, not "0".
    expect(screen.getByRole("link", { name: /Remisiones/ })).not.toHaveTextContent(
      "remisión",
    );
    // The event carried the numbers: nobody asked the API again.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      fetch.mock.calls.filter(([url]) => String(url).endsWith("/pendientes")).length,
    ).toBe(before);
  });

  it("does not put counters on the menu of roles without work to review", async () => {
    const { fetch } = stubApi("ACCOUNTANT");
    renderMenu("ACCOUNTANT");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      fetch.mock.calls.some(([url]) => String(url).endsWith("/pendientes")),
    ).toBe(false);
  });
});

describe("where a notification leads", () => {
  it("sends the admin from cuenta_bloqueada to the accounts screen, filtered", () => {
    expect(destinationFor("cuenta_bloqueada", "ADMIN")).toEqual({
      to: "/accounts?bloqueadas=1",
      label: "Ver cuentas",
    });
    expect(destinationFor("cuenta_bloqueada", "ACCOUNTANT")).toBeNull();
  });

  it("sends referral events to the referrals screen and bed alerts to the command center", () => {
    expect(destinationFor("remision_creada", "SPECIALIST")?.to).toBe("/referrals");
    expect(destinationFor("remision_aceptada", "IPS_CLINICAL_OPERATOR")?.to).toBe(
      "/referrals",
    );
    expect(destinationFor("remision_creada", "ACCOUNTANT")).toBeNull();
    expect(destinationFor("alerta_cama_sin_limpiar", "EPS_COORDINATOR")?.to).toBe(
      "/capacity",
    );
    // The especialista has no command center.
    expect(destinationFor("alerta_cama_sin_limpiar", "SPECIALIST")).toBeNull();
  });
});
