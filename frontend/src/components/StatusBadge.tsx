import { Tag } from "primereact/tag";
import type { BedStatus } from "../api/types";
export const statusNames: Record<BedStatus, string> = {
  AVAILABLE: "Disponible",
  OCCUPIED: "Ocupada",
  CLEANING: "En limpieza",
  RESERVED: "Reservada",
  BLOCKED: "Bloqueada",
  MAINTENANCE: "Mantenimiento",
};
// Chip colours: tinted background with the dark tone of the same hue.
export const statusClasses: Record<BedStatus, string> = {
  AVAILABLE: "bg-bed-available-tint text-bed-available-ink",
  OCCUPIED: "bg-bed-occupied-tint text-bed-occupied-ink",
  CLEANING: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  RESERVED: "bg-bed-reserved-tint text-bed-reserved-ink",
  BLOCKED: "bg-bed-blocked-tint text-bed-blocked-ink",
  MAINTENANCE: "bg-bed-maintenance-tint text-bed-maintenance-ink",
};
// Solid state colour, used for strip segments and legends.
export const statusSolid: Record<BedStatus, string> = {
  AVAILABLE: "bg-bed-available",
  OCCUPIED: "bg-bed-occupied",
  CLEANING: "bg-bed-cleaning",
  RESERVED: "bg-bed-reserved",
  BLOCKED: "bg-bed-blocked bg-hatch",
  MAINTENANCE: "bg-bed-maintenance bg-hatch",
};
export const statusSymbols: Record<BedStatus, string> = {
  AVAILABLE: "+",
  OCCUPIED: "●",
  CLEANING: "↻",
  RESERVED: "R",
  BLOCKED: "×",
  MAINTENANCE: "M",
};
export function StatusBadge({ status }: { status: BedStatus }) {
  return (
    <Tag
      value={`${statusSymbols[status]} ${statusNames[status]}`}
      pt={{
        root: {
          className: `inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${statusClasses[status]}`,
        },
      }}
      ptOptions={{ mergeSections: false, mergeProps: false }}
    />
  );
}
