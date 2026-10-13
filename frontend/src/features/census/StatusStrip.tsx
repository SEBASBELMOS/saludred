import { Link } from "react-router-dom";
import type { Bed, BedStatus } from "../../api/types";
import { statusSolid, statusSymbols } from "../../components/StatusBadge";
import { focus } from "../../components/ui";
import { tileTones } from "./BedTile";

export const statusOrder: BedStatus[] = [
  "AVAILABLE",
  "OCCUPIED",
  "RESERVED",
  "CLEANING",
  "BLOCKED",
  "MAINTENANCE",
];
export const countByStatus = (beds: Bed[]) =>
  Object.fromEntries(
    statusOrder.map((status) => [
      status,
      beds.filter((bed) => bed.status === status).length,
    ]),
  ) as Record<BedStatus, number>;
const plural: Record<BedStatus, string> = {
  AVAILABLE: "disponibles",
  OCCUPIED: "ocupadas",
  CLEANING: "en limpieza",
  RESERVED: "reservadas",
  BLOCKED: "bloqueadas",
  MAINTENANCE: "en mantenimiento",
};
// One horizontal strip; each segment is as wide as its share of the beds.
export function StatusStrip({ beds }: { beds: Bed[] }) {
  const counts = countByStatus(beds);
  const visible = statusOrder.filter((status) => counts[status] > 0);
  const total = beds.length;
  const rate = total ? Math.round((counts.OCCUPIED / total) * 100) : 0;
  return (
    <div className="flex flex-wrap items-center gap-x-8 gap-y-3 border-b border-line pb-5">
      <div className="flex items-baseline gap-2">
        <strong className="font-display text-2xl font-semibold">
          {rate} %
        </strong>
        <span className="text-sm text-ink-muted">de ocupación</span>
      </div>
      <div
        aria-hidden="true"
        className="flex h-4 w-full min-w-48 flex-[1_1_16rem] gap-px overflow-hidden rounded-sm"
      >
        {visible.map((status) => (
          <span
            key={status}
            title={`${counts[status]} ${plural[status]}`}
            style={{ flexGrow: counts[status], flexBasis: 0 }}
            className={statusSolid[status]}
          />
        ))}
      </div>
      <ul className="flex min-w-0 flex-wrap gap-x-5 lg:shrink-0 gap-y-1.5 text-sm">
        {visible.map((status) => (
          <li key={status}>
            <Link
              to={`/beds?status=${status}`}
              className={`inline-flex items-center gap-1.5 rounded ${focus}`}
            >
              <span
                aria-hidden="true"
                className={`grid h-5 w-5 place-items-center rounded-sm border text-xs font-semibold leading-none ${tileTones[status]}`}
              >
                {statusSymbols[status]}
              </span>
              <strong className="font-semibold">{counts[status]}</strong>
              <span className="text-ink-muted">{plural[status]}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
