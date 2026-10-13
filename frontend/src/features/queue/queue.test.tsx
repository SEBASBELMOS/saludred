import { expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AssignDialog,
  AssignmentForm,
  PriorityConflictDialog,
} from "./AssignDialog";
import {
  compareQueue,
  criticalityPercent,
  priorRequest,
  queueKey,
} from "./priority";
import { rankCandidates, recommendedCandidate } from "./recommend";
import { QueueTable } from "./QueuePage";
import { requestBed } from "./RequestDialog";
import { renderUi } from "../../test/render";
import {
  candidate,
  encounter,
  patient,
  request,
  response,
} from "../../test/fixtures";
it("requires a nonblank reason before submitting an exception", async () => {
  const submit = vi.fn();
  renderUi(
    <AssignmentForm
      candidates={[candidate]}
      exception
      pending={false}
      submit={submit}
      onClose={vi.fn()}
    />,
  );
  const interaction = userEvent.setup();
  await interaction.click(screen.getByRole("radio"));
  expect(
    screen.getByRole("button", { name: "Asignar como excepción" }),
  ).toBeDisabled();
  await interaction.type(
    screen.getByLabelText("Motivo de la excepción"),
    "   ",
  );
  expect(
    screen.getByRole("button", { name: "Asignar como excepción" }),
  ).toBeDisabled();
  await interaction.type(
    screen.getByLabelText("Motivo de la excepción"),
    "Traslado coordinado",
  );
  await interaction.click(
    screen.getByRole("button", { name: "Asignar como excepción" }),
  );
  expect(submit).toHaveBeenCalledWith("bed-1", "Traslado coordinado");
});
it("offers the previous request and exception choices", async () => {
  const previous = vi.fn(),
    exception = vi.fn();
  renderUi(
    <PriorityConflictDialog
      previous={{ ...request, priority: "EMERGENCY" }}
      onPrevious={previous}
      onException={exception}
      onClose={vi.fn()}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Ver solicitud anterior" }),
  );
  expect(previous).toHaveBeenCalledOnce();
  await userEvent.click(
    screen.getByRole("button", { name: "Asignar como excepción" }),
  );
  expect(exception).toHaveBeenCalledOnce();
});
it("mirrors priority comparison for a destination-free request", () => {
  const earlier = {
    ...request,
    id: "earlier",
    priority: "EMERGENCY" as const,
    target_organization_id: "north",
  };
  expect(priorRequest(request, [earlier])?.id).toBe("earlier");
  expect(
    priorRequest({ ...request, target_organization_id: "south" }, [earlier]),
  ).toBeUndefined();
});
it("turns a server priority conflict into a reasoned override", async () => {
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith("/candidates")) return response([candidate]);
      if (url.endsWith("/assign"))
        return JSON.parse(String(init.body)).override_priority
          ? response({ id: "assignment" })
          : response({ detail: "Otra solicitud debe atenderse primero" }, 409);
      return response([]);
    });
  vi.stubGlobal("fetch", fetch);
  const close = vi.fn();
  renderUi(
    <AssignDialog
      request={request}
      queue={[request]}
      onClose={close}
      onSelect={vi.fn()}
    />,
  );
  await userEvent.click(await screen.findByRole("radio"));
  await userEvent.click(
    screen.getByRole("button", { name: "Confirmar asignación" }),
  );
  await screen.findByRole("dialog", { name: "Hay una solicitud anterior" });
  await userEvent.click(
    screen.getByRole("button", { name: "Asignar como excepción" }),
  );
  await userEvent.click(await screen.findByRole("radio"));
  await userEvent.type(
    screen.getByLabelText("Motivo de la excepción"),
    "Traslado confirmado",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Asignar como excepción" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalled());
  const bodies = fetch.mock.calls
    .filter(([url]) => String(url).endsWith("/assign"))
    .map(([, init]) => JSON.parse(init.body));
  expect(bodies[1]).toEqual({
    location_id: "bed-1",
    override_priority: true,
    reason: "Traslado confirmado",
  });
});
it("reuses an open encounter without creating another", async () => {
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string) =>
      url.endsWith("/encounters")
        ? response([encounter])
        : response({ id: "new-request" }),
    );
  vi.stubGlobal("fetch", fetch);
  await requestBed({
    patient,
    document: "",
    organization: "ips-1",
    priority: "URGENT",
    service: "UCI",
    reason: "Motivo",
  });
  expect(
    fetch.mock.calls.some(
      ([url, init]) => url === "/api/v1/encounters" && init.method === "POST",
    ),
  ).toBe(false);
  expect(
    JSON.parse(
      fetch.mock.calls.find(([url]) => url.endsWith("/bed-requests"))![1].body,
    ),
  ).toMatchObject({ encounter_id: encounter.id });
});
it("creates the missing encounter before requesting a bed", async () => {
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init: RequestInit) =>
      url.includes("/patients/")
        ? response([])
        : url === "/api/v1/encounters" && init.method === "POST"
          ? response(encounter)
          : response({ id: "new-request" }),
    );
  vi.stubGlobal("fetch", fetch);
  await requestBed({
    patient,
    document: "",
    organization: "ips-1",
    priority: "EMERGENCY",
    service: "UCI",
    reason: "Motivo",
  });
  const writes = fetch.mock.calls.filter(([, init]) => init.method === "POST");
  expect(writes.map(([url]) => url)).toEqual([
    "/api/v1/encounters",
    "/api/v1/bed-requests",
  ]);
  expect(JSON.parse(writes[0][1].body)).toMatchObject({
    encounter_class: "EMER",
    status: "in-progress",
  });
});

