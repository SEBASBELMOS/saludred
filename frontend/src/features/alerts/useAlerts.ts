import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { alertsApi, type Alert, type Pending } from "../../api/alerts";

// Every ACTIVE alert the user may see. The banner reads the clinical ones, the
// command center the bed ones; both live in one cache entry the live channel keeps fresh.
export const ALERTS_KEY = ["alerts", "active"] as const;
export const PENDING_KEY = ["pendientes"] as const;

export const useActiveAlerts = (enabled: boolean) =>
  useQuery({
    queryKey: ALERTS_KEY,
    queryFn: async ({ signal }): Promise<Alert[]> => {
      const list = await alertsApi.list("activa", signal);
      // A degraded response must read as "no alerts", not crash the shell.
      return Array.isArray(list) ? list : [];
    },
    enabled,
    // The banner is chrome, not content: it must not drive the freshness indicator.
    meta: { silent: true },
  });

// R18: the three counters. They are pushed (pendientes_actualizados), never polled.
export const usePending = (enabled: boolean) =>
  useQuery({
    queryKey: PENDING_KEY,
    queryFn: ({ signal }) => alertsApi.pending(signal),
    enabled,
    meta: { silent: true },
  });

const asCounter = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : 0;

// The live event carries the counters already computed, plus a "motivo" we drop.
export function countersFrom(
  datos: Record<string, unknown> | null | undefined,
): Pending | null {
  if (!datos) return null;
  if (
    !("reportes_por_revisar" in datos) &&
    !("remisiones_recibidas" in datos) &&
    !("alertas_activas" in datos)
  )
    return null;
  return {
    reportes_por_revisar: asCounter(datos.reportes_por_revisar),
    remisiones_recibidas: asCounter(datos.remisiones_recibidas),
    alertas_activas: asCounter(datos.alertas_activas),
  };
}

// Timestamps without a zone designator are UTC (the API stores UTC).
export function parseMoment(value: string): number {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasZone ? value : `${value}Z`).getTime();
}

/** A clock that ticks so "hace 5 min" stays honest without a refetch. */
export function useNow(everyMs = 30_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

export function minutesSince(value: string, now: number) {
  const then = parseMoment(value);
  return Number.isNaN(then) ? 0 : Math.max(0, Math.floor((now - then) / 60_000));
}

// Critical first, then the one that has waited longest.
export function byUrgency(a: Alert, b: Alert) {
  const rank = (alert: Alert) => (alert.nivel === "critico" ? 0 : 1);
  return (
    rank(a) - rank(b) ||
    parseMoment(a.created_at) - parseMoment(b.created_at)
  );
}
