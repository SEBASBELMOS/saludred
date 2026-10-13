import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { sessionToken } from "../../api/client";
import type { Factura } from "../../api/billing";
import type { Role } from "../../api/types";
import { cop } from "../../components/format";
import { ToastProvider } from "../../components/ui";
import { AuthProvider } from "../auth/AuthProvider";
import { menuFor } from "../auth/permissions";
import { LiveEventsProvider } from "../notifications/LiveEvents";
import { FakeWebSocket } from "../../test/fakeSocket";
import { response, user } from "../../test/fixtures";
import { renderUi } from "../../test/render";
import { BillingPage } from "./BillingPage";

const invoice = (id: string, over: Partial<Factura> = {}): Factura => ({
  id,
  paciente_id: "0b6f2a9e-5a41-4c5e-9d3b-0d7c1c0f8a11",
  eps_id: "eps-1",
  ips_id: "ips-1",
  servicio: "analisis_ia",
  descripcion: `Análisis de IA aprobado ${id}`,
  cantidad: 1,
  valor_unitario: 45000,
  valor: 45000,
  fecha: "2026-10-03T15:31:30Z",
  estado_pago: "pendiente",
  origen: "automatica",
  creado_por: "sistema",
  reporte_id: "c2f0a1d4-7b3e-4a98-9d65-1e8b7a4c2f90",
  historial: [
    {
      estado: "pendiente",
      fecha: "2026-10-03T15:31:30Z",
      usuario: "sistema",
      nota: "Factura creada",
    },
  ],
  ...over,
});
const summary = {
  total_facturado: { cantidad: 2, valor: 1_295_000 },
  por_estado: {
    pendiente: { cantidad: 1, valor: 45_000 },
    pagada: { cantidad: 1, valor: 1_250_000 },
  },
  por_servicio: {
    analisis_ia: { cantidad: 1, valor: 45_000 },
    dia_cama: { cantidad: 1, valor: 1_250_000 },
  },
};

interface Options {
  role?: Role;
  list?: () => Response | Promise<Response>;
  action?: (url: string) => Response | Promise<Response>;
}
function setup({ role = "ACCOUNTANT", list, action }: Options = {}) {
  sessionToken.set("test-session");
  const store = {
    rows: [invoice("a1"), invoice("a2", { estado_pago: "pagada" })],
  };
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith("/auth/me")) return response({ ...user, role });
      if (url.includes("/facturas/resumen")) return response(summary);
      if (url.includes("/notifications")) return response([]);
      if (init?.method === "POST" && action) return action(url);
      if (url.includes("/facturas?"))
        return list ? list() : response(store.rows);
      return response({});
    }),
  );
  renderUi(
    <AuthProvider>
      <ToastProvider>
        <LiveEventsProvider>
          <BillingPage />
        </LiveEventsProvider>
      </ToastProvider>
    </AuthProvider>,
  );
  const lists = () => calls.filter((c) => c.url.includes("/facturas?")).length;
  return { store, calls, lists };
}
const rowFor = async (text: string) =>
  within(await screen.findByText(text).then((cell) => cell.closest("tr")!));

describe("access", () => {
  it("shows Contabilidad to the accountant and the admin, not to clinicians", () => {
    const has = (role: Role) =>
      menuFor(role).some((item) => item.label === "Contabilidad");
    expect(has("ACCOUNTANT")).toBe(true);
    expect(has("ADMIN")).toBe(true);
    for (const role of [
      "IPS_CLINICAL_OPERATOR",
      "SPECIALIST",
      "EPS_COORDINATOR",
      "PATIENT",
    ] as Role[])
      expect(has(role)).toBe(false);
  });
});

describe("currency", () => {
  it("writes pesos as es-CO without decimals", () => {
    expect(cop(1_250_000)).toBe("$ 1.250.000");
    expect(cop(45_000)).toBe("$ 45.000");
    expect(cop(9_500)).toBe("$ 9.500");
    expect(cop(0)).toBe("$ 0");
    expect(cop(1234.6)).toBe("$ 1.235");
  });
});

