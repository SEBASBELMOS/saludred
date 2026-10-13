import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { Organization } from "../../api/types";
import { sessionToken } from "../../api/client";
import { ToastProvider } from "../../components/ui";
import { renderUi } from "../../test/render";
import { page, response, user } from "../../test/fixtures";
import { AuthProvider } from "../auth/AuthProvider";
import {
  DeleteOrganizationDialog,
  OrganizationFormDialog,
  OrganizationHistoryDrawer,
} from "./OrganizationDialogs";
import { OrganizationsPage } from "./OrganizationsPage";
import { ApiError } from "../../api/client";
import { organizationError, validateOrganization } from "./organizationState";
const eps: Organization = {
  id: "eps-1",
  code: "EPS-PRUEBA",
  name: "EPS de prueba",
  organization_type: "EPS",
  parent_organization_id: null,
  created_at: "2026-09-30T12:00:00Z",
  updated_at: "2026-09-30T12:00:00Z",
};

it.each([false, true])(
  "creates with exactly code and name using the dedicated endpoint (IPS=%s)",
  async (isIps) => {
    const fetch = vi.fn().mockImplementation(async () => response(eps, 201));
    vi.stubGlobal("fetch", fetch);
    const close = vi.fn();
    renderUi(
      <OrganizationFormDialog eps={isIps ? eps : undefined} onClose={close} />,
    );
    const interaction = userEvent.setup();
    await interaction.type(screen.getByLabelText("Código"), "NUEVA");
    await interaction.type(
      screen.getByLabelText("Nombre"),
      "Organización nueva",
    );
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    await interaction.click(
      screen.getByRole("button", { name: isIps ? "Crear IPS" : "Crear EPS" }),
    );
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(
      isIps ? "/api/v1/eps/eps-1/ips" : "/api/v1/eps",
    );
    expect(fetch.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      code: "NUEVA",
      name: "Organización nueva",
    });
  },
);
it.each([409, 422])(
  "keeps server %s errors inline with entered values",
  async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () =>
        response(
          {
            detail:
              status === 409
                ? "Ya existe una organizacion con ese codigo"
                : [{ loc: ["body", "code"], msg: "Invalid input" }],
          },
          status,
        ),
      ),
    );
    const close = vi.fn();
    renderUi(<OrganizationFormDialog onClose={close} />);
    const interaction = userEvent.setup();
    await interaction.type(screen.getByLabelText("Código"), "REPETIDO");
    await interaction.type(screen.getByLabelText("Nombre"), "Prueba");
    await interaction.click(screen.getByRole("button", { name: "Crear EPS" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      status === 409
        ? "Ya existe una organización con ese código"
        : "El código debe tener entre 1 y 32 caracteres",
    );
    expect(screen.getByLabelText("Código")).toHaveValue("REPETIDO");
    expect(close).not.toHaveBeenCalled();
  },
);
it.each([
  [
    409,
    "Ya existe una organizacion con ese codigo",
    "Ya existe una organización con ese código",
  ],
  [
    409,
    "La organizacion indicada en la ruta no es una EPS",
    "Una IPS no puede ser la organización padre",
  ],
  [
    409,
    "No se puede eliminar la organizacion: tiene IPS asociadas activas",
    "No se puede eliminar esta EPS mientras tenga IPS activas",
  ],
  [
    409,
    "La organizacion padre debe ser una EPS activa",
    "Restaura la EPS y vuelve a intentarlo",
  ],
  [422, "code: String should have at most 32 characters", "El código debe tener"],
  [422, "name: Field required", "El nombre debe tener entre 1 y 200"],
  [
    422,
    "an EPS must not reference a parent organization",
    "Una EPS es la raíz de la red",
  ],
  [422, "algo inesperado", "El servidor rechazó los datos"],
  [403, "x", "Solo la administración puede"],
])("explains a %s answer in Spanish: %s", (status, detail, expected) => {
  expect(organizationError(new ApiError(status, detail))).toContain(expected);
});
it("edits only changed fields without type or parent", async () => {
  const fetch = vi.fn().mockImplementation(async () => response(eps));
  vi.stubGlobal("fetch", fetch);
  const close = vi.fn();
  renderUi(<OrganizationFormDialog organization={eps} onClose={close} />);
  const interaction = userEvent.setup();
  await interaction.clear(screen.getByLabelText("Nombre"));
  await interaction.type(screen.getByLabelText("Nombre"), "Nombre corregido");
  await interaction.click(
    screen.getByRole("button", { name: "Guardar cambios" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(fetch.mock.calls[0][0]).toBe("/api/v1/organizations/eps-1");
  expect(fetch.mock.calls[0][1].method).toBe("PUT");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
    name: "Nombre corregido",
  });
});
it("enforces both length boundaries and rejects blank fields", () => {
  expect(validateOrganization("a", "b")).toEqual({ code: "a", name: "b" });
  expect(
    validateOrganization("a".repeat(32), "b".repeat(200)).name,
  ).toHaveLength(200);
  expect(() => validateOrganization("a".repeat(33), "b")).toThrow("código");
  expect(() => validateOrganization("a", "b".repeat(201))).toThrow("nombre");
  expect(() => validateOrganization("  ", "b")).toThrow("código");
  expect(() => validateOrganization("a", " ")).toThrow("nombre");
});
function setupNetwork(restoreStatus = 200) {
  sessionToken.set("test-session");
  let deleted = false;
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/auth/me") return response(user);
      if (url.startsWith("/api/v1/organizations?"))
        return response(page(deleted ? [] : [eps]));
      if (url === "/api/v1/eps/eps-1/ips") return response([]);
      if (url === "/api/v1/organizations/eps-1" && init?.method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      if (
        url === "/api/v1/organizations/eps-1/restore" &&
        init?.method === "POST"
      ) {
        if (restoreStatus === 200) {
          deleted = false;
          return response(eps);
        }
        return response(
          { detail: "No fue posible restaurar la organización" },
          restoreStatus,
        );
      }
      throw new Error(`Unexpected request ${url}`);
    });
  vi.stubGlobal("fetch", fetch);
  const rendered = renderUi(
    <AuthProvider>
      <ToastProvider>
        <OrganizationsPage />
      </ToastProvider>
    </AuthProvider>,
  );
  return { ...rendered, fetch };
}
async function deleteEps() {
  const interaction = userEvent.setup();
  await interaction.click(
    await screen.findByRole("button", { name: "Eliminar EPS de prueba" }),
  );
  expect(screen.getByText(/Es un borrado lógico/)).toBeInTheDocument();
  await interaction.click(
    screen.getByRole("button", { name: "Confirmar eliminación" }),
  );
  await screen.findByRole("heading", { name: "Eliminadas recientemente" });
  return interaction;
}
it("deletes then restores from the undo toast and refreshes the list", async () => {
  const { fetch, client } = setupNetwork();
  const interaction = await deleteEps();
  await interaction.click(
    await screen.findByRole("button", { name: "Deshacer" }),
  );
  await waitFor(() =>
    expect(
      fetch.mock.calls.some(
        ([url, init]) =>
          url === "/api/v1/organizations/eps-1/restore" &&
          init.method === "POST",
      ),
    ).toBe(true),
  );
  await screen.findByRole("button", { name: "Eliminar EPS de prueba" });
  await waitFor(() =>
    expect(
      screen.queryByRole("heading", { name: "Eliminadas recientemente" }),
    ).not.toBeInTheDocument(),
  );
  expect(
    client.getQueryData(["recently-deleted-organizations", user.id]),
  ).toEqual([]);
});
it("also restores from the session list", async () => {
  const { fetch } = setupNetwork();
  const interaction = await deleteEps();
  await interaction.click(
    screen.getByRole("button", { name: "Restaurar EPS de prueba" }),
  );
  await screen.findByRole("button", { name: "Eliminar EPS de prueba" });
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/restore"))).toBe(true);
});
it("keeps a failed restore available for retry", async () => {
  setupNetwork(409);
  const interaction = await deleteEps();
  await interaction.click(screen.getByRole("button", { name: "Deshacer" }));
  expect(
    (await screen.findAllByText("No fue posible restaurar la organización"))
      .length,
  ).toBeGreaterThan(0);
  expect(
    screen.getByRole("button", { name: "Restaurar EPS de prueba" }),
  ).toBeEnabled();
});
it("does not undo using a different signed-in session", async () => {
  const { fetch } = setupNetwork();
  const interaction = await deleteEps();
  sessionToken.set("other-session");
  await interaction.click(screen.getByRole("button", { name: "Deshacer" }));
  await screen.findByText(/Tu sesión cambió/);
  expect(fetch.mock.calls.some(([url]) => url.endsWith("/restore"))).toBe(
    false,
  );
});
it("shows previous snapshots with date and author in the history drawer", async () => {
  const fetch = vi.fn().mockImplementation(async () =>
    response([
      {
        version_number: 1,
        snapshot_json: { ...eps, name: "Nombre anterior" },
        changed_fields: ["name"],
        changed_by: "author-123",
        changed_at: "2026-09-30T12:00:00Z",
      },
    ]),
  );
  vi.stubGlobal("fetch", fetch);
  renderUi(<OrganizationHistoryDrawer organization={eps} onClose={vi.fn()} />);
  expect(await screen.findByText("Nombre anterior")).toBeInTheDocument();
  expect(screen.getByText("Autor: author-123")).toBeInTheDocument();
  expect(screen.getByText("Campos modificados: Nombre")).toBeInTheDocument();
  const version = screen.getByText("Versión 1").closest("li")!;
  expect(
    within(version).getByText(/^30 sep, \d{2}:\d{2}$/),
  ).toBeInTheDocument();
  expect(fetch.mock.calls[0][0]).toBe("/api/v1/organizations/eps-1/history");
});

