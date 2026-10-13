import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { CleaningRecord } from "../../api/cleaning";
import { ApiError, sessionToken } from "../../api/client";
import type { BedEvent, BedStatus, Role } from "../../api/types";
import { ToastProvider } from "../../components/ui";
import { renderUi } from "../../test/render";
import { bed, response, user } from "../../test/fixtures";
import { AuthProvider } from "../auth/AuthProvider";
import { BedDrawer } from "../beds/BedDrawer";
import {
  bedBlockedByEvidence,
  checklistItems,
  cleaningError,
  cleaningProblems,
  evidenceBlockedMessage,
  type CleaningDraft,
} from "./cleaning";
import { CleaningDialog } from "./CleaningDialog";
import { CleaningSection, hasCurrentApproval } from "./CleaningSection";

const cleaningBed = { ...bed, status: "CLEANING" as const };
const record: CleaningRecord = {
  id: "rec-1",
  location_id: bed.id,
  started_at: "2026-10-04T14:00:00Z",
  finished_at: "2026-10-04T14:40:00Z",
  isolation: "NONE",
  disinfectant: "CHLORINE_5000PPM",
  curtains_changed: false,
  checklist: Object.fromEntries(checklistItems.map(([key]) => [key, true])),
  notes: null,
  has_photo: true,
  photo_content_type: "image/jpeg",
  photo_size: 1200,
  status: "PENDING_REVIEW",
  reviewed_by: null,
  reviewed_at: null,
  review_notes: null,
  created_by: "operator-1",
  created_at: "2026-10-04T14:41:00Z",
  predicted_delay_probability: null,
  predicted_delay: null,
  delay_model_version: null,
};
const entry: BedEvent = {
  id: "event-1",
  location_id: bed.id,
  encounter_id: null,
  previous_status: "OCCUPIED",
  new_status: "CLEANING",
  reason: null,
  event_at: "2026-10-04T14:00:00Z",
  changed_by: null,
  predicted_delay_probability: 0.42,
  predicted_delay: true,
  delay_model_version: "v1",
};
const complete: CleaningDraft = {
  checklist: Object.fromEntries(checklistItems.map(([key]) => [key, true])),
  isolation: "NONE",
  disinfectant: "CHLORINE_5000PPM",
  curtainsChanged: false,
  startedAt: "2026-10-04T14:00",
  finishedAt: "2026-10-04T14:40",
  hasPhoto: true,
};
const now = new Date("2026-10-04T20:00:00");

