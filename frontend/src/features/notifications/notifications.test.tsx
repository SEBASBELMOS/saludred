import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { sessionToken } from "../../api/client";
import type { AppNotification, Role } from "../../api/types";
import { ToastProvider } from "../../components/ui";
import { AuthProvider } from "../auth/AuthProvider";
import { Measurements } from "../patients/PatientRecordView";
import { ResponsibleClinician } from "../patients/ResponsibleClinician";
import { renderUi } from "../../test/render";
import { FakeWebSocket } from "../../test/fakeSocket";
import { patient, response, user } from "../../test/fixtures";
import { LiveEventsProvider } from "./LiveEvents";
import { LiveIndicator, NotificationBell } from "./NotificationBell";
import {
  POLL_MS,
  RETRIES_BEFORE_POLLING,
  liveUrl,
  useLiveEvents,
} from "./useLiveEvents";

const note = (
  id: string,
  over: Partial<AppNotification> = {},
): AppNotification => ({
  id,
  tipo: "fhir_observacion",
  mensaje: `Nuevo resultado de laboratorio desde FHIR: Glucosa ${id}`,
  leida: false,
  fecha: "2026-10-03T15:31:30Z",
  paciente_id: "patient-1",
  datos: null,
  ...over,
});

// A tiny API: the inbox lives in `store.list`, reading marks it as read.
function mockApi(role: Role, list: AppNotification[]) {
  const store = { list, reads: [] as string[] };
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/auth/me")) return response({ ...user, role });
    const read = url.match(/\/notifications\/([^/]+)\/read$/);
    if (read && init?.method === "POST") {
      store.reads.push(read[1]);
      store.list = store.list.map((n) =>
        n.id === read[1] ? { ...n, leida: true } : n,
      );
      return response(store.list.find((n) => n.id === read[1]));
    }
    if (url.includes("/notifications?leida=false"))
      return response(store.list.filter((n) => !n.leida));
    if (url.includes("/notifications")) return response(store.list);
    return response({});
  });
  vi.stubGlobal("fetch", fetch);
  return { store, fetch };
}

function renderChrome() {
  sessionToken.set("test-session");
  return renderUi(
    <AuthProvider>
      <ToastProvider>
        <LiveEventsProvider>
          <LiveIndicator />
          <NotificationBell />
        </LiveEventsProvider>
      </ToastProvider>
    </AuthProvider>,
  );
}
const count = () => screen.queryByTestId("unread-count");

