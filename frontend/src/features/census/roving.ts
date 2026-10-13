import { useRef, useState, type KeyboardEvent } from "react";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}
const arrows = [
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "Home",
  "End",
];
/** Index of the tile a key moves to; stays put when there is nowhere to go. */
export function nextTile(boxes: Box[], index: number, key: string): number {
  const last = boxes.length - 1;
  if (key === "ArrowLeft") return Math.max(0, index - 1);
  if (key === "ArrowRight") return Math.min(last, index + 1);
  if (key === "Home") return 0;
  if (key === "End") return last;
  if (key !== "ArrowUp" && key !== "ArrowDown") return index;
  const centre = (box: Box) => ({ x: box.x + box.w / 2, y: box.y + box.h / 2 });
  const here = centre(boxes[index]);
  const direction = key === "ArrowUp" ? -1 : 1;
  const rows = boxes
    .map((box, position) => ({ position, ...centre(box) }))
    .filter((tile) => (tile.y - here.y) * direction > 2);
  if (!rows.length) return index;
  // Nearest row first, then the tile in that row closest to the current column.
  const rowY = rows.reduce(
    (best, tile) =>
      Math.abs(tile.y - here.y) < Math.abs(best - here.y) ? tile.y : best,
    rows[0].y,
  );
  return rows
    .filter((tile) => Math.abs(tile.y - rowY) <= 2)
    .reduce((best, tile) =>
      Math.abs(tile.x - here.x) < Math.abs(best.x - here.x) ? tile : best,
    ).position;
}
/**
 * Roving focus for a board of tiles: one tab stop, arrow keys move between tiles,
 * Enter opens the focused one (links and buttons do that natively).
 */
export function useRovingTiles(ids: string[]) {
  const [stored, setActive] = useState("");
  const board = useRef<HTMLDivElement>(null);
  const active = ids.includes(stored) ? stored : ids[0];
  return {
    boardProps: {
      ref: board,
      onKeyDown(event: KeyboardEvent<HTMLElement>) {
        if (!arrows.includes(event.key)) return;
        const tiles = [
          ...(board.current?.querySelectorAll<HTMLElement>(".bed-tile") ?? []),
        ];
        const index = tiles.indexOf(event.target as HTMLElement);
        if (index < 0) return;
        const boxes = tiles.map((tile) => {
          const rect = tile.getBoundingClientRect();
          return { x: rect.x, y: rect.y, w: rect.width, h: rect.height };
        });
        const next = nextTile(boxes, index, event.key);
        event.preventDefault();
        if (next !== index) tiles[next].focus();
      },
    },
    tileProps: (id: string) => ({
      tabIndex: id === active ? 0 : -1,
      onFocus: () => setActive(id),
    }),
  };
}
