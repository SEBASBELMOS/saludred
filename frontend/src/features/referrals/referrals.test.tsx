import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { sessionToken } from "../../api/client";
import type { Referral, ReferralState, Specialist } from "../../api/referrals";
import type { AppNotification, Role } from "../../api/types";
import { ToastProvider } from "../../components/ui";
import { AuthProvider } from "../auth/AuthProvider";
import { LiveEventsProvider } from "../notifications/LiveEvents";
import { PatientRecordDialog } from "../patients/PatientRecordDialog";
import { FakeWebSocket } from "../../test/fakeSocket";
import { page, patient, response, user } from "../../test/fixtures";
import { renderUi } from "../../test/render";
import { ReferDialog, staleReferralMessage } from "./ReferralDialogs";
import { ReferralsPage } from "./ReferralsPage";

const base: Referral = {
  id: "ref-1",
  paciente_id: patient.id,
  paciente_nombre: "María Pérez",
  motivo: "Dolor torácico persistente",
  estado: "pendiente",
  remitente_id: "doc-1",
  remitente_nombre: "Dr. Ricardo Torres",
  especialista_id: "spec-1",
  especialista_nombre: "Dra. Laura Gómez",
  especialista_organizacion: "Hospital Sur",
  motivo_rechazo: null,
  informe: null,
  creada_en: "2026-10-03T15:00:00Z",
  aceptada_en: null,
  rechazada_en: null,
  completada_en: null,
};
const withState = (
  id: string,
  estado: ReferralState,
  over: Partial<Referral> = {},
): Referral => ({
  ...base,
  id,
  estado,
  paciente_nombre: `Paciente ${id}`,
  ...over,
});
const specialists: Specialist[] = [
  { id: "spec-1", nombre: "Dra. Laura Gómez", organizacion: "Hospital Sur" },
  { id: "spec-2", nombre: "Dr. Pablo Rojas", organizacion: "Clínica Norte" },
];

interface Server {
  referrals: Referral[];
  posts: { url: string; body: unknown }[];
  // Overrides the answer to a POST: the way to simulate a 409.
  answer?: () => Response | undefined;
}

function stubApi(role: Role, server: Partial<Server> = {}) {
  sessionToken.set("test-session");
  const state: Server = { referrals: [base], posts: [], ...server };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/auth/me")) return response({ ...user, role });
    if (url.endsWith("/referrals/specialists")) return response(specialists);
    if (url.includes("/referrals") && method === "POST") {
      state.posts.push({
        url,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      const forced = state.answer?.();
      if (forced) return forced;
      return response({ ...base, id: "ref-new" }, 201);
    }
    if (url.includes("/referrals")) return response(state.referrals);
    if (url.endsWith("/pendientes"))
      return response({
        reportes_por_revisar: 0,
        remisiones_recibidas: state.referrals.filter(
          (r) => r.estado === "pendiente",
        ).length,
        alertas_activas: 0,
      });
    if (url.includes("/notifications")) return response([]);
    if (url.includes("/encounters")) return response([]);
    if (url.includes("/observations")) return response(page([]));
    if (url.includes("/ai-reports")) return response([]);
    if (url.endsWith(`/patients/${patient.id}`)) return response(patient);
    return response({ items: [] });
  });
  vi.stubGlobal("fetch", fetch);
  return { state, fetch };
}

const renderPage = (route = "/referrals") =>
  renderUi(
    <AuthProvider>
      <ToastProvider>
        <LiveEventsProvider>
          <ReferralsPage />
        </LiveEventsProvider>
      </ToastProvider>
    </AuthProvider>,
    route,
  );
const rowOf = async (name: string) =>
  (await screen.findByText(name)).closest("tr") as HTMLElement;
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

