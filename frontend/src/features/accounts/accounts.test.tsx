import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { renderUi } from "../../test/render";
import { page, response } from "../../test/fixtures";
import { CreateAccountDialog } from "./AccountDialogs";
import { accountError } from "./accountState";

const eps = {
  id: "eps-1",
  code: "EPS-SALUDRED",
  name: "EPS SaludRed",
  organization_type: "EPS",
  parent_organization_id: null,
  created_at: "2026-09-30T12:00:00Z",
  updated_at: "2026-09-30T12:00:00Z",
};
function setup(create: () => Response = () => response({ username: "x" }, 201)) {
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/network/capacity")
        return response({
          organizations: [
            {
              organization_id: "ips-1",
              organization_code: "IPS-NORTE",
              organization_name: "Clínica Norte",
            },
          ],
        });
      if (url === "/api/v1/organizations/ips-1/beds") return response([]);
      if (url.startsWith("/api/v1/organizations?")) return response(page([eps]));
      if (url === "/api/v1/admin/users" && init?.method === "POST")
        return create();
      throw new Error(`Unexpected request ${url}`);
    });
  vi.stubGlobal("fetch", fetch);
  renderUi(<CreateAccountDialog onClose={vi.fn()} />);
  return fetch;
}

it("offers especialista with an IPS and contable with an EPS", async () => {
  setup();
  const role = await screen.findByLabelText("Rol");
  expect(
    screen.getByRole("option", { name: "Especialista" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Contable" })).toBeInTheDocument();
  await userEvent.selectOptions(role, "SPECIALIST");
  expect(screen.getByLabelText("IPS")).toBeRequired();
  expect(screen.queryByLabelText("EPS")).not.toBeInTheDocument();
  expect(screen.getByText(/recibe remisiones/)).toBeInTheDocument();
  await userEvent.selectOptions(role, "ACCOUNTANT");
  expect(screen.getByLabelText("EPS")).toBeRequired();
  expect(screen.queryByLabelText("IPS")).not.toBeInTheDocument();
  expect(screen.getByText(/sin datos clínicos/)).toBeInTheDocument();
});
it("sends the IPS of a new especialista and the EPS of a new contable", async () => {
  const fetch = setup();
  const role = await screen.findByLabelText("Rol");
  await userEvent.selectOptions(role, "SPECIALIST");
  await userEvent.selectOptions(screen.getByLabelText("IPS"), "ips-1");
  await userEvent.type(screen.getByLabelText("Usuario"), "especialista.nuevo");
  await userEvent.type(screen.getByLabelText("Nombre completo"), "Ana Gómez");
  await userEvent.type(screen.getByLabelText("Clave inicial"), "Demo2026!");
  await userEvent.click(screen.getByRole("button", { name: "Crear cuenta" }));
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(([url]) => url === "/api/v1/admin/users"),
    ).toBe(true),
  );
  const call = fetch.mock.calls.find(([url]) => url === "/api/v1/admin/users")!;
  expect(JSON.parse(call[1].body)).toMatchObject({
    role: "SPECIALIST",
    organization_id: "ips-1",
    username: "especialista.nuevo",
  });
});
it("explains a duplicate user and a wrong organization in the form", async () => {
  setup(() => response({ detail: "Ya existe una cuenta con ese usuario" }, 409));
  const role = await screen.findByLabelText("Rol");
  await userEvent.selectOptions(role, "ACCOUNTANT");
  await userEvent.selectOptions(screen.getByLabelText("EPS"), "eps-1");
  await userEvent.type(screen.getByLabelText("Usuario"), "contable.eps");
  await userEvent.type(screen.getByLabelText("Nombre completo"), "Luis Pérez");
  await userEvent.type(screen.getByLabelText("Clave inicial"), "Demo2026!");
  await userEvent.click(screen.getByRole("button", { name: "Crear cuenta" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Ese usuario ya existe. Elige otro nombre de usuario.",
  );
});

it.each([
  [
    409,
    "Ya existe una cuenta con ese usuario",
    "ACCOUNTANT" as const,
    "Ese usuario ya existe",
  ],
  [
    409,
    "Ese paciente ya tiene una cuenta de acceso",
    "PATIENT" as const,
    "Ese paciente ya tiene una cuenta",
  ],
  [
    422,
    "Este rol necesita una organizacion de tipo IPS",
    "SPECIALIST" as const,
    "El rol especialista pertenece a una IPS",
  ],
  [
    422,
    "Este rol necesita una organizacion de tipo EPS",
    "ACCOUNTANT" as const,
    "El rol contable pertenece a una EPS",
  ],
  [
    422,
    "No hay un paciente con ese documento",
    "PATIENT" as const,
    "No hay un paciente con ese documento",
  ],
  [
    422,
    "username: String should match pattern '^[a-z0-9._-]+$'",
    "SPECIALIST" as const,
    "minúsculas, números, punto, guion",
  ],
  [
    422,
    "password: String should have at least 8 characters",
    "SPECIALIST" as const,
    "La clave debe tener entre 8 y 128",
  ],
  [403, "x", "ADMIN" as const, "Solo la administración crea cuentas"],
])("maps %s %s for %s", (status, detail, role, expected) => {
  expect(accountError(new ApiError(status, detail), role)).toContain(expected);
});
