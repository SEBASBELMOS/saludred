import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { sessionToken } from "../../api/client";
import type { Role } from "../../api/types";
import { renderUi } from "../../test/render";
import { response, user } from "../../test/fixtures";
import { AuthProvider } from "./AuthProvider";
import { RouteGuard } from "./RouteGuard";

it.each<[Role | null, string]>([
  [null, "Inicio de sesión"],
  ["PATIENT", "Portal propio"],
  ["IPS_CLINICAL_OPERATOR", "Pizarra permitida"],
  ["SPECIALIST", "Remisiones permitidas"],
  ["ACCOUNTANT", "Cuenta propia"],
  ["EPS_COORDINATOR", "Resumen permitido"],
  ["ADMIN", "Cuentas permitidas"],
])("guards the accounts route for %s", async (role, expected) => {
  if (role) sessionToken.set("test-session");
  const fetch = vi.fn().mockResolvedValue(response({ ...user, role }));
  vi.stubGlobal("fetch", fetch);
  renderUi(
    <AuthProvider>
      <Routes>
        <Route element={<RouteGuard path="/accounts" />}>
          <Route path="/accounts" element={<p>Cuentas permitidas</p>} />
        </Route>
        <Route path="/capacity" element={<p>Resumen permitido</p>} />
        <Route path="/beds" element={<p>Pizarra permitida</p>} />
        <Route path="/patients" element={<p>Pacientes permitidos</p>} />
        <Route path="/referrals" element={<p>Remisiones permitidas</p>} />
        <Route path="/billing" element={<p>Cuenta propia</p>} />
        <Route path="/portal" element={<p>Portal propio</p>} />
        <Route path="/login" element={<p>Inicio de sesión</p>} />
      </Routes>
    </AuthProvider>,
    "/accounts",
  );
  expect(await screen.findByText(expected)).toBeInTheDocument();
  expect(fetch.mock.calls.every(([url]) => url === "/api/v1/auth/me")).toBe(
    true,
  );
});

it.each<[Role | null, string]>([
  [null, "Inicio de sesión"],
  ["PATIENT", "Portal propio"],
  ["IPS_CLINICAL_OPERATOR", "Pizarra permitida"],
  ["SPECIALIST", "Remisiones permitidas"],
  ["ACCOUNTANT", "Cuenta propia"],
  ["EPS_COORDINATOR", "Resumen permitido"],
  ["ADMIN", "Red permitida"],
])("guards the organization route for %s", async (role, expected) => {
  if (role) sessionToken.set("test-session");
  const fetch = vi
    .fn()
    .mockImplementation(async () => response({ ...user, role }));
  vi.stubGlobal("fetch", fetch);
  renderUi(
    <AuthProvider>
      <Routes>
        <Route element={<RouteGuard path="/organizations" />}>
          <Route path="/organizations" element={<p>Red permitida</p>} />
        </Route>
        <Route path="/capacity" element={<p>Resumen permitido</p>} />
        <Route path="/beds" element={<p>Pizarra permitida</p>} />
        <Route path="/patients" element={<p>Pacientes permitidos</p>} />
        <Route path="/referrals" element={<p>Remisiones permitidas</p>} />
        <Route path="/billing" element={<p>Cuenta propia</p>} />
        <Route path="/portal" element={<p>Portal propio</p>} />
        <Route path="/login" element={<p>Inicio de sesión</p>} />
      </Routes>
    </AuthProvider>,
    "/organizations",
  );
  expect(await screen.findByText(expected)).toBeInTheDocument();
  expect(fetch.mock.calls.every(([url]) => url === "/api/v1/auth/me")).toBe(
    true,
  );
});
