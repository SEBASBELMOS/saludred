import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Outlet, useLocation } from "react-router-dom";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { Sidebar } from "primereact/sidebar";
import { Tooltip } from "primereact/tooltip";
import type { Role } from "../api/types";
import { useAuth } from "../features/auth/AuthProvider";
import { canReviewAi, menuFor, roleNames } from "../features/auth/permissions";
import { usePending } from "../features/alerts/useAlerts";
import { CriticalAlertBanner } from "../features/alerts/CriticalAlertBanner";
import { Action, focus, useToast, interaction } from "./ui";
import { useSearchShortcut } from "./shortcuts";
import { drawerTimeout, drawerTransition, useReducedMotion } from "./motion";
import { LiveEventsProvider } from "../features/notifications/LiveEvents";
import {
  LiveIndicator,
  NotificationBell,
} from "../features/notifications/NotificationBell";
export function RoleMenu({
  role,
  onNavigate,
}: {
  role: Role;
  onNavigate?: () => void;
}) {
  // R18 counters, pushed by the live channel: one source for every badge.
  const pending = usePending(canReviewAi(role));
  const waiting = pending.data?.reportes_por_revisar ?? 0;
  const referrals = pending.data?.remisiones_recibidas ?? 0;
  return (
    <nav aria-label="Navegación principal" className="space-y-0.5">
      {menuFor(role).map((item) => (
        <NavLink
          onClick={onNavigate}
          key={item.path}
          to={item.path}
          className={({ isActive }) =>
            `flex min-h-9 items-center gap-3 rounded-lg px-3 text-sm font-semibold max-lg:min-h-11 ${focus} ${isActive ? "border border-line bg-surface font-bold text-accent-strong" : "border border-transparent text-ink hov:bg-line/60"}`
          }
        >
          <i className={`pi ${item.icon} w-4 text-center`} aria-hidden="true" />
          {item.label}
          {item.reviewBadge && waiting > 0 && (
            <span className="ml-auto rounded-full bg-bed-cleaning-tint px-2 py-0.5 text-xs font-semibold text-bed-cleaning-ink tabular-nums">
              {waiting}
              <span className="sr-only">
                {waiting === 1
                  ? " reporte de IA por revisar"
                  : " reportes de IA por revisar"}
              </span>
            </span>
          )}
          {item.referralBadge && referrals > 0 && (
            <span className="ml-auto rounded-full bg-bed-cleaning-tint px-2 py-0.5 text-xs font-semibold text-bed-cleaning-ink tabular-nums">
              {referrals}
              <span className="sr-only">
                {referrals === 1
                  ? " remisión por responder"
                  : " remisiones por responder"}
              </span>
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
// How fresh the data on screen is: green under 30 s, ochre under 2 min, orange after that.
function useFreshness(updated: number, offline: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (offline) return { text: "Sin conexión", dot: "bg-bed-blocked" };
  if (!updated) return null;
  const seconds = Math.max(0, Math.floor((now - updated) / 1000));
  const text =
    seconds < 60
      ? `Actualizado hace ${seconds} s`
      : seconds < 3600
        ? `Actualizado hace ${Math.floor(seconds / 60)} min`
        : `Actualizado hace ${Math.floor(seconds / 3600)} h`;
  const dot =
    seconds < 30
      ? "bg-bed-available"
      : seconds < 120
        ? "bg-bed-cleaning"
        : "bg-urgent";
  return { text, dot };
}
export function AppLayout() {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [drawer, setDrawer] = useState(false);
  const main = useRef<HTMLElement>(null);
  const client = useQueryClient();
  const fetching = useIsFetching();
  const toast = useToast();
  const reduced = useReducedMotion();
  useSearchShortcut();
  const active = client
    .getQueryCache()
    .getAll()
    .filter(
      (query) =>
        query.isActive() &&
        query.queryKey[0] !== "session" &&
        !query.meta?.silent,
    );
  const updated = Math.max(0, ...active.map((q) => q.state.dataUpdatedAt));
  const fresh = useFreshness(
    updated,
    active.some((q) => q.state.status === "error"),
  );
  useEffect(() => {
    main.current?.focus();
    window.scrollTo?.(0, 0);
  }, [location.pathname]);
  if (!user) return <Navigate to="/login" replace />;
  const current = menuFor(user.role).find((item) =>
    location.pathname.startsWith(item.path),
  );
  const menu = (
    <>
      <div className="mb-6 flex items-center gap-2.5 px-3">
        <img src="/logo.svg" alt="SaludRed" className="h-9 w-9" />
        <span className="font-display text-xl font-semibold">
          salud<span className="font-sans font-normal">red</span>
        </span>
      </div>
      <RoleMenu role={user.role} onNavigate={() => setDrawer(false)} />
      <div className="mt-auto break-words border-t border-line px-3 pt-4">
        <strong className="block text-sm">{user.full_name}</strong>
        <span className="block text-meta text-ink-muted">
          {roleNames[user.role]}
        </span>
        <button
          className={`mt-2 -ml-2 rounded-lg px-2 py-1 text-sm font-semibold text-accent-strong hov:bg-accent-tint ${interaction} ${focus}`}
          onClick={logout}
        >
          Cerrar sesión
        </button>
      </div>
    </>
  );
  return (
    <LiveEventsProvider>
      <div className="flex min-h-dvh min-w-0">
        <Tooltip
          target=".refresh-view"
          showDelay={300}
          pt={{
            root: { className: "absolute z-[1300] animate-tip" },
            text: {
              className:
                "rounded-lg bg-ink px-2.5 py-1.5 text-xs font-semibold text-white shadow-overlay",
            },
            arrow: { className: "hidden" },
          }}
        />
        <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-2 overflow-y-auto border-r border-line bg-surface-sunken p-4 lg:flex">
          {menu}
        </aside>
        <Sidebar
          visible={drawer}
          onHide={() => setDrawer(false)}
          position="left"
          className="w-72 max-w-[90vw]"
          transitionOptions={{
            classNames: drawerTransition(reduced, "left"),
            timeout: drawerTimeout(reduced),
          }}
          pt={{
            root: {
              className:
                "relative flex h-full w-72 max-w-[90vw] flex-col bg-surface-sunken text-ink shadow-overlay",
            },
            mask: { className: "flex bg-ink/40" },
            header: { className: "flex items-center justify-end p-2" },
            content: { className: "flex h-full flex-col gap-2 p-4 pt-0" },
            closeButton: {
              "aria-label": "Cerrar menú",
              className: `grid h-11 w-11 place-items-center rounded-lg text-accent-strong hov:bg-accent-tint ${focus} ${interaction}`,
            },
          }}
          ptOptions={{ mergeSections: false, mergeProps: false }}
        >
          {menu}
        </Sidebar>
        <div className="min-w-0 flex-1">
          <CriticalAlertBanner />
          <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2 lg:hidden">
            <Action
              secondary
              icon="pi pi-bars"
              aria-label="Abrir menú"
              onClick={() => setDrawer(true)}
            />
            <span className="font-display text-xl font-semibold">
              salud<span className="font-sans font-normal">red</span>
            </span>
          </header>
          <main
            ref={main}
            id="main"
            tabIndex={-1}
            className="mx-auto min-w-0 max-w-[1560px] px-4 py-5 outline-none sm:px-8 sm:py-6"
          >
            <div className="mb-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <h1 className="font-display text-2xl font-semibold">
                {current?.label}
              </h1>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <LiveIndicator />
                <span
                  role="status"
                  className="flex items-center gap-2 text-sm text-ink-muted"
                >
                  {fetching ? (
                    "Actualizando…"
                  ) : fresh ? (
                    <>
                      <span
                        aria-hidden="true"
                        className={`h-2 w-2 rounded-full ${fresh.dot}`}
                      />
                      {fresh.text}
                    </>
                  ) : null}
                </span>
                <NotificationBell />
                <Action
                  secondary
                  icon="pi pi-refresh"
                  aria-label="Actualizar vista"
                  data-pr-tooltip="Actualizar vista"
                  data-pr-position="left"
                  className="refresh-view !w-9 !px-0 max-sm:!w-11"
                  disabled={fetching > 0}
                  onClick={async () => {
                    try {
                      await client.refetchQueries(
                        {
                          type: "active",
                          predicate: (query) =>
                            query.queryKey[0] !== "session" &&
                            !query.meta?.silent,
                        },
                        { throwOnError: true },
                      );
                      toast("Datos actualizados");
                    } catch {
                      toast(
                        "No se pudo actualizar toda la vista. Revisa los mensajes de error.",
                        "error",
                      );
                    }
                  }}
                />
              </div>
            </div>
            <Outlet />
          </main>
        </div>
      </div>
    </LiveEventsProvider>
  );
}
