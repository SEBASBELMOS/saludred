import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSearchShortcut } from "./shortcuts";

function Screen({ dialog = false }: { dialog?: boolean }) {
  useSearchShortcut();
  return (
    <div>
      <button>Antes</button>
      <input aria-label="Buscar" data-main-search />
      <input aria-label="Otro" />
      {dialog && <div role="dialog" />}
    </div>
  );
}
it('focuses the main search with "/" and types nothing into it', async () => {
  render(<Screen />);
  await userEvent.keyboard("/");
  expect(screen.getByLabelText("Buscar")).toHaveFocus();
  expect(screen.getByLabelText("Buscar")).toHaveValue("");
});
it('lets "/" be typed in other fields', async () => {
  render(<Screen />);
  await userEvent.click(screen.getByLabelText("Otro"));
  await userEvent.keyboard("a/b");
  expect(screen.getByLabelText("Otro")).toHaveValue("a/b");
  expect(screen.getByLabelText("Buscar")).not.toHaveFocus();
});
it("stays quiet while a dialog is open", async () => {
  render(<Screen dialog />);
  await userEvent.keyboard("/");
  expect(screen.getByLabelText("Buscar")).not.toHaveFocus();
});
