import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { BedStateForm } from "./BedDrawer";
import { renderUi } from "../../test/render";
import { bed, response } from "../../test/fixtures";
import { loadNetwork } from "../../api/capacity";
it("never offers an occupied state", () => {
  renderUi(
    <BedStateForm
      bed={bed}
      role="ADMIN"
      pending={false}
      onChange={vi.fn()}
      onRelease={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("option", { name: "Ocupada" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: "En limpieza" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Motivo del cambio")).toBeRequired();
});
it.each(["ADMIN", "EPS_COORDINATOR"] as const)(
  "offers only release for an occupied bed to %s",
  (role) => {
    renderUi(
      <BedStateForm
        bed={{ ...bed, status: "OCCUPIED" }}
        role={role}
        pending={false}
        onChange={vi.fn()}
        onRelease={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Liberar y enviar a limpieza" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  },
);
it("does not let the IPS operator release an occupied bed", () => {
  renderUi(
    <BedStateForm
      bed={{ ...bed, status: "OCCUPIED" }}
      role="IPS_CLINICAL_OPERATOR"
      pending={false}
      onChange={vi.fn()}
      onRelease={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Liberar y enviar a limpieza" }),
  ).not.toBeInTheDocument();
});
it.each(["SPECIALIST", "ACCOUNTANT", "PATIENT"] as const)(
  "shows %s a read-only bed with no way to change its state",
  (role) => {
    renderUi(
      <BedStateForm
        bed={bed}
        role={role}
        pending={false}
        onChange={vi.fn()}
        onRelease={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Guardar cambio" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Tu rol consulta el estado/)).toBeInTheDocument();
  },
);
it("rejects the complete capacity snapshot when an IPS call fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementation(async (url: string) =>
        url.endsWith("/capacity")
          ? response({ organizations: [{ organization_id: "ips-1" }] })
          : response({ detail: "IPS no disponible" }, 503),
      ),
  );
  await expect(loadNetwork()).rejects.toThrow("IPS no disponible");
});