describe("notification bell and inbox", () => {
  it("shows the unread count and no badge when everything is read", async () => {
    mockApi("IPS_CLINICAL_OPERATOR", [
      note("a"),
      note("b"),
      note("c", { leida: true }),
    ]);
    renderChrome();
    expect(await screen.findByTestId("unread-count")).toHaveTextContent("2");
    expect(
      screen.getByRole("button", { name: "Notificaciones, 2 sin leer" }),
    ).toBeInTheDocument();
  });

  it("shows the empty state for every role, including patient and accountant", async () => {
    for (const role of ["PATIENT", "ACCOUNTANT"] as Role[]) {
      mockApi(role, []);
      const view = renderChrome();
      await userEvent.click(
        await screen.findByRole("button", { name: /Notificaciones/ }),
      );
      expect(
        await screen.findByText("No tienes notificaciones."),
      ).toBeInTheDocument();
      expect(count()).toBeNull();
      view.unmount();
    }
  });

  it("marks one notification as read and lowers the count", async () => {
    const { store } = mockApi("IPS_CLINICAL_OPERATOR", [note("a"), note("b")]);
    renderChrome();
    await userEvent.click(
      await screen.findByRole("button", { name: /Notificaciones, 2/ }),
    );
    const panel = await screen.findByRole("dialog", {
      name: "Bandeja de notificaciones",
    });
    await userEvent.click(
      within(panel).getAllByRole("button", { name: "Marcar como leída" })[0],
    );
    await waitFor(() => expect(count()).toHaveTextContent("1"));
    expect(store.reads).toEqual(["a"]);
  });

  it("marks all as read with one request per unread notification", async () => {
    const { store } = mockApi("IPS_CLINICAL_OPERATOR", [
      note("a"),
      note("b"),
      note("c", { leida: true }),
    ]);
    renderChrome();
    await userEvent.click(
      await screen.findByRole("button", { name: /Notificaciones, 2/ }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "Marcar todas como leídas" }),
    );
    await waitFor(() => expect(count()).toBeNull());
    expect(store.reads).toEqual(["a", "b"]);
  });

  it("only offers the patient link to roles that have the bed board", async () => {
    mockApi("ACCOUNTANT", [note("a", { tipo: "factura", paciente_id: null })]);
    renderChrome();
    await userEvent.click(
      await screen.findByRole("button", { name: /Notificaciones/ }),
    );
    expect(await screen.findByText(/Nuevo resultado/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ver paciente" })).toBeNull();
  });
});

describe("live events", () => {
  it("connects to the same-origin socket with wss behind https", () => {
    expect(
      liveUrl("a b", { protocol: "https:", host: "x.test" } as Location),
    ).toBe("wss://x.test/api/v1/events/ws?token=a%20b");
    expect(
      liveUrl("t", { protocol: "http:", host: "lan:8080" } as Location),
    ).toBe("ws://lan:8080/api/v1/events/ws?token=t");
  });

  it("updates the count and shows a toast when an event arrives, with no reload", async () => {
    const { store } = mockApi("IPS_CLINICAL_OPERATOR", [note("a")]);
    renderChrome();
    expect(await screen.findByTestId("unread-count")).toHaveTextContent("1");
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(FakeWebSocket.last.url).toContain(
      "/api/v1/events/ws?token=test-session",
    );
    act(() => FakeWebSocket.last.open());
    expect(await screen.findByText("En vivo")).toBeInTheDocument();

    const incoming = note("z", {
      mensaje: "Nuevo resultado: Glucosa 182 mg/dL",
    });
    store.list = [incoming, ...store.list];
    act(() => FakeWebSocket.last.receive(incoming));

    expect(
      await screen.findByText("Nuevo resultado: Glucosa 182 mg/dL"),
    ).toBeInTheDocument();
    expect(screen.getByText("Nueva notificación")).toBeInTheDocument();
    await waitFor(() => expect(count()).toHaveTextContent("2"));

    await userEvent.click(
      screen.getByRole("button", { name: /Notificaciones, 2/ }),
    );
    const panel = await screen.findByRole("dialog", {
      name: "Bandeja de notificaciones",
    });
    expect(
      within(panel).getByText("Nuevo resultado: Glucosa 182 mg/dL"),
    ).toBeInTheDocument();
  });

  it("ignores the heartbeat", async () => {
    mockApi("IPS_CLINICAL_OPERATOR", [note("a")]);
    renderChrome();
    await screen.findByTestId("unread-count");
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.receive({ tipo: "ping" }));
    expect(screen.queryByText("Nueva notificación")).toBeNull();
    expect(count()).toHaveTextContent("1");
  });

  it("refreshes the open patient record when the event names that patient", async () => {
    mockApi("IPS_CLINICAL_OPERATOR", []);
    const view = renderChrome();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    view.client.setQueryData(["patient-record", "patient-1"], { stale: true });
    view.client.setQueryData(["patient-record", "other"], { stale: true });
    act(() =>
      FakeWebSocket.last.receive(note("q", { paciente_id: "patient-1" })),
    );
    await waitFor(() =>
      expect(
        view.client.getQueryState(["patient-record", "patient-1"])
          ?.isInvalidated,
      ).toBe(true),
    );
    expect(
      view.client.getQueryState(["patient-record", "other"])?.isInvalidated,
    ).toBe(false);
  });
});

describe("connection state: reconnect and polling fallback", () => {
  afterEach(() => vi.useRealTimers());

  const setup = () => {
    vi.useFakeTimers();
    sessionToken.set("t");
    const handlers = { onEvent: vi.fn(), onSync: vi.fn(), onPoll: vi.fn() };
    const hook = renderHook(() =>
      useLiveEvents({ enabled: true, ...handlers }),
    );
    return { ...hook, handlers };
  };

  it("goes live, then reconnects with backoff after a drop and resyncs", () => {
    const { result, handlers } = setup();
    expect(result.current).toBe("connecting");
    act(() => FakeWebSocket.last.open());
    expect(result.current).toBe("live");
    expect(handlers.onSync).toHaveBeenCalledTimes(1);

    act(() => FakeWebSocket.last.drop());
    expect(result.current).toBe("reconnecting");
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(1000));
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => FakeWebSocket.last.open());
    expect(result.current).toBe("live");
    expect(handlers.onSync).toHaveBeenCalledTimes(2);
    expect(handlers.onPoll).not.toHaveBeenCalled();
  });

  it("falls back to polling every 20 s after a few failed retries, and recovers", () => {
    const { result, handlers } = setup();
    for (let i = 0; i < RETRIES_BEFORE_POLLING; i++) {
      act(() => FakeWebSocket.last.drop());
      act(() => void vi.advanceTimersByTime(20_000));
    }
    expect(result.current).toBe("polling");
    expect(handlers.onPoll).toHaveBeenCalled();
    const before = handlers.onPoll.mock.calls.length;
    act(() => void vi.advanceTimersByTime(POLL_MS));
    expect(handlers.onPoll.mock.calls.length).toBe(before + 1);

    // The socket keeps being retried in the background: when it opens, polling stops.
    act(() => FakeWebSocket.last.open());
    expect(result.current).toBe("live");
    const after = handlers.onPoll.mock.calls.length;
    act(() => void vi.advanceTimersByTime(POLL_MS * 2));
    expect(handlers.onPoll.mock.calls.length).toBe(after);
  });

  it("stops retrying a refused token and closes the socket on unmount", () => {
    const { result, handlers, unmount } = setup();
    act(() => FakeWebSocket.last.drop(4401));
    expect(result.current).toBe("polling");
    expect(handlers.onPoll).toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(60_000));
    expect(FakeWebSocket.instances).toHaveLength(1);

    const second = renderHook(() =>
      useLiveEvents({ enabled: true, ...handlers }),
    );
    const socket = FakeWebSocket.last;
    second.unmount();
    expect(socket.closed).toBe(true);
    unmount();
  });

  it("tells the user when the live channel is not available", async () => {
    mockApi("IPS_CLINICAL_OPERATOR", []);
    renderChrome();
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    act(() => FakeWebSocket.last.drop(4401));
    expect(
      await screen.findByText("Sin canal en vivo: se revisa cada 20 s"),
    ).toBeInTheDocument();
  });
});

