import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { Alert } from "../../api/alerts";
import type { NetworkSnapshot } from "../../api/capacity";
import type { Bed, OrganizationCapacity } from "../../api/types";
import { renderUi } from "../../test/render";
import { bed, request, response } from "../../test/fixtures";
import { CapacityView } from "./CapacityPage";
import { buildAttention, restOfQueue } from "./attention";

const org = (id: string, name: string) =>
  ({
    organization_id: id,
    organization_code: `IPS-${id}`,
    organization_name: name,
  }) as OrganizationCapacity;
const snapshot = (beds: Bed[]): NetworkSnapshot => ({
  network: {
    total: beds.length,
    available: 0,
    occupied: 0,
    occupancy_rate: 0,
    pending_requests: 0,
    organizations: [
      org("ips-1", "Clínica Norte"),
      org("ips-2", "Hospital Sur"),
    ],
  },
  beds,
});
const free = (id: string, organization_id: string): Bed => ({
  ...bed,
  id,
  organization_id,
  status: "AVAILABLE",
});
const emergency = {
  ...request,
  id: "request-9",
  priority: "EMERGENCY" as const,
  waiting_minutes: 12,
};
const overdue = {
  location_id: "bed-7",
  code: "N-UCI-04",
  name: "Cama UCI-04",
  service: "UCI",
  organization_id: "ips-1",
  cleaning_since: "2026-09-29T12:00:00Z",
  minutes_in_cleaning: 95,
  pending_review_records: 1,
};
it("groups what needs action by kind and sorts each group by age", () => {
  const beds = [
    free("a", "ips-1"),
    { ...free("b", "ips-2"), status: "OCCUPIED" as const },
  ];
  const older = { ...emergency, id: "request-8", waiting_minutes: 40 };
  const attention = buildAttention(
    snapshot(beds),
    [request, emergency, older],
    [overdue, { ...overdue, location_id: "bed-8", minutes_in_cleaning: 130 }],
    true,
  );
  expect(attention.waiting.map((item) => item.key)).toEqual([
    "request-request-8",
    "request-request-9",
  ]);
  expect(attention.waiting[0].to).toBe("/queue?assign=request-8");
  expect(attention.cleaning.map((item) => item.minutes)).toEqual([130, 95]);
  expect(attention.cleaning[0].title).toBe("Cama N-UCI-04");
  expect(attention.full.map((item) => item.title)).toEqual(["Hospital Sur"]);
  expect(attention.total).toBe(5);
});
it("does not repeat the listed emergencies in the rest of the queue", () => {
  const attention = buildAttention(
    snapshot([free("a", "ips-1"), free("b", "ips-2")]),
    [request, emergency],
    [],
    true,
  );
  expect(restOfQueue([request, emergency], attention)).toEqual([request]);
});
it("sends roles that cannot assign to the patient record instead", () => {
  const attention = buildAttention(
    snapshot([free("a", "ips-1"), free("b", "ips-2")]),
    [emergency],
    [],
    false,
  );
  expect(attention.waiting).toHaveLength(1);
  expect(attention.waiting[0].action).toBe("Ver ficha");
});
it("renders the three groups with counts and the queue without duplicates", () => {
  renderUi(
    <CapacityView
      snapshot={snapshot([
        free("a", "ips-1"),
        { ...free("b", "ips-2"), status: "OCCUPIED" as const },
      ])}
      queue={[request, emergency]}
      assign
      overdue={[overdue]}
    />,
  );
  const waiting = screen.getByRole("region", { name: "Pacientes esperando" });
  expect(within(waiting).getByText("María Pérez")).toBeInTheDocument();
  expect(
    screen.getByRole("region", { name: "Camas atrasadas en limpieza" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("region", { name: "IPS sin camas libres" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "Resto de la cola" }),
  ).toBeInTheDocument();
});
it("says so when nothing is urgent", () => {
  renderUi(
    <CapacityView
      snapshot={snapshot([free("a", "ips-1"), free("b", "ips-2")])}
      queue={[request]}
      assign
    />,
  );
  expect(screen.getByText("No hay nada urgente ahora")).toBeInTheDocument();
  const tiles = screen.getAllByRole("link", {
    name: /Cama Norte 01, N-01: Disponible/,
  });
  expect(tiles.map((tile) => tile.getAttribute("href"))).toEqual([
    "/beds?bed=a",
    "/beds?bed=b",
  ]);
});

// "Está libre, pero no ha sido limpiada": the active bed alert (R20).
const bedAlert: Alert = {
  id: "alert-bed-1",
  paciente_id: null,
  nivel: "alto",
  estado: "activa",
  kind: "cama_sin_limpiar",
  mensaje:
    "La cama N-UCI-04 (UCI) esta libre, pero no ha sido limpiada: esta cama esta perdiendo tiempo (62 min).",
  created_at: new Date(Date.now() - 2 * 60_000).toISOString(),
  cama_id: "bed-7",
  organizacion_id: "ips-1",
  datos: { cama: "N-UCI-04", minutos_en_limpieza: 62 },
};
it("lists the bed alert in its own group and does not repeat the overdue bed", () => {
  const attention = buildAttention(
    snapshot([free("a", "ips-1"), free("b", "ips-2")]),
    [],
    [overdue, { ...overdue, location_id: "bed-8", code: "N-UCI-05" }],
    true,
    [bedAlert],
  );
  expect(attention.unattended).toHaveLength(1);
  expect(attention.unattended[0]).toMatchObject({
    title: "Cama N-UCI-04",
    alertId: "alert-bed-1",
    to: "/beds?bed=bed-7",
  });
  // 62 min when the alert was raised plus the 2 since then.
  expect(attention.unattended[0].minutes).toBe(64);
  // bed-7 has the alert; only bed-8 stays as a plain overdue row.
  expect(attention.cleaning.map((item) => item.title)).toEqual([
    "Cama N-UCI-05",
  ]);
  expect(attention.total).toBe(2);
});
it("shows the bed alert in the ochre cleaning tone, never red", () => {
  renderUi(
    <CapacityView
      snapshot={snapshot([free("a", "ips-1"), free("b", "ips-2")])}
      queue={[]}
      assign={false}
      bedAlerts={[bedAlert]}
    />,
  );
  const group = screen.getByRole("region", { name: "Libres, pero sin limpiar" });
  expect(within(group).getByText("Cama N-UCI-04")).toBeInTheDocument();
  expect(group.innerHTML).toContain("bed-cleaning");
  expect(group.innerHTML).not.toContain("critical");
  expect(screen.queryByText("No hay nada urgente ahora")).toBeNull();
});
it("acknowledges a bed alert with a required note", async () => {
  const posts: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST")
        posts.push({ url, body: JSON.parse(String(init.body)) });
      return response({ ...bedAlert, estado: "reconocida" });
    }),
  );
  renderUi(
    <CapacityView
      snapshot={snapshot([free("a", "ips-1"), free("b", "ips-2")])}
      queue={[]}
      assign={false}
      bedAlerts={[bedAlert]}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Reconocer la alerta de Cama N-UCI-04" }),
  );
  const confirm = screen.getByRole("button", {
    name: "Confirmar reconocimiento",
  });
  expect(confirm).toBeDisabled();
  await userEvent.type(
    screen.getByLabelText(/Nota de reconocimiento/),
    "Aseo ya viene en camino",
  );
  await userEvent.click(confirm);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe("/api/v1/alerts/alert-bed-1/acknowledge");
  expect(posts[0].body).toEqual({ nota: "Aseo ya viene en camino" });
});
