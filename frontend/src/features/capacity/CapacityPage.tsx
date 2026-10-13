import { useState } from "react";
import { Link } from "react-router-dom";
import { useNetwork, useOverdueCleaning, useQueue } from "../../api/query";
import type { Alert } from "../../api/alerts";
import type { NetworkSnapshot } from "../../api/capacity";
import type { OverdueCleaningBed, QueuedRequest } from "../../api/types";
import { useAuth } from "../auth/AuthProvider";
import { canAssign, canReadAlerts } from "../auth/permissions";
import { AcknowledgeForm } from "../alerts/AcknowledgeForm";
import { useActiveAlerts } from "../alerts/useAlerts";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { PriorityBadge } from "../../components/PriorityBadge";
import {
  DataTable,
  focus,
  linkButton,
  Loading,
  Section,
} from "../../components/ui";
import { minutes } from "../../components/format";
import { BedTile, BedTooltip } from "../census/BedTile";
import { StatusStrip } from "../census/StatusStrip";
import { AvailabilityAnnouncer } from "../census/AvailabilityAnnouncer";
import { useRovingTiles } from "../census/roving";
import {
  buildAttention,
  restOfQueue,
  type Attention,
  type AttentionItem,
} from "./attention";

const link = `rounded font-semibold text-accent underline-offset-2 hov:underline ${focus}`;
const visibleItems = 5;
type Level = "critical" | "cleaning" | "attention" | "neutral";
const levels: Record<Level, { text: string; icon: string; word: string }> = {
  critical: {
    text: "text-critical-ink",
    icon: "pi-exclamation-circle text-critical",
    word: "Crítico",
  },
  // A free bed nobody cleaned is not an emergency: the ochre of the cleaning state, never red.
  cleaning: {
    text: "text-bed-cleaning-ink",
    icon: "pi-sync text-bed-cleaning",
    word: "En limpieza",
  },
  attention: {
    text: "text-urgent-ink",
    icon: "pi-exclamation-triangle text-urgent",
    word: "Atención",
  },
  neutral: {
    text: "text-ink-muted",
    icon: "pi-info-circle text-ink-muted",
    word: "",
  },
};
function AttentionGroup({
  title,
  level,
  items,
}: {
  title: string;
  level: Level;
  items: AttentionItem[];
}) {
  // Which bed alert is being acknowledged (a note is required, so it opens a form).
  const [acknowledging, setAcknowledging] = useState<string | null>(null);
  if (!items.length) return null;
  const tone = levels[level];
  return (
    <section aria-label={title}>
      <h3
        className={`flex items-center gap-2 text-sm font-semibold ${tone.text}`}
      >
        <i className={`pi text-sm ${tone.icon}`} aria-hidden="true" />
        {title}
        <span className="font-semibold tabular-nums">{items.length}</span>
        {tone.word && (
          <span className="ml-auto text-xs font-semibold">{tone.word}</span>
        )}
      </h3>
      <ul className="mt-1 divide-y divide-line border-y border-line">
        {items.slice(0, visibleItems).map((item) => (
          <li key={item.key} className="py-2 text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <strong className="min-w-0 break-words font-semibold">
                {item.title}
              </strong>
              {item.minutes !== undefined && (
                <span className="shrink-0 tabular-nums">
                  {minutes(item.minutes)}
                </span>
              )}
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 break-words text-xs text-ink-muted">
                {item.detail}
              </span>
              <span className="flex shrink-0 items-baseline gap-3">
                {item.alertId && acknowledging !== item.alertId && (
                  <button
                    type="button"
                    className={`text-sm ${linkButton}`}
                    aria-label={`Reconocer la alerta de ${item.title}`}
                    onClick={() => setAcknowledging(item.alertId!)}
                  >
                    Reconocer
                  </button>
                )}
                <Link className={`text-sm ${link}`} to={item.to}>
                  {item.action}
                </Link>
              </span>
            </div>
            {item.alertId && acknowledging === item.alertId && (
              <AcknowledgeForm
                alert={{ id: item.alertId }}
                onClose={() => setAcknowledging(null)}
              />
            )}
          </li>
        ))}
      </ul>
      {items.length > visibleItems && (
        <p className="pt-1.5 text-sm text-ink-muted">
          Y {items.length - visibleItems} más.{" "}
          <Link className={link} to={level === "critical" ? "/queue" : "/beds"}>
            {level === "critical" ? "Ver la cola" : "Ver las camas"}
          </Link>
        </p>
      )}
    </section>
  );
}
function AttentionList({ attention }: { attention: Attention }) {
  if (!attention.total)
    return (
      <p className="border-y border-line py-4 text-sm text-ink-muted">
        No hay nada urgente ahora
      </p>
    );
  return (
    <div className="space-y-5">
      <AttentionGroup
        title="Pacientes esperando"
        level="critical"
        items={attention.waiting}
      />
      <AttentionGroup
        title="Libres, pero sin limpiar"
        level="cleaning"
        items={attention.unattended}
      />
      <AttentionGroup
        title="Camas atrasadas en limpieza"
        level="attention"
        items={attention.cleaning}
      />
      <AttentionGroup
        title="IPS sin camas libres"
        level="neutral"
        items={attention.full}
      />
    </div>
  );
}
function RestOfQueue({
  rest,
  total,
}: {
  rest: QueuedRequest[];
  total: number;
}) {
  if (!rest.length)
    return total ? (
      <p className="border-y border-line py-4 text-sm text-ink-muted">
        Todas las solicitudes pendientes están en la lista de arriba.
      </p>
    ) : (
      <EmptyState title="No hay solicitudes pendientes">
        Las nuevas solicitudes aparecen aquí cuando una IPS las registra.
      </EmptyState>
    );
  return (
    <ul className="divide-y divide-line border-y border-line">
      {rest.slice(0, visibleItems).map((r) => (
        <li key={r.id} className="py-2 text-sm">
          <div className="flex items-baseline justify-between gap-3">
            <strong className="min-w-0 break-words font-semibold">
              {r.patient_name}
            </strong>
            <span className="shrink-0 tabular-nums">
              {minutes(r.waiting_minutes)}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-2">
            <PriorityBadge priority={r.priority} />
            <span className="min-w-0 break-words text-xs text-ink-muted">
              {r.required_service}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}
function CensusBoard({
  snapshot,
  since,
}: {
  snapshot: NetworkSnapshot;
  since: Map<string, number>;
}) {
  const { network, beds } = snapshot;
  const groups = network.organizations.map((org) => {
    const list = beds.filter((b) => b.organization_id === org.organization_id);
    const services = [
      ...new Set(list.map((b) => b.service || "Sin servicio")),
    ].sort();
    return {
      org,
      list,
      rows: services.map((service) => ({
        service,
        beds: list.filter((b) => (b.service || "Sin servicio") === service),
      })),
    };
  });
  const order = groups.flatMap((g) => g.rows.flatMap((r) => r.beds));
  const { boardProps, tileProps } = useRovingTiles(order.map((b) => b.id));
  return (
    <div className="space-y-6" {...boardProps}>
      {groups.map(({ org, list, rows }) => {
        const free = list.filter((b) => b.status === "AVAILABLE").length;
        return (
          <article key={org.organization_id}>
            <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-4">
              <h3 className="text-base font-semibold">
                {org.organization_name}
                <span className="ml-2 text-xs font-normal text-ink-muted">
                  {org.organization_code}
                </span>
              </h3>
              <span
                className={`text-sm font-semibold ${free ? "text-bed-available-ink" : "text-urgent-ink"}`}
              >
                {free
                  ? `${free} ${free === 1 ? "libre" : "libres"}`
                  : "Sin camas libres"}
              </span>
            </div>
            <div className="divide-y divide-line border-y border-line">
              {rows.map(({ service, beds: row }) => (
                <div
                  key={service}
                  className="grid gap-x-3 gap-y-1.5 py-2.5 sm:grid-cols-[8.5rem_minmax(0,1fr)] sm:items-center"
                >
                  <span className="text-sm text-ink-muted">{service}</span>
                  <div className="grid grid-cols-4 gap-2 sm:grid-cols-[repeat(8,minmax(44px,56px))] sm:gap-1.5">
                    {row.map((bed) => (
                      <BedTile
                        key={bed.id}
                        bed={bed}
                        fluid
                        since={since.get(bed.id)}
                        to={`/beds?bed=${bed.id}`}
                        {...tileProps(bed.id)}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </article>
        );
      })}
    </div>
  );
}
export function CapacityView({
  snapshot,
  queue,
  assign,
  overdue = [],
  bedAlerts = [],
}: {
  snapshot: NetworkSnapshot;
  queue: QueuedRequest[];
  assign: boolean;
  overdue?: OverdueCleaningBed[];
  // Active "está libre, pero no ha sido limpiada" alerts (R20).
  bedAlerts?: Alert[];
}) {
  const { network, beds } = snapshot;
  const rate = (occupied: number, total: number) =>
    total ? Math.round((occupied / total) * 100) : 0;
  const services = [
    ...new Set(beds.map((b) => b.service || "Sin servicio")),
  ].sort();
  const attention = buildAttention(snapshot, queue, overdue, assign, bedAlerts);
  const rest = restOfQueue(queue, attention);
  // The API only tells when a bed entered a state for beds in overdue cleaning.
  const since = new Map(
    overdue.map((o) => [o.location_id, o.minutes_in_cleaning]),
  );
  return (
    <div className="space-y-8">
      <BedTooltip />
      <AvailabilityAnnouncer beds={beds} />
      <StatusStrip beds={beds} />
      <div className="grid min-w-0 gap-x-10 gap-y-8 xl:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="min-w-0 space-y-8 xl:col-start-2 xl:row-start-1">
          <Section title="Requiere atención">
            <AttentionList attention={attention} />
          </Section>
          <Section
            title="Resto de la cola"
            action={
              <Link className={`text-sm ${link}`} to="/queue">
                Ver cola completa
              </Link>
            }
          >
            <RestOfQueue rest={rest} total={queue.length} />
          </Section>
        </div>
        <div className="min-w-0 xl:col-start-1 xl:row-start-1">
          <Section
            title="Pizarra de censo"
            description="Una ficha por cama. Con el teclado, usa las flechas para moverte y Enter para abrir la cama."
          >
            {!network.organizations.length && (
              <EmptyState title="No hay instituciones visibles para tu cuenta" />
            )}
            <CensusBoard snapshot={snapshot} since={since} />
          </Section>
          <div className="mt-8">
            <Section
              title="Capacidad por servicio"
              description="Disponibilidad según la atención requerida"
            >
              <div>
                <DataTable
                  framed
                  numeric={[1, 2, 3, 4]}
                  caption="Disponibilidad y ocupación por servicio"
                  headers={[
                    "Servicio",
                    "Total",
                    "Disponibles",
                    "Ocupadas",
                    "Ocupación",
                    "Detalle",
                  ]}
                >
                  {services.map((service) => {
                    const list = beds.filter(
                      (b) => (b.service || "Sin servicio") === service,
                    );
                    const occupied = list.filter(
                      (b) => b.status === "OCCUPIED",
                    ).length;
                    return (
                      <tr key={service}>
                        <td className="font-semibold">{service}</td>
                        <td className="text-right">{list.length}</td>
                        <td className="text-right font-semibold text-bed-available-ink">
                          {list.filter((b) => b.status === "AVAILABLE").length}
                        </td>
                        <td className="text-right">{occupied}</td>
                        <td className="text-right">
                          {rate(occupied, list.length)} %
                        </td>
                        <td>
                          <Link
                            className={link}
                            to={`/beds?service=${encodeURIComponent(service)}`}
                          >
                            Ver camas
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </DataTable>
              </div>
            </Section>
          </div>
        </div>
      </div>
    </div>
  );
}
export function CapacityPage() {
  const network = useNetwork();
  const queue = useQueue();
  const { user } = useAuth();
  const assign = canAssign(user!.role);
  // The overdue-cleaning endpoint is optional: any failure hides that group.
  const overdue = useOverdueCleaning(assign);
  // Bed alerts reach the coordinator, the admin and the medico of the IPS; they update live.
  const alerts = useActiveAlerts(canReadAlerts(user!.role));
  const bedAlerts = (alerts.data ?? []).filter(
    (alert) => alert.kind === "cama_sin_limpiar",
  );
  if (network.error || queue.error)
    return (
      <ErrorState
        error={(network.error || queue.error)!}
        retry={() => Promise.all([network.refetch(), queue.refetch()])}
      />
    );
  if (!network.data || !queue.data) return <Loading variant="cards" />;
  return (
    <CapacityView
      snapshot={network.data}
      queue={queue.data}
      assign={assign}
      overdue={overdue.data}
      bedAlerts={bedAlerts}
    />
  );
}
