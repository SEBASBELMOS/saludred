import { createContext, useCallback, useContext, type ReactNode } from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { notificationsApi } from "../../api/notifications";
import type { Alert } from "../../api/alerts";
import type { AppNotification, User } from "../../api/types";
import { canSeeCriticalAlerts } from "../auth/permissions";
import { useToast } from "../../components/ui";
import { ALERTS_KEY, PENDING_KEY, countersFrom } from "../alerts/useAlerts";
import { INVOICES_KEY, SUMMARY_KEY } from "../billing/useBillingFailure";
import { REFERRALS_KEY } from "../referrals/keys";
import { useLiveEvents, type LiveState } from "./useLiveEvents";

export const NOTIFICATIONS_KEY = ["notifications"] as const;
const LiveContext = createContext<LiveState>("connecting");
export const useLiveState = () => useContext(LiveContext);

// Some mocked or degraded responses are not arrays: the inbox then reads as empty.
const asList = (data: unknown): AppNotification[] =>
  Array.isArray(data) ? (data as AppNotification[]) : [];

// Live-only events never enter the inbox and never raise a toast: they only
// move what is already on screen (the counters, the alert banner).
const EPHEMERAL = new Set(["pendientes_actualizados", "alerta_reconocida"]);
const isEphemeral = (event: AppNotification) =>
  EPHEMERAL.has(event.tipo) || !event.id;

// Ids an alert event talks about: one, or several when a bed backlog is grouped.
function alertIds(event: AppNotification): string[] {
  const datos = event.datos ?? {};
  const many = Array.isArray(datos.alerta_ids) ? datos.alerta_ids : [];
  const one = typeof datos.alerta_id === "string" ? [datos.alerta_id] : [];
  return [...new Set([...many, ...one].filter((id) => typeof id === "string"))];
}

export function useNotifications() {
  return useQuery({
    queryKey: NOTIFICATIONS_KEY,
    queryFn: async ({ signal }) => asList(await notificationsApi.list(signal)),
    // The bell is chrome, not content: it must not drive the freshness indicator.
    meta: { silent: true },
  });
}

/**
 * What a live event changes in the cache. Everything is a cache update, never
 * a navigation: the screens re-render by themselves, with no reload.
 */
export function applyLiveEvent(client: QueryClient, event: AppNotification) {
  const invalidate = (queryKey: readonly unknown[]) =>
    void client.invalidateQueries({ queryKey });

  if (event.tipo === "pendientes_actualizados") {
    const counters = countersFrom(event.datos);
    if (counters) client.setQueryData(PENDING_KEY, counters);
    // The reports inbox and the alert list follow the counters they came from.
    invalidate(["ai-reports"]);
    const cached = client.getQueryData<Alert[]>(ALERTS_KEY);
    if (counters && cached && cached.length !== counters.alertas_activas)
      invalidate(ALERTS_KEY);
    invalidate(REFERRALS_KEY);
    return;
  }
  // An automatic AI report was generated: it shows up in the patient drawer and
  // in the pending list with no reload, and the counters follow.
  if (event.tipo === "analisis_ia_pendiente") {
    invalidate(["ai-reports"]);
    invalidate(PENDING_KEY);
  }
  if (event.tipo.startsWith("alerta_")) {
    if (event.tipo === "alerta_reconocida") {
      // The banner clears at once; the refetch confirms it.
      const gone = alertIds(event);
      client.setQueryData<Alert[]>(ALERTS_KEY, (list) =>
        list ? list.filter((alert) => !gone.includes(alert.id)) : list,
      );
    }
    invalidate(ALERTS_KEY);
    invalidate(PENDING_KEY);
  }
  if (event.tipo.startsWith("remision_")) {
    invalidate(REFERRALS_KEY);
    invalidate(PENDING_KEY);
  }
  // A new invoice shows up on the accountant's screen without a reload.
  if (event.tipo === "factura_creada") {
    invalidate(INVOICES_KEY);
    invalidate(SUMMARY_KEY);
  }
  if (event.tipo === "cuenta_bloqueada") {
    // The accounts screen of the admin shows the new lock without a reload.
    invalidate(["accounts"]);
    invalidate(["audit"]);
  }
  if (event.paciente_id) invalidate(["patient-record", event.paciente_id]);
}

/**
 * Opens the live channel for the signed-in user and turns what arrives into
 * cache updates: the inbox, the counters, the alert banner, the referral
 * lists and the open patient record. Mounted once, by the app layout.
 */
export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const toast = useToast();

  // Adds notifications the cache does not know yet. Returns the new ones.
  const merge = useCallback(
    (incoming: AppNotification[]) => {
      const cached = client.getQueryData<AppNotification[]>(NOTIFICATIONS_KEY);
      if (!cached) return incoming;
      const known = new Set(cached.map((item) => item.id));
      const fresh = incoming.filter((item) => !known.has(item.id));
      if (fresh.length)
        client.setQueryData<AppNotification[]>(
          NOTIFICATIONS_KEY,
          [...fresh, ...cached].sort((a, b) => b.fecha.localeCompare(a.fecha)),
        );
      return fresh;
    },
    [client],
  );

  const announce = useCallback(
    (items: AppNotification[]) => {
      const role = client
        .getQueriesData<User>({ queryKey: ["session"] })
        .map(([, user]) => user?.role)
        .find(Boolean);
      items.forEach((item) => {
        // The persistent banner already shows a critical alert, and a toast at the
        // top right would cover its "Reconocer" button.
        if (
          item.tipo === "alerta_critica" &&
          role &&
          canSeeCriticalAlerts(role)
        )
          return;
        if (item.mensaje)
          toast(item.mensaje, item.tipo.startsWith("alerta_") ? "warn" : "info");
      });
    },
    [toast, client],
  );

  const state = useLiveEvents({
    enabled: true,
    onEvent: (event) => {
      if (!isEphemeral(event)) {
        announce(merge([event]));
        void client.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
      }
      applyLiveEvent(client, event);
    },
    onSync: () => {
      void client.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
      // After a reconnection, read what moved while the socket was down.
      void client.invalidateQueries({ queryKey: PENDING_KEY });
      void client.invalidateQueries({ queryKey: ALERTS_KEY });
      void client.invalidateQueries({ queryKey: REFERRALS_KEY });
    },
    onPoll: async () => {
      try {
        const unread = asList(await notificationsApi.unread());
        if (client.getQueryData(NOTIFICATIONS_KEY)) {
          const fresh = merge(unread);
          announce(fresh);
          fresh.forEach((item) => applyLiveEvent(client, item));
        } else {
          void client.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
        }
        // The counters and the banner are not in the inbox: refresh them too.
        void client.invalidateQueries({ queryKey: PENDING_KEY });
        void client.invalidateQueries({ queryKey: ALERTS_KEY });
      } catch {
        /* The next tick tries again; a 401 already ended the session. */
      }
    },
  });

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}