it("refreshes the cached queue before opening an earlier request discovered after a 409", async () => {
  const earlier = { ...request, id: "earlier", priority: "EMERGENCY" as const };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url: string) =>
        url.endsWith("/candidates")
          ? response([candidate])
          : url.endsWith("/assign")
            ? response({ detail: "Otra solicitud debe atenderse primero" }, 409)
            : response([earlier, request]),
      ),
  );
  const select = vi.fn();
  const { client } = renderUi(
    <AssignDialog
      request={request}
      queue={[request]}
      onClose={vi.fn()}
      onSelect={select}
    />,
  );
  await userEvent.click(await screen.findByRole("radio"));
  await userEvent.click(
    screen.getByRole("button", { name: "Confirmar asignación" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Ver solicitud anterior" }),
  );
  expect(select).toHaveBeenCalledWith("earlier");
  expect(client.getQueryData(["queue"])).toEqual([earlier, request]);
});

it("ranks same service and same IPS first and recommends it", () => {
  const far = { ...candidate, location_id: "far", same_organization: false };
  const other = {
    ...candidate,
    location_id: "other",
    same_service: false,
  };
  const best = { ...candidate, location_id: "best" };
  expect(rankCandidates([other, far, best]).map((c) => c.location_id)).toEqual([
    "best",
    "far",
    "other",
  ]);
  expect(recommendedCandidate([other, far, best])?.location_id).toBe("best");
  // With only other-service beds nothing is preselected.
  expect(recommendedCandidate([other])).toBeUndefined();
});
it("preselects the recommended bed and labels it", () => {
  const other = {
    ...candidate,
    location_id: "other",
    name: "Cama Otra",
    same_service: false,
  };
  renderUi(
    <AssignmentForm
      candidates={[other, candidate]}
      exception={false}
      pending={false}
      submit={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByText("Recomendada")).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /Cama Norte 01/ })).toBeChecked();
  expect(
    screen.getByRole("button", { name: "Confirmar asignación" }),
  ).toBeEnabled();
  expect(screen.queryByLabelText(/Motivo/)).not.toBeInTheDocument();
});
it("asks for a reason before assigning a bed of another service", async () => {
  const submit = vi.fn();
  const other = {
    ...candidate,
    location_id: "other",
    name: "Cama Otra",
    same_service: false,
  };
  renderUi(
    <AssignmentForm
      candidates={[candidate, other]}
      exception={false}
      pending={false}
      submit={submit}
      onClose={vi.fn()}
    />,
  );
  const interaction = userEvent.setup();
  await interaction.click(screen.getByRole("radio", { name: /Cama Otra/ }));
  const confirm = screen.getByRole("button", { name: "Confirmar asignación" });
  expect(confirm).toBeDisabled();
  await interaction.type(
    screen.getByLabelText("Motivo para asignar una cama de otro servicio"),
    "Sin camas en UCI",
  );
  await interaction.click(confirm);
  expect(submit).toHaveBeenCalledWith("other", "Sin camas en UCI");
});
// The API (_queue_key in app/services/beds.py) serves by priority rank, then by
// criticality (higher first, no score last), then by arrival.
const queued = (
  id: string,
  priority: "EMERGENCY" | "URGENT" | "ROUTINE",
  score: number | null,
  requested_at: string,
) => ({ ...request, id, priority, criticality_score: score, requested_at });
it("orders the queue by priority, criticality with nulls last, then arrival", () => {
  const list = [
    queued("routine-high", "ROUTINE", 0.99, "2026-09-29T10:00:00Z"),
    queued("urgent-none-old", "URGENT", null, "2026-09-29T09:00:00Z"),
    queued("urgent-low", "URGENT", 0.2, "2026-09-29T08:00:00Z"),
    queued("emergency-none", "EMERGENCY", null, "2026-09-29T12:00:00Z"),
    queued("urgent-high-new", "URGENT", 0.8, "2026-09-29T11:00:00Z"),
    queued("urgent-none-new", "URGENT", null, "2026-09-29T09:30:00Z"),
    queued("urgent-high-old", "URGENT", 0.8, "2026-09-29T07:00:00Z"),
  ];
  expect([...list].sort(compareQueue).map((r) => r.id)).toEqual([
    "emergency-none",
    "urgent-high-old",
    "urgent-high-new",
    "urgent-low",
    "urgent-none-old",
    "urgent-none-new",
    "routine-high",
  ]);
});
it("builds the same key as the API for a missing score", () => {
  expect(queueKey(queued("a", "URGENT", null, "2026-09-29T09:00:00Z"))[1]).toBe(
    1,
  );
  expect(queueKey(queued("b", "URGENT", 1, "2026-09-29T09:00:00Z"))[1]).toBe(-1);
  expect(queueKey(queued("c", "EMERGENCY", 0, "2026-09-29T09:00:00Z"))[0]).toBe(
    -3,
  );
});
it("warns about a request with the same priority but higher criticality", () => {
  const mine = queued("mine", "URGENT", 0.3, "2026-09-29T08:00:00Z");
  // It arrived later, but its criticality is higher: the API serves it first.
  const higher = queued("higher", "URGENT", 0.9, "2026-09-29T09:00:00Z");
  expect(priorRequest(mine, [mine, higher])?.id).toBe("higher");
  expect(priorRequest(higher, [mine, higher])).toBeUndefined();
});
it("does not warn about a routine request with a high score or a later arrival", () => {
  const urgent = queued("urgent", "URGENT", null, "2026-09-29T09:00:00Z");
  const routine = queued("routine", "ROUTINE", 0.99, "2026-09-29T07:00:00Z");
  expect(priorRequest(urgent, [urgent, routine])).toBeUndefined();
  expect(priorRequest(routine, [urgent, routine])?.id).toBe("urgent");
  // Scored goes before unscored; between equals, the earlier arrival goes first.
  const scored = queued("scored", "URGENT", 0.1, "2026-09-29T10:00:00Z");
  expect(priorRequest(urgent, [urgent, scored])?.id).toBe("scored");
  const sameScore = queued("same", "URGENT", 0.1, "2026-09-29T10:30:00Z");
  expect(priorRequest(sameScore, [scored, sameScore])?.id).toBe("scored");
  expect(priorRequest(scored, [scored, sameScore])).toBeUndefined();
});
it("returns the most urgent of several earlier requests", () => {
  const mine = queued("mine", "ROUTINE", null, "2026-09-29T10:00:00Z");
  const first = queued("first", "EMERGENCY", 0.5, "2026-09-29T11:00:00Z");
  const second = queued("second", "URGENT", 0.9, "2026-09-29T08:00:00Z");
  expect(priorRequest(mine, [mine, second, first])?.id).toBe("first");
});
it("shows the criticality as a percentage with tabular figures", () => {
  expect(criticalityPercent(0.874)).toBe("87 %");
  expect(criticalityPercent(0)).toBe("0 %");
  expect(criticalityPercent(null)).toBe("—");
  expect(criticalityPercent(undefined)).toBe("—");
  renderUi(
    <QueueTable
      requests={[
        { ...request, criticality_score: 0.998 },
        { ...request, id: "request-2", criticality_score: null },
      ]}
      organizations={[]}
      assign={false}
      onAssign={vi.fn()}
    />,
  );
  const score = screen.getByText("100 %");
  expect(score).toHaveClass("tabular-nums");
  expect(
    screen.getByRole("columnheader", { name: "Criticidad" }),
  ).toBeInTheDocument();
  expect(screen.getAllByText("—")).toHaveLength(1);
});
it("gives the primary action only to the first row of the queue", () => {
  renderUi(
    <QueueTable
      requests={[
        request,
        { ...request, id: "request-2", patient_name: "Luis Gómez" },
      ]}
      organizations={[]}
      assign
      onAssign={vi.fn()}
    />,
  );
  const buttons = screen.getAllByRole("button", { name: /Asignar cama a/ });
  expect(buttons).toHaveLength(2);
  expect(buttons[0]).toHaveAttribute("data-primary-assign");
  expect(buttons[1]).not.toHaveAttribute("data-primary-assign");
  expect(buttons[0].className).toContain("bg-accent");
  expect(buttons[1].className).not.toContain("bg-accent ");
});