describe("responsible clinician", () => {
  const withRole = (role: Role, patientOver = {}, eligible: unknown[] = []) => {
    sessionToken.set("test-session");
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url.endsWith("/auth/me")) return response({ ...user, role });
        if (url.includes("/eligible-clinicians")) return response(eligible);
        if (init?.method === "PUT") return response({ ...patient });
        return response({});
      }),
    );
    renderUi(
      <AuthProvider>
        <ToastProvider>
          <dl>
            <ResponsibleClinician
              patient={{
                ...patient,
                responsible_clinician_id: "doc-0",
                responsible_clinician_name: "Dr. Luis Mora",
                ...patientOver,
              }}
            />
          </dl>
        </ToastProvider>
      </AuthProvider>,
    );
    return calls;
  };
  const colleagues = [
    {
      id: "doc-1",
      nombre: "Dra. Ana Ruiz",
      rol: "especialista",
      organizacion: "Clínica Norte",
    },
  ];

  it.each(["ADMIN", "IPS_CLINICAL_OPERATOR", "SPECIALIST"] as Role[])(
    "lets %s change it from the eligible clinicians list",
    async (role) => {
      const calls = withRole(role, {}, colleagues);
      expect(await screen.findByText("Dr. Luis Mora")).toBeInTheDocument();
      await userEvent.click(
        await screen.findByRole("button", { name: "Cambiar" }),
      );
      const select = await screen.findByLabelText("Nuevo médico responsable");
      await waitFor(() => expect(select).toBeEnabled());
      expect(
        screen.getByRole("option", { name: /Dra. Ana Ruiz.*Clínica Norte/ }),
      ).toBeInTheDocument();
      await userEvent.selectOptions(select, "doc-1");
      await userEvent.click(screen.getByRole("button", { name: "Guardar" }));
      await waitFor(() =>
        expect(calls.some((c) => c.init?.method === "PUT")).toBe(true),
      );
      expect(
        calls.some((c) =>
          c.url.endsWith("/patients/patient-1/eligible-clinicians"),
        ),
      ).toBe(true);
      expect(calls.some((c) => c.url.includes("/admin/users"))).toBe(false);
      const put = calls.find((c) => c.init?.method === "PUT")!;
      expect(put.url).toBe("/api/v1/patients/patient-1/responsible-clinician");
      expect(JSON.parse(String(put.init?.body))).toEqual({
        clinician_id: "doc-1",
      });
    },
  );

  it("says so when the IPS has no other clinician to offer", async () => {
    withRole("IPS_CLINICAL_OPERATOR", {}, []);
    await userEvent.click(
      await screen.findByRole("button", { name: "Cambiar" }),
    );
    expect(
      await screen.findByText(/No hay otro médico o especialista activo/),
    ).toBeInTheDocument();
  });

  it("is read-only for the coordinator", async () => {
    const calls = withRole("EPS_COORDINATOR");
    expect(await screen.findByText("Dr. Luis Mora")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cambiar" })).toBeNull();
    expect(calls.some((c) => c.url.includes("eligible-clinicians"))).toBe(
      false,
    );
  });

  it("says when nobody is assigned", async () => {
    withRole("EPS_COORDINATOR", {
      responsible_clinician_id: null,
      responsible_clinician_name: null,
    });
    expect(await screen.findByText("Sin asignar")).toBeInTheDocument();
  });
});

describe("observations from FHIR", () => {
  it("labels only the measurements that came from FHIR", () => {
    const base = {
      patient_id: "patient-1",
      encounter_id: "e1",
      status: "final" as const,
      code: "2339-0",
      display: "Glucosa",
      value_numeric: 182,
      unit: "mg/dL",
      observed_at: "2026-10-03T15:00:00Z",
      id: "o1",
      created_at: "",
      updated_at: "",
    };
    const { rerender } = renderUi(
      <Measurements observations={[{ ...base, origin: "fhir" }]} />,
    );
    expect(screen.getByText("Desde FHIR")).toBeInTheDocument();
    rerender(<Measurements observations={[{ ...base, origin: "local" }]} />);
    expect(screen.queryByText("Desde FHIR")).toBeNull();
  });
});
