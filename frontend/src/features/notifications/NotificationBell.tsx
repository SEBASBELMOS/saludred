import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { notificationsApi } from "../../api/notifications";
import type { AppNotification, Role } from "../../api/types";
import { Drawer } from "../../components/Drawer";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { dateTime } from "../../components/format";
import {
  Action,
  Loading,
  focus,
  interaction,
  linkButton,
  useToast,
} from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canOpenPatientFromInbox, menuFor } from "../auth/permissions";
import {
  NOTIFICATIONS_KEY,
  useLiveState,
  useNotifications,
} from "./LiveEvents";

const kinds: Record<string, string> = {
  fhir_observacion: "Resultado desde FHIR",
  alerta_critica: "Alerta crítica",
  alerta_escalada: "Alerta escalada",
  alerta_cama_sin_limpiar: "Cama libre sin limpiar",
  remision_creada: "Remisión recibida",
  remision_aceptada: "Remisión aceptada",
  remision_rechazada: "Remisión rechazada",
  remision_completada: "Remisión completada",
  cuenta_bloqueada: "Cuenta bloqueada",
};
const kindName = (tipo: string) =>
  kinds[tipo] ?? tipo.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
// Where an event is worked on, for the roles whose menu has that screen.
const destinations: Record<string, { to: string; label: string }> = {
  cuenta_bloqueada: { to: "/accounts?bloqueadas=1", label: "Ver cuentas" },
  alerta_cama_sin_limpiar: { to: "/capacity", label: "Ver centro de mando" },
  remision_creada: { to: "/referrals", label: "Ver remisiones" },
  remision_aceptada: { to: "/referrals", label: "Ver remisiones" },
  remision_rechazada: { to: "/referrals", label: "Ver remisiones" },
  remision_completada: { to: "/referrals", label: "Ver remisiones" },
};
export const destinationFor = (tipo: string, role: Role) => {
  const destination = destinations[tipo];
  return destination &&
    menuFor(role).some((item) => destination.to.startsWith(item.path))
    ? destination
    : null;
};
const MAX_SHOWN = 99;

