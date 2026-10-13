import { expect, it, vi } from "vitest";
import { useState } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderUi } from "../test/render";
import { Drawer } from "./Drawer";

function Harness({ onClose = vi.fn() }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Abrir</button>
      <Drawer
        visible={open}
        onClose={() => {
          onClose();
          setOpen(false);
        }}
        title="Detalle"
        label="Detalle de prueba"
        footer={<button>Último</button>}
      >
        <button>Primero</button>
      </Drawer>
    </>
  );
}
it("keeps Tab inside, closes with Escape and returns focus to the opener", async () => {
  const close = vi.fn();
  renderUi(<Harness onClose={close} />);
  const opener = screen.getByRole("button", { name: "Abrir" });
  await userEvent.click(opener);
  const dialog = await screen.findByRole("dialog", {
    name: "Detalle de prueba",
  });
  expect(dialog).toBeInTheDocument();
  const last = screen.getByRole("button", { name: "Último" });
  last.focus();
  await userEvent.tab();
  expect(dialog.contains(document.activeElement)).toBe(true);
  expect(document.activeElement).not.toBe(opener);
  await userEvent.keyboard("{Escape}");
  expect(close).toHaveBeenCalled();
  await vi.waitFor(() => expect(opener).toHaveFocus());
});
