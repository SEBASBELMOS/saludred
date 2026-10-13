import { fireEvent, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Action, DataTable, FormActions, Modal } from "./ui";
import { renderUi } from "../test/render";

it("highlights changed rows without remounting their controls or highlighting unchanged rows", () => {
  const view = (status: string) => (
    <DataTable caption="Camas" headers={["Estado"]}>
      <tr key="bed">
        <td>
          {status}
          <input aria-label="Nota" defaultValue="Conservar" />
        </td>
      </tr>
    </DataTable>
  );
  const { rerender } = renderUi(view("Disponible"));
  const input = screen.getByRole("textbox");
  rerender(view("Ocupada"));
  expect(screen.getByRole("textbox")).toBe(input);
  const row = screen.getByText("Ocupada").closest("tr")!;
  const animation = row.className;
  expect(animation).toContain("animate-flash");
  rerender(view("Ocupada"));
  expect(row.className).toBe(animation);
  rerender(view("En limpieza"));
  expect(row.className).not.toBe(animation);
});
it("keeps the measured button width and accessible label while loading", () => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 160,
  } as DOMRect);
  const { rerender } = renderUi(<Action label="Guardar" />);
  rerender(<Action label="Guardando…" loading disabled />);
  const button = screen.getByRole("button", { name: "Guardando…" });
  expect(button).toHaveStyle({ width: "160px" });
  expect(button).toHaveAttribute("aria-busy", "true");
  expect(button).toBeDisabled();
});
it("closes dialog actions immediately for reduced motion", () => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  const close = vi.fn();
  renderUi(
    <Modal title="Prueba" onClose={close}>
      <FormActions label="Guardar" onClose={close} />
    </Modal>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
  expect(close).toHaveBeenCalledOnce();
});