it("opens IPS creation inside its EPS with an accurate nested count", async () => {
  sessionToken.set("test-session");
  const ips = {
    ...eps,
    id: "ips-1",
    code: "IPS-PRUEBA",
    name: "Clínica de prueba",
    organization_type: "IPS",
    parent_organization_id: eps.id,
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url: string) =>
        url.endsWith("/auth/me")
          ? response(user)
          : url.endsWith("/ips")
            ? response([ips])
            : response(page([eps])),
      ),
  );
  renderUi(
    <AuthProvider>
      <OrganizationsPage />
    </AuthProvider>,
  );
  const toggle = await screen.findByRole("button", {
    name: /EPS-PRUEBA/,
  });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await screen.findByText("1 IPS en esta red");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(
    screen.getByRole("heading", { name: "Clínica de prueba" }),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Nueva IPS en EPS de prueba" }),
  );
  expect(
    screen.getByText(/La nueva IPS pertenecerá a EPS de prueba/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});
it("shows a failed EPS list with retry instead of an empty network", async () => {
  sessionToken.set("test-session");
  let failed = true;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url: string) =>
        url.endsWith("/auth/me")
          ? response(user)
          : url.endsWith("/ips")
            ? response([])
            : failed
              ? response({ detail: "Consulta no disponible" }, 503)
              : response(page([eps])),
      ),
  );
  renderUi(
    <AuthProvider>
      <OrganizationsPage />
    </AuthProvider>,
  );
  await screen.findByText("Consulta no disponible");
  expect(screen.queryByText(/0 EPS registradas/)).not.toBeInTheDocument();
  failed = false;
  await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
  await screen.findByRole("button", { name: "Editar EPS de prueba" });
});

it("keeps a refused EPS deletion in its confirmation without offering undo", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async () =>
      response(
        {
          detail:
            "No se puede eliminar la organizacion: tiene IPS asociadas activas",
        },
        409,
      ),
    ),
  );
  const close = vi.fn();
  const deleted = vi.fn();
  renderUi(
    <DeleteOrganizationDialog
      organization={eps}
      onClose={close}
      onDeleted={deleted}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Confirmar eliminación" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "mientras tenga IPS activas. Elimina primero sus IPS",
  );
  expect(close).not.toHaveBeenCalled();
  expect(deleted).not.toHaveBeenCalled();
});