describe("billing screen", () => {
  it("shows the summary line and the invoices", async () => {
    setup();
    expect(await screen.findByTestId("total-facturado")).toHaveTextContent(
      "$ 1.295.000",
    );
    const row = await rowFor("Análisis de IA aprobado a1");
    expect(row.getByText("$ 45.000")).toBeInTheDocument();
    expect(row.getByText(/Pendiente/)).toBeInTheDocument();
    // The accountant cannot read patients: only a short id is shown.
    expect(row.getByText("Paciente 0b6f2a9e")).toBeInTheDocument();
  });

  it("offers pay and void only on pending invoices", async () => {
    setup();
    const pending = await rowFor("Análisis de IA aprobado a1");
    expect(
      pending.getByRole("button", { name: "Registrar pago" }),
    ).toBeVisible();
    const paid = await rowFor("Análisis de IA aprobado a2");
    expect(paid.queryByRole("button", { name: "Registrar pago" })).toBeNull();
    expect(paid.queryByRole("button", { name: "Anular" })).toBeNull();
  });

  it("registers a payment with an optional note", async () => {
    const { calls } = setup();
    const row = await rowFor("Análisis de IA aprobado a1");
    await userEvent.click(row.getByRole("button", { name: "Registrar pago" }));
    await userEvent.type(
      await screen.findByLabelText("Nota (opcional)"),
      "Transferencia 123",
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Registrar pago" }).at(-1)!,
    );
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/facturas/a1/pagar"))).toBe(
        true,
      ),
    );
    const post = calls.find((c) => c.url.endsWith("/facturas/a1/pagar"))!;
    expect(JSON.parse(String(post.init?.body))).toEqual({
      nota: "Transferencia 123",
    });
  });

  it("requires a reason to void, then asks for confirmation", async () => {
    const { calls } = setup();
    const row = await rowFor("Análisis de IA aprobado a1");
    await userEvent.click(row.getByRole("button", { name: "Anular" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Continuar" }),
    );
    expect(await screen.findByText(/Escribe el motivo/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/anular"))).toBe(false);

    await userEvent.type(screen.getByLabelText(/Motivo/), "Cobro duplicado");
    await userEvent.click(screen.getByRole("button", { name: "Continuar" }));
    expect(calls.some((c) => c.url.endsWith("/anular"))).toBe(false);
    await userEvent.click(
      await screen.findByRole("button", { name: "Sí, anular factura" }),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/facturas/a1/anular"))).toBe(
        true,
      ),
    );
    const post = calls.find((c) => c.url.endsWith("/anular"))!;
    expect(JSON.parse(String(post.init?.body))).toEqual({
      motivo: "Cobro duplicado",
    });
  });

  it("reloads the list when the invoice changed state (409)", async () => {
    const { lists } = setup({
      action: () =>
        response({ detail: "La factura ya no esta pendiente" }, 409),
    });
    const row = await rowFor("Análisis de IA aprobado a1");
    await waitFor(() => expect(lists()).toBe(1));
    await userEvent.click(row.getByRole("button", { name: "Registrar pago" }));
    await userEvent.click(
      screen.getAllByRole("button", { name: "Registrar pago" }).at(-1)!,
    );
    expect(
      await screen.findByText(/cambió de estado mientras la veías/),
    ).toBeInTheDocument();
    await waitFor(() => expect(lists()).toBe(2));
  });

  it("explains when billing is unavailable (503)", async () => {
    setup({
      list: () => response({ detail: "Mongo caído" }, 503),
    });
    expect(
      await screen.findByText(
        "La contabilidad no está disponible en este momento. Las facturas automáticas se crearán al conciliar.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Mongo caído")).toBeNull();
  });

  it("shows the reconciliation counts", async () => {
    setup({
      action: () =>
        response({
          analisis_ia: { revisados: 4, creadas: 1, ya_facturados: 3 },
          dia_cama: { revisados: 2, creadas: 0, ya_facturados: 2 },
          total_creadas: 1,
        }),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: "Conciliar" }),
    );
    expect(await screen.findByText(/1 factura creada/)).toBeInTheDocument();
    expect(
      screen.getByText(/4 revisados, 1 creadas, 3 ya facturados/),
    ).toBeInTheDocument();
  });

  it("asks for the patient id and explains why when the role cannot search", async () => {
    setup();
    await userEvent.click(
      await screen.findByRole("button", { name: "Nueva factura" }),
    );
    expect(
      await screen.findByLabelText("Identificador del paciente"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Tu rol no consulta datos de pacientes/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Crear factura" }),
    ).toBeDisabled();
  });

  it("opens the history of an invoice", async () => {
    setup();
    await userEvent.click(
      await screen.findByRole("button", {
        name: /Ver historial: Análisis de IA aprobado a1/,
      }),
    );
    const panel = await screen.findByRole("dialog", {
      name: "Detalle de la factura",
    });
    expect(within(panel).getByText("Historial")).toBeInTheDocument();
    expect(
      within(panel).getByText(/Sistema: Factura creada/),
    ).toBeInTheDocument();
  });
});

describe("live invoices", () => {
  it("refreshes the list when factura_creada arrives and marks the new row", async () => {
    const { store, lists } = setup();
    await rowFor("Análisis de IA aprobado a1");
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    act(() => FakeWebSocket.last.open());
    const before = lists();
    store.rows = [
      invoice("a3", { fecha: "2026-10-03T16:00:00Z" }),
      ...store.rows,
    ];
    act(() =>
      FakeWebSocket.last.receive({
        id: "n1",
        tipo: "factura_creada",
        mensaje: "Nueva factura automática",
        leida: false,
        fecha: "2026-10-03T16:00:00Z",
        datos: { factura_id: "a3" },
      }),
    );
    const row = await rowFor("Análisis de IA aprobado a3");
    expect(lists()).toBeGreaterThan(before);
    expect(row.getByText("$ 45.000")).toBeInTheDocument();
    expect(row.getAllByRole("button")[0].closest("tr")).toHaveClass(
      "animate-flash",
    );
  });
});
