import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  ApiError,
  api,
  onSessionExpired,
  request,
  sessionToken,
} from "../../api/client";
import { LoginForm } from "./LoginPage";
import { homeFor, menuFor } from "./permissions";
import { RoleMenu } from "../../components/AppLayout";
import { AuthProvider, useAuth } from "./AuthProvider";
import type { Role } from "../../api/types";
import { renderUi } from "../../test/render";
import { response, user } from "../../test/fixtures";
import { Action } from "../../components/ui";
import { vi, describe, it, expect } from "vitest";
describe("login messages", () => {
  it.each([
    [
      401,
      "Usuario o contraseña incorrectos. Al tercer intento fallido la cuenta se bloquea.",
    ],
    [
      423,
      "Cuenta bloqueada por intentos fallidos. Solicite el desbloqueo a un administrador.",
    ],
  ])("shows the exact message for %s", async (status, message) => {
    const login = vi
      .fn()
      .mockRejectedValue(new ApiError(status as number, "Internal response"));
    renderUi(<LoginForm login={login} />);
    const interaction = userEvent.setup();
    await interaction.type(screen.getByLabelText("Usuario"), "registro.local");
    await interaction.type(screen.getByLabelText("Contraseña"), "example-test");
    await interaction.click(screen.getByRole("button", { name: "Ingresar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(login).toHaveBeenCalledWith("registro.local", "example-test");
    if (status === 423)
      expect(screen.getByRole("alert")).toHaveClass("bg-bed-cleaning-tint");
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  });
});
it.each<[Role, string[]]>([
  [
    "ADMIN",
    [
      "Centro de mando",
      "Cola de asignación",
      "Pizarra de camas",
      "Pacientes",
      "Remisiones",
      "Indicadores",
      "Cuentas y auditoría",
      "Red de atención",
      "Contabilidad",
    ],
  ],
  [
    "EPS_COORDINATOR",
    [
      "Centro de mando",
      "Cola de asignación",
      "Pizarra de camas",
      "Pacientes",
      "Indicadores",
    ],
  ],
  [
    "IPS_CLINICAL_OPERATOR",
    [
      "Centro de mando",
      "Cola de asignación",
      "Pizarra de camas",
      "Pacientes",
      "Remisiones",
    ],
  ],
  [
    "SPECIALIST",
    ["Cola de asignación", "Pizarra de camas", "Pacientes", "Remisiones"],
  ],
  ["ACCOUNTANT", ["Contabilidad"]],
  ["PATIENT", ["Mi información"]],
])("renders the menu for %s", (role, names) => {
  renderUi(<RoleMenu role={role} />);
  expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual(
    names,
  );
  expect(menuFor(role).map((item) => item.label)).toEqual(names);
});
it.each([false, true])(
  "expires the session for a data 401 (raw=%s)",
  async (raw) => {
    sessionToken.set("test-session");
    const expire = vi.fn();
    const unsubscribe = onSessionExpired(expire);
    const fetch = vi
      .fn()
      .mockResolvedValue(response({ detail: "Expired" }, 401));
    vi.stubGlobal("fetch", fetch);
    await expect(
      raw ? request("/imaging/instances/1/preview") : api("/patients/1"),
    ).rejects.toMatchObject({ status: 401 });
    expect(sessionToken.get()).toBeNull();
    expect(expire).toHaveBeenCalledOnce();
    expect(
      new Headers(fetch.mock.calls[0][1].headers).get("Authorization"),
    ).toBe("Bearer test-session");
    unsubscribe();
  },
);
it("does not trigger session expiration for incorrect login credentials", async () => {
  const expire = vi.fn();
  const unsubscribe = onSessionExpired(expire);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({}, 401)));
  await expect(
    api("/auth/login", { method: "POST", body: {} }),
  ).rejects.toMatchObject({ status: 401 });
  expect(expire).not.toHaveBeenCalled();
  unsubscribe();
});
function SessionProbe() {
  const auth = useAuth();
  return (
    <>
      <span>{auth.user?.full_name || auth.message || "Sin sesión"}</span>
      <Action label="Salir" onClick={auth.logout} />
    </>
  );
}
it("restores a tab session and clears cached records on logout", async () => {
  sessionToken.set("test-session");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(user)));
  const { client } = renderUi(
    <AuthProvider>
      <SessionProbe />
    </AuthProvider>,
  );
  await screen.findByText(user.full_name);
  client.setQueryData(["patient-record", "private"], { name: "cached" });
  await userEvent.click(screen.getByRole("button", { name: "Salir" }));
  expect(sessionToken.get()).toBeNull();
  await waitFor(() =>
    expect(client.getQueryData(["patient-record", "private"])).toBeUndefined(),
  );
  expect(screen.getByText("Sin sesión")).toBeInTheDocument();
});

it("renders the session-ended message and removes the signed-in identity after a data 401", async () => {
  sessionToken.set("test-session");
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url: string) =>
        url.endsWith("/auth/me") ? response(user) : response({}, 401),
      ),
  );
  const { client } = renderUi(
    <AuthProvider>
      <SessionProbe />
    </AuthProvider>,
  );
  await screen.findByText(user.full_name);
  client.setQueryData(["patient-record", "private"], { name: "cached" });
  await expect(api("/patients/private")).rejects.toMatchObject({ status: 401 });
  expect(
    await screen.findByText("Tu sesión terminó. Vuelve a iniciar sesión."),
  ).toBeInTheDocument();
  expect(screen.queryByText(user.full_name)).not.toBeInTheDocument();
  expect(client.getQueryData(["patient-record", "private"])).toBeUndefined();
});
it.each<[Role, string]>([
  ["ADMIN", "/capacity"],
  ["EPS_COORDINATOR", "/capacity"],
  ["IPS_CLINICAL_OPERATOR", "/beds"],
  ["SPECIALIST", "/referrals"],
  ["ACCOUNTANT", "/billing"],
  ["PATIENT", "/portal"],
])("lands %s on %s, a route its own menu allows", (role, home) => {
  expect(homeFor(role)).toBe(home);
  expect(menuFor(role).map((item) => item.path)).toContain(home);
});