it("lists the 11 CDC items with the keys of the API", () => {
  expect(checklistItems).toHaveLength(11);
  expect(checklistItems.map(([key]) => key)).toEqual([
    "barandas",
    "colchon",
    "mesa_auxiliar",
    "timbre_y_controles",
    "poste_de_suero",
    "manijas_e_interruptores",
    "cortinas",
    "bano",
    "piso",
    "residuos",
    "ropa_de_cama",
  ]);
});
it("accepts a complete draft and names each missing requirement", () => {
  expect(cleaningProblems(complete, now)).toEqual([]);
  const missing = cleaningProblems(
    { ...complete, checklist: { barandas: true }, hasPhoto: false },
    now,
  );
  expect(missing[0]).toBe("Falta marcar 10 de 11 puntos de la lista.");
  expect(missing.join(" ")).toContain("foto");
  expect(
    cleaningProblems({ ...complete, isolation: "CONTACT" }, now),
  ).toEqual(["Una habitación con aislamiento exige cambio de cortinas."]);
  expect(
    cleaningProblems(
      { ...complete, isolation: "SPORES", curtainsChanged: true },
      now,
    ),
  ).toEqual(["El aislamiento por esporas exige desinfectante esporicida."]);
  expect(
    cleaningProblems(
      {
        ...complete,
        isolation: "SPORES",
        curtainsChanged: true,
        disinfectant: "SPORICIDAL",
      },
      now,
    ),
  ).toEqual([]);
  expect(
    cleaningProblems({ ...complete, finishedAt: "2026-10-04T13:00" }, now),
  ).toEqual(["La limpieza no puede terminar antes de empezar."]);
  expect(
    cleaningProblems({ ...complete, finishedAt: "2026-10-05T09:00" }, now),
  ).toEqual(["La hora de fin no puede ser futura."]);
});
it("rewrites the server messages with the way forward", () => {
  expect(
    cleaningError(
      new ApiError(403, "Quien registra la limpieza no puede aprobarla"),
    ),
  ).toContain("No puedes aprobar una limpieza que registraste tú");
  expect(
    cleaningError(new ApiError(409, "Falta la foto de evidencia")),
  ).toContain("Sube una foto");
  expect(
    cleaningError(new ApiError(409, "El registro ya fue revisado (APPROVED)")),
  ).toContain("Otra persona ya revisó");
  expect(
    cleaningError(new ApiError(422, "Rechazar un registro exige indicar el motivo")),
  ).toContain("escribe el motivo");
  expect(cleaningError(new ApiError(403, "otro"))).toContain("Tu rol no puede");
  expect(cleaningError(new Error("falló"))).toBe("falló");
  expect(
    bedBlockedByEvidence(
      new ApiError(409, "La cama no tiene evidencia de limpieza aprobada"),
    ),
  ).toBe(true);
  expect(bedBlockedByEvidence(new ApiError(409, "La cama ya esta"))).toBe(false);
  expect(bedBlockedByEvidence(null)).toBe(false);
});
it("accepts an approval only if it is newer than the bed entering cleaning", () => {
  const approved = { ...record, status: "APPROVED" as const };
  expect(hasCurrentApproval([approved], "2026-10-04T14:00:00Z")).toBe(true);
  // An approval from a previous cleaning cycle does not count.
  expect(hasCurrentApproval([approved], "2026-10-04T15:00:00Z")).toBe(false);
  expect(hasCurrentApproval([record], undefined)).toBe(false);
  expect(hasCurrentApproval([approved], undefined)).toBe(true);
});

