import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { sessionToken } from "../../api/client";
import type { Role } from "../../api/types";
import { renderUi } from "../../test/render";
import { encounter, patient, response, user } from "../../test/fixtures";
import { AuthProvider } from "../auth/AuthProvider";
import { EncounterDialog } from "./EncounterDialog";

function setup(role: Role) {
  sessionToken.set("test-session");
  const fetch = vi
    .fn()
    .mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/auth/me")
        return response({ ...user, role, organization_id: "ips-1" });
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
      if (url === "/api/v1/encounters" && init?.method === "POST")
        return response(encounter, 201);
      throw new Error(`Unexpected request ${url}`);
    });
  vi.stubGlobal("fetch", fetch);
  const saved = vi.fn();
  renderUi(
    <AuthProvider>
      <EncounterDialog patient={patient} onClose={vi.fn()} onSaved={saved} />
    </AuthProvider>,
  );
  return { fetch, saved };
}

// The API answers 403 when an especialista or a medico registers an encounter in
// another IPS, so the form fixes the institution instead of offering a choice.
it.each(["SPECIALIST", "IPS_CLINICAL_OPERATOR"] as const)(
  "fixes the institution to their own IPS for the %s",
  async (role) => {
    const { fetch, saved } = setup(role);
    expect(await screen.findByText(/Institución: Clínica Norte/)).toBeVisible();
    expect(
      screen.queryByLabelText("Institución que atiende"),
    ).not.toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("Motivo de consulta"),
      "Control posterior a la remisión",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Registrar atención" }),
    );
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    const post = fetch.mock.calls.find(
      ([url, init]) => url === "/api/v1/encounters" && init?.method === "POST",
    )!;
    expect(JSON.parse(post[1].body)).toMatchObject({
      organization_id: "ips-1",
      patient_id: patient.id,
    });
  },
);
it("lets the admin choose the institution", async () => {
  setup("ADMIN");
  expect(
    await screen.findByLabelText("Institución que atiende"),
  ).toBeInTheDocument();
});