// What the live channel is doing, said only when it differs from "everything normal".
export function LiveIndicator() {
  const state = useLiveState();
  if (state === "connecting") return null;
  const text =
    state === "live"
      ? "En vivo"
      : state === "reconnecting"
        ? "Reconectando…"
        : "Sin canal en vivo: se revisa cada 20 s";
  return (
    <span
      role="status"
      className="flex items-center gap-2 text-sm text-ink-muted"
    >
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ${state === "live" ? "bg-bed-available" : "bg-bed-cleaning"}`}
      />
      {text}
    </span>
  );
}

function useMarkRead() {
  const client = useQueryClient();
  const apply = (ids: string[]) =>
    client.setQueryData<AppNotification[]>(NOTIFICATIONS_KEY, (list) =>
      (list ?? []).map((item) =>
        ids.includes(item.id) ? { ...item, leida: true } : item,
      ),
    );
  return useMutation({
    mutationFn: async (ids: string[]) => {
      // The API marks one at a time; a loop over the unread is cheap enough.
      const done: string[] = [];
      try {
        for (const id of ids) {
          await notificationsApi.read(id);
          done.push(id);
        }
      } finally {
        apply(done);
      }
    },
  });
}

function NotificationItem({
  item,
  canOpen,
  destination,
  busy,
  onRead,
  onOpen,
  onGo,
}: {
  item: AppNotification;
  canOpen: boolean;
  destination: { to: string; label: string } | null;
  busy: boolean;
  onRead: () => void;
  onOpen: () => void;
  onGo: () => void;
}) {
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-2 text-xs text-ink-muted">
        <span>{kindName(item.tipo)}</span>
        <time dateTime={item.fecha}>{dateTime(item.fecha)}</time>
        {!item.leida && (
          <span className="rounded-full bg-accent-tint px-2 py-0.5 font-semibold text-accent-strong">
            Nueva
          </span>
        )}
      </div>
      <p
        className={`mt-1 break-words text-sm ${item.leida ? "" : "font-semibold"}`}
      >
        {item.mensaje}
      </p>
      {(canOpen || destination || !item.leida) && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {destination && (
            <button
              type="button"
              className={`${linkButton} py-1 text-sm max-sm:min-h-11`}
              onClick={onGo}
            >
              {destination.label}
            </button>
          )}
          {canOpen && (
            <button
              type="button"
              className={`${linkButton} py-1 text-sm max-sm:min-h-11`}
              onClick={onOpen}
            >
              Ver paciente
            </button>
          )}
          {!item.leida && (
            <button
              type="button"
              disabled={busy}
              className={`${linkButton} py-1 text-sm disabled:opacity-50 max-sm:min-h-11`}
              onClick={onRead}
            >
              Marcar como leída
            </button>
          )}
        </div>
      )}
    </li>
  );
}

function NotificationPanel({
  onClose,
  list,
}: {
  onClose: () => void;
  list: ReturnType<typeof useNotifications>;
}) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const mark = useMarkRead();
  const items = list.data ?? [];
  const unread = items.filter((item) => !item.leida);
  const canOpen = !!user && canOpenPatientFromInbox(user.role);
  const failed = () =>
    toast("No se pudo marcar la notificación. Intenta de nuevo.", "error");
  return (
    <Drawer
      visible
      onClose={onClose}
      title="Notificaciones"
      label="Bandeja de notificaciones"
      footer={
        unread.length > 1 ? (
          <Action
            secondary
            label="Marcar todas como leídas"
            loading={mark.isPending}
            disabled={mark.isPending}
            onClick={() =>
              mark.mutate(
                unread.map((item) => item.id),
                { onError: failed },
              )
            }
          />
        ) : undefined
      }
    >
      {list.error ? (
        <ErrorState error={list.error} retry={list.refetch} />
      ) : !list.data ? (
        <Loading />
      ) : items.length ? (
        <ul
          className="divide-y divide-line"
          aria-label="Notificaciones recientes"
        >
          {items.map((item) => (
            <NotificationItem
              key={item.id}
              item={item}
              canOpen={canOpen && !!item.paciente_id}
              destination={user ? destinationFor(item.tipo, user.role) : null}
              busy={mark.isPending}
              onRead={() => mark.mutate([item.id], { onError: failed })}
              onGo={() => {
                const destination = destinationFor(item.tipo, user!.role);
                if (!destination) return;
                if (!item.leida) mark.mutate([item.id]);
                onClose();
                navigate(destination.to);
              }}
              onOpen={() => {
                if (!item.leida) mark.mutate([item.id]);
                onClose();
                navigate(`/beds?patient=${item.paciente_id}`);
              }}
            />
          ))}
        </ul>
      ) : (
        <EmptyState title="No tienes notificaciones.">
          Aquí aparecen los avisos que lleguen para ti.
        </EmptyState>
      )}
    </Drawer>
  );
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const list = useNotifications();
  const unread = (list.data ?? []).filter((item) => !item.leida).length;
  const shown = unread > MAX_SHOWN ? `${MAX_SHOWN}+` : String(unread);
  return (
    <>
      <button
        type="button"
        aria-label={
          unread
            ? `Notificaciones, ${unread} sin leer`
            : "Notificaciones, sin avisos nuevos"
        }
        aria-haspopup="dialog"
        className={`relative inline-flex min-h-9 w-9 items-center justify-center rounded-lg border border-line-strong bg-surface text-accent-strong hov:bg-accent-tint max-sm:min-h-11 max-sm:w-11 ${interaction} ${focus}`}
        onClick={() => setOpen(true)}
      >
        <i className="pi pi-bell text-sm" aria-hidden="true" />
        {unread > 0 && (
          <span
            data-testid="unread-count"
            aria-hidden="true"
            className="absolute -right-1.5 -top-1.5 min-w-5 rounded-full bg-accent px-1 text-center text-xs font-semibold leading-5 text-white tabular-nums"
          >
            {shown}
          </span>
        )}
      </button>
      {open && <NotificationPanel list={list} onClose={() => setOpen(false)} />}
    </>
  );
}
