import type { NetworkSnapshot } from "../../api/capacity";
import type { Alert } from "../../api/alerts";
import type { OverdueCleaningBed, QueuedRequest } from "../../api/types";
import { minutesSince } from "../alerts/useAlerts";

export interface AttentionItem {
  key: string;
  title: string;
  /** Service and institution, in one line. */
  detail: string;
  minutes?: number;
  action: string;
  to: string;
  /** Set when the item is an alert the user can acknowledge with a note. */
  alertId?: string;
}
export interface Attention {
  /** Emergencies waiting for a bed, oldest first (critical). */
  waiting: AttentionItem[];
  /** Beds that are free but not cleaned: the active bed alerts, oldest first (cleaning ochre). */
  unattended: AttentionItem[];
  /** Beds in cleaning for too long, oldest first (attention). */
  cleaning: AttentionItem[];
  /** Institutions with no free bed (neutral). */
  full: AttentionItem[];
  /** Ids of the requests already listed, so the queue does not repeat them. */
  listedRequests: Set<string>;
  total: number;
}
// Only what asks for an action, grouped by kind and sorted by age inside each group.
export function buildAttention(
  snapshot: NetworkSnapshot,
  queue: QueuedRequest[],
  overdue: OverdueCleaningBed[],
  assign: boolean,
  bedAlerts: Alert[] = [],
  now: number = Date.now(),
): Attention {
  const { network, beds } = snapshot;
  const orgName = (id: string) =>
    network.organizations.find((o) => o.organization_id === id)
      ?.organization_name ?? "";
  const line = (...parts: (string | null | undefined)[]) =>
    parts.filter(Boolean).join(", ");
  const emergencies = queue
    .filter((r) => r.priority === "EMERGENCY")
    .sort((a, b) => b.waiting_minutes - a.waiting_minutes);
  const waiting = emergencies.map((r) => ({
    key: `request-${r.id}`,
    title: r.patient_name,
    detail: line(
      r.required_service,
      orgName(r.target_organization_id || r.requesting_organization_id),
    ),
    minutes: r.waiting_minutes,
    action: assign ? "Asignar cama" : "Ver ficha",
    to: assign ? `/queue?assign=${r.id}` : `/patients?patient=${r.patient_id}`,
  }));
  // The alert already says the bed is overdue: it replaces the plain overdue row.
  const alerted = new Set(bedAlerts.map((alert) => alert.cama_id));
  const sinceCleaning = (alert: Alert) => {
    const waited = Number(alert.datos?.minutos_en_limpieza);
    return (
      (Number.isFinite(waited) ? waited : 0) +
      minutesSince(alert.created_at, now)
    );
  };
  const unattended = [...bedAlerts]
    .sort((a, b) => sinceCleaning(b) - sinceCleaning(a))
    .map((alert) => {
      const bed = beds.find((b) => b.id === alert.cama_id);
      const code = bed?.code ?? String(alert.datos?.cama ?? "");
      return {
        key: `alert-${alert.id}`,
        title: code ? `Cama ${code}` : "Cama",
        detail: line(
          bed?.service || "Sin servicio",
          orgName(alert.organizacion_id ?? bed?.organization_id ?? ""),
        ),
        minutes: sinceCleaning(alert),
        action: "Ver cama",
        to: `/beds?bed=${alert.cama_id}`,
        alertId: alert.id,
      };
    });
  const cleaning = [...overdue]
    .filter((bed) => !alerted.has(bed.location_id))
    .sort((a, b) => b.minutes_in_cleaning - a.minutes_in_cleaning)
    .map((bed) => ({
      key: `cleaning-${bed.location_id}`,
      title: `Cama ${bed.code}`,
      detail: line(bed.service || "Sin servicio", orgName(bed.organization_id)),
      minutes: bed.minutes_in_cleaning,
      action: "Ver cama",
      to: `/beds?bed=${bed.location_id}`,
    }));
  const full = network.organizations
    .filter(
      (org) =>
        !beds.some(
          (b) =>
            b.organization_id === org.organization_id &&
            b.status === "AVAILABLE",
        ),
    )
    .map((org) => ({
      key: `full-${org.organization_id}`,
      title: org.organization_name,
      detail: "Ninguna cama libre en todos los servicios",
      action: "Ver camas",
      to: `/beds?org=${org.organization_id}`,
    }));
  return {
    waiting,
    unattended,
    cleaning,
    full,
    listedRequests: new Set(emergencies.map((r) => r.id)),
    total:
      waiting.length + unattended.length + cleaning.length + full.length,
  };
}
/** The queue without the requests that already appear under "Requiere atención". */
export const restOfQueue = (queue: QueuedRequest[], attention: Attention) =>
  queue.filter((r) => !attention.listedRequests.has(r.id));
