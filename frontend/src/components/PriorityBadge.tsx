import { Tag } from "primereact/tag";
import type { Priority } from "../api/types";
export const priorities: Record<Priority, string> = {
  EMERGENCY: "Emergencia",
  URGENT: "Urgente",
  ROUTINE: "Rutina",
};
// Emergency is the only red in the product; routine carries no colour of its own.
const tones: Record<Priority, string> = {
  EMERGENCY: "bg-critical-tint text-critical-ink",
  URGENT: "bg-urgent-tint text-urgent-ink",
  ROUTINE: "bg-surface-sunken text-ink-muted",
};
const icons: Record<Priority, string> = {
  EMERGENCY: "pi pi-exclamation-circle",
  URGENT: "pi pi-exclamation-triangle",
  ROUTINE: "pi pi-minus",
};
export function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <Tag
      value={priorities[priority]}
      icon={icons[priority]}
      pt={{
        root: {
          className: `inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${tones[priority]}`,
        },
      }}
      ptOptions={{ mergeSections: false, mergeProps: false }}
    />
  );
}
