import { Link } from "react-router-dom";
import { Tooltip } from "primereact/tooltip";
import type { Bed, BedStatus } from "../../api/types";
import { statusNames, statusSymbols } from "../../components/StatusBadge";
import { useChangeHighlight } from "../../components/motion";
import { focus } from "../../components/ui";
import { minutes } from "../../components/format";

// Available stands out as the only solid fill; occupied (the normal state) stays calm.
// Text on the blocked and maintenance tints uses the dark ink tone, never white.
export const tileTones: Record<BedStatus, string> = {
  AVAILABLE: "border-bed-available bg-bed-available text-white",
  OCCUPIED: "border-bed-occupied/40 bg-bed-occupied-tint text-bed-occupied-ink",
  CLEANING: "border-bed-cleaning/60 bg-bed-cleaning-tint text-bed-cleaning-ink",
  RESERVED: "border-bed-reserved/50 bg-bed-reserved-tint text-bed-reserved-ink",
  BLOCKED:
    "border-bed-blocked/70 bg-bed-blocked-tint bg-hatch-dark text-bed-blocked-ink",
  MAINTENANCE:
    "border-bed-maintenance/70 bg-bed-maintenance-tint bg-hatch-dark text-bed-maintenance-ink",
};
export const shortCode = (code: string) => code.split("-").pop() || code;
export function BedTooltip() {
  return (
    <Tooltip
      target=".bed-tile"
      event="both"
      showDelay={120}
      pt={{
        root: { className: "absolute z-[1300] max-w-56 animate-tip" },
        text: {
          className:
            "rounded-lg bg-ink px-2.5 py-1.5 text-xs font-semibold text-white shadow-overlay",
        },
        arrow: { className: "hidden" },
      }}
    />
  );
}
interface TileProps {
  bed: Bed;
  /** md is 44 px (command centre), lg is 48 px (bed board). */
  size?: "md" | "lg";
  /** Fill the grid cell as a square (phone layout). */
  fluid?: boolean;
  to?: string;
  onSelect?: (id: string) => void;
  selected?: boolean;
  /** Minutes in the current state, when the API tells us. */
  since?: number;
  tabIndex?: number;
  onFocus?: () => void;
}
// Full code, state and time in state: used for the tooltip and the accessible name.
export function describeBed(bed: Bed, since?: number) {
  const state = statusNames[bed.status];
  const time = since != null ? `, ${minutes(since)} en este estado` : "";
  return {
    tip: `${bed.code}: ${state}${time}`,
    label: `${bed.name}, ${bed.code}: ${state}${time}`,
  };
}
export function BedTile({
  bed,
  size = "md",
  fluid = false,
  to,
  onSelect,
  selected,
  since,
  tabIndex,
  onFocus,
}: TileProps) {
  const changed = useChangeHighlight(bed.status);
  const lg = size === "lg";
  // Fluid tiles fill a square grid cell on phones and keep their size from sm up.
  const box = fluid ? "aspect-square w-full" : lg ? "h-12 w-12" : "h-11 w-11";
  const className = `bed-tile inline-flex shrink-0 flex-col items-center justify-center gap-0.5 rounded border text-xs font-semibold leading-none hov:ring-2 hov:ring-ink ${box} ${tileTones[bed.status]} ${selected ? "ring-2 ring-ink" : ""} ${changed} ${focus}`;
  const content = (
    <>
      <span>{shortCode(bed.code)}</span>
      <span aria-hidden="true" className="text-sm">
        {statusSymbols[bed.status]}
      </span>
    </>
  );
  const { tip, label } = describeBed(bed, since);
  return to ? (
    <Link
      to={to}
      aria-label={label}
      data-pr-tooltip={tip}
      data-pr-position="top"
      tabIndex={tabIndex}
      onFocus={onFocus}
      className={className}
    >
      {content}
    </Link>
  ) : (
    <button
      type="button"
      aria-label={label}
      aria-pressed={selected}
      data-pr-tooltip={tip}
      data-pr-position="top"
      tabIndex={tabIndex}
      onFocus={onFocus}
      onClick={() => onSelect?.(bed.id)}
      className={className}
    >
      {content}
    </button>
  );
}
