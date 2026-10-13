import { useEffect, useRef, useState } from "react";
import type { Bed } from "../../api/types";

/** Beds that were not available before and are now (new beds do not count). */
export function newlyAvailable(prev: Bed[] | undefined, next: Bed[]) {
  if (!prev) return [];
  const before = new Map(prev.map((bed) => [bed.id, bed.status]));
  return next.filter(
    (bed) =>
      bed.status === "AVAILABLE" &&
      before.has(bed.id) &&
      before.get(bed.id) !== "AVAILABLE",
  );
}
export function availabilityMessage(freed: Bed[]) {
  if (!freed.length) return "";
  if (freed.length === 1)
    return `La cama ${freed[0].code} ahora está disponible`;
  const codes = freed.slice(0, 5).map((bed) => bed.code);
  const rest = freed.length - codes.length;
  return `${freed.length} camas ahora están disponibles: ${codes.join(", ")}${rest ? ` y ${rest} más` : ""}`;
}
// Screen readers hear when a bed becomes available after a refresh; nothing is shown.
export function AvailabilityAnnouncer({ beds }: { beds: Bed[] }) {
  const previous = useRef<Bed[]>();
  const [message, setMessage] = useState("");
  useEffect(() => {
    const freed = newlyAvailable(previous.current, beds);
    previous.current = beds;
    if (freed.length) setMessage(availabilityMessage(freed));
  }, [beds]);
  return (
    <p role="status" aria-live="polite" className="sr-only">
      {message}
    </p>
  );
}