describe("referrals screen, especialista", () => {
  const all = [
    withState("pend", "pendiente"),
    withState("acep", "aceptada", { aceptada_en: "2026-10-03T15:05:00Z" }),
    withState("rech", "rechazada", { motivo_rechazo: "Fuera de mi especialidad" }),
    withState("comp", "completada", { informe: "Sin hallazgos agudos." }),
  ];

  it("offers accept and reject only while pending, and the report only once accepted", async () => {
    stubApi("SPECIALIST", { referrals: all });
    renderPage();
    expect(await screen.findByText("Recibidas")).toBeInTheDocument();

    const pend = within(await rowOf("Paciente pend"));
    expect(pend.getByRole("button", { name: /Aceptar/ })).toBeInTheDocument();
    expect(pend.getByRole("button", { name: /Rechazar/ })).toBeInTheDocument();
    expect(pend.queryByRole("button", { name: /Escribir informe/ })).toBeNull();

    const acep = within(await rowOf("Paciente acep"));
    expect(acep.getByRole("button", { name: /Escribir informe/ })).toBeInTheDocument();
    expect(acep.queryByRole("button", { name: /Aceptar|Rechazar/ })).toBeNull();

    for (const id of ["rech", "comp"]) {
      const row = within(await rowOf(`Paciente ${id}`));
      expect(row.queryByRole("button", { name: /Aceptar|Rechazar|Escribir/ })).toBeNull();
      expect(row.getByRole("button", { name: /Ver paciente/ })).toBeInTheDocument();
    }
    // What the other side wrote is on the row.
    expect(screen.getByText("Fuera de mi especialidad")).toBeInTheDocument();
    expect(screen.getByText("Informe del especialista")).toBeInTheDocument();
  });

  it("accepts with one click", async () => {
    const { state } = stubApi("SPECIALIST");
    renderPage();
    const row = within(await rowOf("María Pérez"));
    await userEvent.click(row.getByRole("button", { name: /Aceptar/ }));
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0].url).toBe("/api/v1/referrals/ref-1/accept");
  });

  it("refreshes and says so when the referral changed under the screen (409)", async () => {
    const { state } = stubApi("SPECIALIST", {
      answer: () => response({ detail: "No se puede aceptar" }, 409),
    });
    renderPage();
    await userEvent.click(
      within(await rowOf("María Pérez")).getByRole("button", { name: /Aceptar/ }),
    );
    expect(await screen.findByText(staleReferralMessage)).toBeInTheDocument();
    expect(state.posts).toHaveLength(1);
  });

  it("requires a motivo to reject", async () => {
    const { state } = stubApi("SPECIALIST");
    renderPage();
    await userEvent.click(
      within(await rowOf("María Pérez")).getByRole("button", { name: /Rechazar/ }),
    );
    // The modal renders in a portal: query the whole document.
    const confirm = await screen.findByRole("button", { name: "Rechazar remisión" });
    const dialog = document.body;
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Motivo del rechazo/), "   ");
    expect(confirm).toBeDisabled();
    await userEvent.type(
      within(dialog).getByLabelText(/Motivo del rechazo/),
      "Fuera de mi especialidad",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0].url).toBe("/api/v1/referrals/ref-1/reject");
    expect(state.posts[0].body).toEqual({ motivo: "Fuera de mi especialidad" });
  });

  it("requires an informe to complete an accepted referral", async () => {
    const { state } = stubApi("SPECIALIST", {
      referrals: [withState("acep", "aceptada")],
    });
    renderPage();
    await userEvent.click(
      within(await rowOf("Paciente acep")).getByRole("button", {
        name: /Escribir informe/,
      }),
    );
    const send = await screen.findByRole("button", { name: "Enviar informe" });
    const dialog = document.body;
    expect(send).toBeDisabled();
    await userEvent.type(
      within(dialog).getByLabelText(/Informe del especialista/),
      "Ecocardiograma sin hallazgos.",
    );
    await userEvent.click(send);
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0].url).toBe("/api/v1/referrals/acep/report");
    expect(state.posts[0].body).toEqual({ informe: "Ecocardiograma sin hallazgos." });
  });

  it("shows a new referral live, with its counter, and no reload", async () => {
    const { state } = stubApi("SPECIALIST", { referrals: [] });
    renderPage();
    expect(
      await screen.findByText("Cuando un médico te remita un paciente, aparece aquí al instante."),
    ).toBeInTheDocument();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    FakeWebSocket.last.open();

    state.referrals = [base];
    live({
      tipo: "remision_creada",
      mensaje: "Nueva remisión de Dr. Ricardo Torres: María Pérez",
      paciente_id: patient.id,
      datos: { remision_id: base.id, estado: "pendiente" },
    });
    expect(await rowOf("María Pérez")).toBeInTheDocument();
    expect(
      await screen.findByText("Nueva remisión de Dr. Ricardo Torres: María Pérez"),
    ).toBeInTheDocument();
    expect(await screen.findByText("1 por responder")).toBeInTheDocument();
  });

  it("opens the patient record from the referral, even for a patient of another IPS", async () => {
    stubApi("SPECIALIST");
    renderPage();
    await userEvent.click(
      within(await rowOf("María Pérez")).getByRole("button", { name: /Ver paciente/ }),
    );
    expect(
      await screen.findByRole("dialog", { name: "Ficha del paciente" }),
    ).toBeInTheDocument();
  });
});

describe("referrals screen, medico and admin", () => {
  it("shows the medico what they sent, with live state changes and no answer buttons", async () => {
    const { state } = stubApi("IPS_CLINICAL_OPERATOR");
    renderPage();
    expect(await screen.findByText("Enviadas")).toBeInTheDocument();
    const row = within(await rowOf("María Pérez"));
    expect(row.getByText("Pendiente")).toBeInTheDocument();
    expect(row.getByText("Dra. Laura Gómez")).toBeInTheDocument();
    expect(row.getByText("Hospital Sur")).toBeInTheDocument();
    expect(row.queryByRole("button", { name: /Aceptar|Rechazar|Escribir/ })).toBeNull();

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    FakeWebSocket.last.open();
    state.referrals = [{ ...base, estado: "aceptada", aceptada_en: "2026-10-03T15:05:00Z" }];
    live({
      tipo: "remision_aceptada",
      mensaje: "Dra. Laura Gómez aceptó la remisión de María Pérez",
      paciente_id: patient.id,
      datos: { remision_id: base.id, estado: "aceptada" },
    });
    await waitFor(async () =>
      expect(within(await rowOf("María Pérez")).getByText("Aceptada")).toBeInTheDocument(),
    );
    expect(
      await screen.findByText("Dra. Laura Gómez aceptó la remisión de María Pérez"),
    ).toBeInTheDocument();
  });

  it("shows the admin every referral, with who sent it, and no answer buttons", async () => {
    stubApi("ADMIN");
    renderPage();
    expect(await screen.findByText("Todas las remisiones")).toBeInTheDocument();
    const row = within(await rowOf("María Pérez"));
    expect(row.getByText("De Dr. Ricardo Torres")).toBeInTheDocument();
    expect(row.queryByRole("button", { name: /Aceptar|Rechazar|Escribir/ })).toBeNull();
  });
});