function dialog(onClose = vi.fn(), failPhoto = false) {
  sessionToken.set("test-session");
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === `/api/v1/beds/${bed.id}/cleaning` && init?.method === "POST")
        return response(record, 201);
      if (url === "/api/v1/cleaning/rec-1/photo" && init?.method === "PUT")
        return failPhoto
          ? response({ detail: "Solo se aceptan fotos PNG o JPEG" }, 422)
          : response(record);
      return response([]);
    });
  vi.stubGlobal("fetch", fetch);
  renderUi(
    <ToastProvider>
      <CleaningDialog bed={cleaningBed} onClose={onClose} />
    </ToastProvider>,
  );
  return { fetch, onClose };
}
async function fillChecklist() {
  const boxes = screen.getAllByRole("checkbox");
  // Eleven checklist items, then the curtains box.
  expect(boxes).toHaveLength(12);
  for (const box of boxes.slice(0, 11)) await userEvent.click(box);
}
it("opens the camera on a phone and blocks the form until everything is there", async () => {
  dialog();
  const input = screen.getByLabelText("Foto de la cama limpia");
  expect(input).toHaveAttribute("accept", "image/*");
  expect(input).toHaveAttribute("capture", "environment");
  const submit = screen.getByRole("button", { name: "Registrar limpieza" });
  expect(submit).toBeDisabled();
  expect(screen.getByText(/Falta marcar 11 de 11/)).toBeInTheDocument();
  await fillChecklist();
  // The photo is still missing.
  expect(submit).toBeDisabled();
  expect(screen.getByText(/Agrega la foto de la cama limpia/)).toBeInTheDocument();
  await userEvent.upload(
    input,
    new File(["x"], "cama.jpg", { type: "image/jpeg" }),
  );
  await waitFor(() => expect(submit).toBeEnabled());
});
it("sends the record with the 11 items and then the photo as a raw body", async () => {
  const { fetch, onClose } = dialog();
  await fillChecklist();
  await userEvent.selectOptions(screen.getByLabelText("Desinfectante"), "OTHER");
  await userEvent.type(screen.getByLabelText("Notas (opcional)"), "Sin novedad");
  const file = new File(["x"], "cama.jpg", { type: "image/jpeg" });
  await userEvent.upload(screen.getByLabelText("Foto de la cama limpia"), file);
  await userEvent.click(
    screen.getByRole("button", { name: "Registrar limpieza" }),
  );
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  const [create, upload] = fetch.mock.calls;
  expect(create[0]).toBe("/api/v1/beds/bed-1/cleaning");
  const body = JSON.parse(create[1].body);
  expect(Object.keys(body.checklist)).toHaveLength(11);
  expect(Object.values(body.checklist).every((value) => value === true)).toBe(
    true,
  );
  expect(body).toMatchObject({
    isolation: "NONE",
    disinfectant: "OTHER",
    curtains_changed: false,
    notes: "Sin novedad",
  });
  expect(new Date(body.finished_at).getTime()).toBeGreaterThanOrEqual(
    new Date(body.started_at).getTime(),
  );
  expect(upload[0]).toBe("/api/v1/cleaning/rec-1/photo");
  expect(upload[1].method).toBe("PUT");
  expect(upload[1].body).toBe(file);
  expect(new Headers(upload[1].headers).get("Content-Type")).toBe("image/jpeg");
});
it("keeps the record and says so when the photo cannot be uploaded", async () => {
  const { onClose } = dialog(vi.fn(), true);
  await fillChecklist();
  await userEvent.upload(
    screen.getByLabelText("Foto de la cama limpia"),
    new File(["x"], "cama.jpg", { type: "image/jpeg" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Registrar limpieza" }),
  );
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(
    await screen.findByText(/quedó registrada, pero la foto no se subió/),
  ).toBeInTheDocument();
});
it("refuses a file that is not a PNG or JPEG photo", async () => {
  dialog();
  await userEvent
    .setup({ applyAccept: false })
    .upload(
      screen.getByLabelText("Foto de la cama limpia"),
      new File(["x"], "nota.txt", { type: "text/plain" }),
    );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Solo se aceptan fotos PNG o JPEG",
  );
  expect(
    screen.getByRole("button", { name: "Registrar limpieza" }),
  ).toBeDisabled();
});
it("requires curtains and a sporicidal for isolation by spores", async () => {
  dialog();
  await userEvent.selectOptions(screen.getByLabelText("Aislamiento"), "SPORES");
  expect(
    screen.getByText("Una habitación con aislamiento exige cambio de cortinas."),
  ).toBeInTheDocument();
  expect(
    screen.getByText("El aislamiento por esporas exige desinfectante esporicida."),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Se cambiaron las cortinas"));
  await userEvent.selectOptions(
    screen.getByLabelText("Desinfectante"),
    "SPORICIDAL",
  );
  expect(
    screen.queryByText(/exige cambio de cortinas|exige desinfectante/),
  ).not.toBeInTheDocument();
});

interface Network {
  role: Role;
  id?: string;
  records?: CleaningRecord[];
  overdue?: unknown[];
  review?: (body: Record<string, unknown>) => Response;
  status?: Response;
}
function section({
  role,
  id = "reviewer-1",
  records = [record],
  overdue = [],
  review,
  status,
}: Network) {
  sessionToken.set("test-session");
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/auth/me") return response({ ...user, id, role });
      if (url === `/api/v1/beds/${bed.id}/cleaning`) return response(records);
      if (url.startsWith("/api/v1/beds/cleaning/overdue"))
        return response(overdue);
      if (url === `/api/v1/beds/${bed.id}/status`)
        return status ?? response({ ...bed, status: "AVAILABLE" });
      if (url.endsWith("/photo") && !init?.method)
        return new Response(new Blob(["img"], { type: "image/jpeg" }));
      if (url.endsWith("/review") && review)
        return review(JSON.parse(String(init?.body)));
      throw new Error(`Unexpected request ${url}`);
    });
  vi.stubGlobal("fetch", fetch);
  renderUi(
    <AuthProvider>
      <ToastProvider>
        <CleaningSection bed={cleaningBed} role={role} entry={entry} />
      </ToastProvider>
    </AuthProvider>,
  );
  return fetch;
}
it("lets the coordinator approve a pending record with its photo", async () => {
  const fetch = section({
    role: "EPS_COORDINATOR",
    review: (body) => response({ ...record, status: "APPROVED", ...body }),
  });
  expect(await screen.findByText("Pendiente de revisión")).toBeInTheDocument();
  // Rejecting needs a note; approving does not.
  expect(screen.getByRole("button", { name: "Rechazar" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Aprobar" }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([url]) => url === "/api/v1/cleaning/rec-1/review"),
    ).toBe(true),
  );
  const call = fetch.mock.calls.find(([url]) => url.endsWith("/review"))!;
  expect(JSON.parse(call[1].body)).toEqual({
    approved: true,
    review_notes: null,
  });
});
it("rejects with a note that the author will read", async () => {
  const fetch = section({
    role: "EPS_COORDINATOR",
    review: () => response({ ...record, status: "REJECTED" }),
  });
  await screen.findByText("Pendiente de revisión");
  await userEvent.type(
    screen.getByLabelText(/Nota de la revisión/),
    "Falta limpiar el baño",
  );
  await userEvent.click(screen.getByRole("button", { name: "Rechazar" }));
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/review"))).toBe(
      true,
    ),
  );
  const call = fetch.mock.calls.find(([url]) => url.endsWith("/review"))!;
  expect(JSON.parse(call[1].body)).toEqual({
    approved: false,
    review_notes: "Falta limpiar el baño",
  });
});
it("explains why the author cannot approve their own record", async () => {
  section({
    role: "ADMIN",
    id: "operator-1",
    records: [{ ...record, created_by: "operator-1" }],
  });
  expect(
    await screen.findByText(/Registraste esta limpieza: no puedes aprobarla/),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Aprobar" }),
  ).not.toBeInTheDocument();
});
it("shows the server reason when it refuses a self-approval", async () => {
  section({
    role: "ADMIN",
    review: () =>
      response({ detail: "Quien registra la limpieza no puede aprobarla" }, 403),
  });
  await userEvent.click(await screen.findByRole("button", { name: "Aprobar" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "No puedes aprobar una limpieza que registraste tú",
  );
});
it("blocks approving a record without a photo and offers to add one", async () => {
  section({
    role: "ADMIN",
    id: "operator-1",
    records: [{ ...record, has_photo: false, created_by: "operator-1" }],
  });
  expect(await screen.findByText(/Sin foto: no se puede aprobar/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Subir foto" }),
  ).toBeInTheDocument();
});
it("keeps the approval disabled for a coordinator while the photo is missing", async () => {
  section({
    role: "EPS_COORDINATOR",
    records: [{ ...record, has_photo: false }],
  });
  expect(
    await screen.findByText("Aprobar exige la foto de evidencia."),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Aprobar" })).toBeDisabled();
});
it("shows a rejection with its note and an approval with its date", async () => {
  section({
    role: "EPS_COORDINATOR",
    records: [
      {
        ...record,
        id: "rec-2",
        status: "REJECTED",
        review_notes: "Falta limpiar el baño",
      },
      {
        ...record,
        status: "APPROVED",
        reviewed_at: "2026-10-04T15:00:00Z",
        review_notes: "Todo en orden",
      },
    ],
  });
  expect(await screen.findByText("Rechazada")).toBeInTheDocument();
  expect(
    screen.getByText("Motivo del rechazo: Falta limpiar el baño"),
  ).toBeInTheDocument();
  expect(screen.getByText("Aprobada")).toBeInTheDocument();
  expect(screen.getByText(/Nota: Todo en orden/)).toBeInTheDocument();
});
it("shows the delay prediction of a record as a percentage and a flag", async () => {
  section({
    role: "EPS_COORDINATOR",
    records: [
      {
        ...record,
        predicted_delay_probability: 0.63,
        predicted_delay: true,
        delay_model_version: "v1",
      },
    ],
  });
  const probability = await screen.findByText("63 %");
  expect(probability).toHaveClass("tabular-nums");
  expect(screen.getByText("Se prevé retraso")).toBeInTheDocument();
});
it("shows the prediction of the entry event while there is no record yet", async () => {
  section({ role: "IPS_CLINICAL_OPERATOR", records: [] });
  expect(
    await screen.findByText("Aún no hay limpieza registrada para esta cama."),
  ).toBeInTheDocument();
  expect(screen.getByText("42 %")).toBeInTheDocument();
  expect(screen.getByText("Se prevé retraso")).toBeInTheDocument();
});
it("offers to register only to the IPS operator and the admin", async () => {
  section({ role: "IPS_CLINICAL_OPERATOR", records: [] });
  expect(
    await screen.findByRole("button", { name: "Registrar limpieza" }),
  ).toBeInTheDocument();
});
it.each(["SPECIALIST", "EPS_COORDINATOR"] as const)(
  "tells the %s that the IPS operator registers the cleaning",
  async (role) => {
    section({ role, records: [] });
    expect(
      await screen.findByText("La limpieza la registra la operación de la IPS."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Registrar limpieza" }),
    ).not.toBeInTheDocument();
  },
);
it("links the overdue alert for the roles that can read it", async () => {
  section({
    role: "EPS_COORDINATOR",
    overdue: [
      {
        location_id: bed.id,
        code: bed.code,
        name: bed.name,
        service: "UCI",
        organization_id: "ips-1",
        cleaning_since: "2026-10-04T14:00:00Z",
        minutes_in_cleaning: 95,
        pending_review_records: 1,
      },
    ],
  });
  const link = await screen.findByRole("link", {
    name: "Ver en «Requiere atención»",
  });
  expect(link).toHaveAttribute("href", "/capacity");
  expect(screen.getByText("1 h 35 min")).toBeInTheDocument();
});
it("offers to make the bed available once an approval exists", async () => {
  const fetch = section({
    role: "IPS_CLINICAL_OPERATOR",
    records: [
      {
        ...record,
        status: "APPROVED",
        reviewed_at: "2026-10-04T15:00:00Z",
      },
    ],
  });
  await userEvent.click(
    await screen.findByRole("button", { name: "Dejar la cama disponible" }),
  );
  await waitFor(() =>
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/status"))).toBe(
      true,
    ),
  );
  const call = fetch.mock.calls.find(([url]) => url.endsWith("/status"))!;
  expect(JSON.parse(call[1].body)).toMatchObject({ new_status: "AVAILABLE" });
});

function drawer(role: Role, status: BedStatus = "CLEANING") {
  sessionToken.set("test-session");
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/auth/me") return response({ ...user, role });
      if (url === `/api/v1/beds/${bed.id}/history`) return response([entry]);
      if (url === `/api/v1/beds/${bed.id}/cleaning`) return response([]);
      if (url.startsWith("/api/v1/beds/cleaning/overdue")) return response([]);
      if (url === `/api/v1/beds/${bed.id}/status` && init?.method === "POST")
        return response(
          { detail: "La cama no tiene evidencia de limpieza aprobada" },
          409,
        );
      throw new Error(`Unexpected request ${url}`);
    });
  vi.stubGlobal("fetch", fetch);
  renderUi(
    <AuthProvider>
      <ToastProvider>
        <BedDrawer
          bed={{ ...cleaningBed, status }}
          organization="Clínica Norte"
          role={role}
          onClose={vi.fn()}
        />
      </ToastProvider>
    </AuthProvider>,
  );
  return fetch;
}
it("explains in the bed panel why a bed cannot become available without evidence", async () => {
  drawer("IPS_CLINICAL_OPERATOR");
  await userEvent.selectOptions(
    await screen.findByLabelText("Nuevo estado"),
    "AVAILABLE",
  );
  await userEvent.type(screen.getByLabelText("Motivo del cambio"), "Ya limpia");
  await userEvent.click(screen.getByRole("button", { name: "Guardar cambio" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(evidenceBlockedMessage);
  expect(alert).toHaveTextContent("falta una limpieza aprobada");
});
it("shows the evidence section in the panel of a bed in cleaning", async () => {
  drawer("IPS_CLINICAL_OPERATOR");
  expect(
    await screen.findByRole("heading", { name: "Evidencia de limpieza" }),
  ).toBeInTheDocument();
  expect(
    await screen.findByRole("button", { name: "Registrar limpieza" }),
  ).toBeInTheDocument();
});
it("does not show the evidence section for an available bed", async () => {
  drawer("IPS_CLINICAL_OPERATOR", "AVAILABLE");
  await screen.findByText("Últimos movimientos");
  expect(
    screen.queryByRole("heading", { name: "Evidencia de limpieza" }),
  ).not.toBeInTheDocument();
  expect(within(document.body).queryByText(/Registrar limpieza/)).toBeNull();
});
