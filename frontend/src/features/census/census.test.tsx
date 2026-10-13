import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { nextTile, useRovingTiles, type Box } from "./roving";
import { availabilityMessage, newlyAvailable } from "./AvailabilityAnnouncer";
import { BedTile, describeBed } from "./BedTile";
import { bed } from "../../test/fixtures";

// Two rows of three tiles, 44 px wide with 6 px gaps.
const grid: Box[] = Array.from({ length: 6 }, (_, i) => ({
  x: (i % 3) * 50,
  y: Math.floor(i / 3) * 50,
  w: 44,
  h: 44,
}));
it("moves between tiles with the arrow keys", () => {
  expect(nextTile(grid, 1, "ArrowRight")).toBe(2);
  expect(nextTile(grid, 0, "ArrowLeft")).toBe(0);
  expect(nextTile(grid, 1, "ArrowDown")).toBe(4);
  expect(nextTile(grid, 5, "ArrowUp")).toBe(2);
  expect(nextTile(grid, 4, "ArrowDown")).toBe(4);
  expect(nextTile(grid, 3, "Home")).toBe(0);
  expect(nextTile(grid, 3, "End")).toBe(5);
});
it("jumps to the closest column when the row below is shorter", () => {
  const rows: Box[] = [
    { x: 0, y: 0, w: 44, h: 44 },
    { x: 50, y: 0, w: 44, h: 44 },
    { x: 100, y: 0, w: 44, h: 44 },
    { x: 0, y: 50, w: 44, h: 44 },
  ];
  expect(nextTile(rows, 2, "ArrowDown")).toBe(3);
});
function Board() {
  const { boardProps, tileProps } = useRovingTiles(["a", "b"]);
  return (
    <div {...boardProps}>
      {["a", "b"].map((id) => (
        <BedTile
          key={id}
          bed={{ ...bed, id, code: `N-${id}` }}
          to={`/beds?bed=${id}`}
          {...tileProps(id)}
        />
      ))}
    </div>
  );
}
it("keeps one tab stop and follows the arrow keys", async () => {
  render(
    <MemoryRouter>
      <Board />
    </MemoryRouter>,
  );
  const [first, second] = screen.getAllByRole("link");
  expect(first).toHaveAttribute("tabindex", "0");
  expect(second).toHaveAttribute("tabindex", "-1");
  first.focus();
  await userEvent.keyboard("{ArrowRight}");
  expect(second).toHaveFocus();
  expect(second).toHaveAttribute("tabindex", "0");
  expect(first).toHaveAttribute("tabindex", "-1");
});
it("describes a bed with its code, state and time in state", () => {
  expect(describeBed({ ...bed, status: "CLEANING" }, 95).tip).toBe(
    "N-01: En limpieza, 1 h 35 min en este estado",
  );
  expect(describeBed(bed).label).toBe("Cama Norte 01, N-01: Disponible");
});
it("finds the beds that became available after a refresh", () => {
  const before = [
    { ...bed, id: "1", status: "CLEANING" as const },
    { ...bed, id: "2", status: "AVAILABLE" as const },
    { ...bed, id: "3", status: "OCCUPIED" as const },
  ];
  const after = [
    { ...before[0], status: "AVAILABLE" as const },
    before[1],
    before[2],
    { ...bed, id: "4", status: "AVAILABLE" as const },
  ];
  expect(newlyAvailable(before, after).map((b) => b.id)).toEqual(["1"]);
  expect(newlyAvailable(undefined, after)).toEqual([]);
  expect(availabilityMessage(newlyAvailable(before, after))).toBe(
    "La cama N-01 ahora está disponible",
  );
});