describe("refer a patient (medico)", () => {
  it("requires a specialist and a motivo, then posts the referral", async () => {
    const { state } = stubApi("IPS_CLINICAL_OPERATOR");
    const onClose = vi.fn();
    renderUi(
      <AuthProvider>
        <ToastProvider>
          <ReferDialog patient={patient} onClose={onClose} />
        </ToastProvider>
      </AuthProvider>,
    );
    const send = await screen.findByRole("button", { name: "Enviar remisión" });
    const dialog = document.body;
    expect(send).toBeDisabled();

    // The picker shows name and IPS.
    const picker = within(dialog).getByLabelText("Especialista");
    await waitFor(() => expect(picker).toBeEnabled());
    expect(
      within(dialog).getByRole("option", { name: "Dra. Laura Gómez, Hospital Sur" }),
    ).toBeInTheDocument();
    await userEvent.selectOptions(picker, "spec-1");
    expect(send).toBeDisabled();

    await userEvent.type(within(dialog).getByLabelText(/Motivo de la remisión/), "   ");
    expect(send).toBeDisabled();
    await userEvent.type(
      within(dialog).getByLabelText(/Motivo de la remisión/),
      "Valoración cardiológica",
    );
    expect(send).toBeEnabled();
    await userEvent.click(send);
    await waitFor(() => expect(state.posts).toHaveLength(1));
    expect(state.posts[0].url).toBe("/api/v1/referrals");
    expect(state.posts[0].body).toEqual({
      paciente_id: patient.id,
      especialista_id: "spec-1",
      motivo: "Valoración cardiológica",
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

describe("patient record drawer", () => {
  const renderRecord = () =>
    renderUi(
      <AuthProvider>
        <ToastProvider>
          <PatientRecordDialog patientId={patient.id} onClose={() => {}} />
        </ToastProvider>
      </AuthProvider>,
    );

  it("offers Remitir a especialista to the medico", async () => {
    stubApi("IPS_CLINICAL_OPERATOR");
    renderRecord();
    expect(
      await screen.findByRole("button", { name: "Remitir a especialista" }),
    ).toBeInTheDocument();
  });

  it.each(["SPECIALIST", "ADMIN", "EPS_COORDINATOR"] as Role[])(
    "does not offer Remitir a especialista to %s",
    async (role) => {
      stubApi(role);
      renderRecord();
      await screen.findByText("Médico responsable");
      expect(
        screen.queryByRole("button", { name: "Remitir a especialista" }),
      ).toBeNull();
    },
  );

  it("lists this patient's referrals with state, both names, reasons and the informe", async () => {
    stubApi("IPS_CLINICAL_OPERATOR", {
      referrals: [
        withState("a", "rechazada", {
          paciente_nombre: "María Pérez",
          motivo_rechazo: "Fuera de mi especialidad",
        }),
        withState("b", "completada", {
          paciente_nombre: "María Pérez",
          especialista_nombre: "Dr. Pablo Rojas",
          especialista_organizacion: "Clínica Norte",
          informe: "Sin hallazgos agudos.",
        }),
        // Another patient: it must not show here.
        withState("c", "pendiente", { paciente_id: "other", especialista_nombre: "Dra. Otra" }),
      ],
    });
    renderRecord();
    const panel = (await screen.findByRole("heading", { name: "Remisiones" })).closest(
      "section",
    ) as HTMLElement;
    expect(await within(panel).findByText("Rechazada")).toBeInTheDocument();
    expect(within(panel).getByText("Completada")).toBeInTheDocument();
    expect(within(panel).getByText("Fuera de mi especialidad")).toBeInTheDocument();
    expect(within(panel).getByText("Sin hallazgos agudos.")).toBeInTheDocument();
    expect(within(panel).getByText("Dr. Pablo Rojas")).toBeInTheDocument();
    expect(within(panel).queryByText("Dra. Otra")).toBeNull();
  });

  it("has no referrals panel for roles that cannot read referrals", async () => {
    const { fetch } = stubApi("EPS_COORDINATOR");
    renderRecord();
    await screen.findByText("Médico responsable");
    expect(screen.queryByRole("heading", { name: "Remisiones" })).toBeNull();
    expect(fetch.mock.calls.some(([url]) => String(url).includes("/referrals"))).toBe(false);
  });
});
