import { useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useNetwork, useQueue } from "../../api/query";
import type { QueuedRequest, OrganizationCapacity } from "../../api/types";
import { useAuth } from "../auth/AuthProvider";
import { canAssign, canCreate } from "../auth/permissions";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { Filters } from "../../components/Filters";
import { PriorityBadge, priorities } from "../../components/PriorityBadge";
import {
  Action,
  DataTable,
  buttonFocus,
  buttonLooks,
  buttonShape,
  focus,
  interaction,
  Loading,
  Panel,
} from "../../components/ui";
import { minutes, normalize } from "../../components/format";
import { AssignDialog } from "./AssignDialog";
import { criticalityPercent } from "./priority";
export function QueueTable({
  requests,
  organizations,
  assign,
  onAssign,
}: {
  requests: QueuedRequest[];
  organizations: OrganizationCapacity[];
  assign: boolean;
  onAssign: (id: string) => void;
}) {
  if (!requests.length)
    return (
      <EmptyState title="No hay solicitudes para mostrar">
        Prueba otros filtros o actualiza la vista.
      </EmptyState>
    );
  return (
    <DataTable
      stack
      caption="Cola de asignación"
      numeric={[3, 4]}
      headers={[
        "Prioridad",
        "Paciente",
        "Servicio e institución",
        "Criticidad",
        "Espera",
        "Acción",
      ]}
    >
      {requests.map((r, index) => (
        <tr key={r.id}>
          <td>
            <PriorityBadge priority={r.priority} />
            <small className="mt-0.5 block text-ink-muted">
              Puesto {r.queue_position}
            </small>
          </td>
          <td>
            <strong>{r.patient_name}</strong>
            <small className="block text-ink-muted">{r.patient_document}</small>
          </td>
          <td>
            {r.required_service}
            <small className="block text-ink-muted">
              {
                organizations.find(
                  (o) =>
                    o.organization_id ===
                    (r.target_organization_id || r.requesting_organization_id),
                )?.organization_name
              }
            </small>
          </td>
          <td className="tabular-nums">
            {criticalityPercent(r.criticality_score)}
          </td>
          <td className="tabular-nums">{minutes(r.waiting_minutes)}</td>
          <td>
            {assign ? (
              // Only the head of the queue is the primary action: the others are
              // quiet so the wrong request is not assigned by habit.
              <Action
                label="Asignar cama"
                secondary={index > 0}
                data-primary-assign={index === 0 || undefined}
                aria-label={`Asignar cama a ${r.patient_name}`}
                onClick={() => onAssign(r.id)}
              />
            ) : (
              <Link
                to={`/patients?patient=${r.patient_id}`}
                className={`font-semibold text-accent ${focus}`}
              >
                Ver ficha
              </Link>
            )}
          </td>
        </tr>
      ))}
    </DataTable>
  );
}
export function QueuePage() {
  const network = useNetwork();
  const queue = useQueue();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const table = useRef<HTMLDivElement>(null);
  const refocus = useRef(false);
  // After an assignment the row is gone: focus the new head of the queue.
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    const next = table.current?.querySelector<HTMLElement>(
      "[data-primary-assign]",
    );
    (next ?? document.getElementById("main"))?.focus();
  }, [queue.data]);
  if (network.error || queue.error)
    return (
      <ErrorState
        error={(network.error || queue.error)!}
        retry={() => Promise.all([network.refetch(), queue.refetch()])}
      />
    );
  if (!network.data || !queue.data) return <Loading />;
  const list = queue.data.filter(
    (r) =>
      (!params.get("org") ||
        (r.target_organization_id || r.requesting_organization_id) ===
          params.get("org")) &&
      (!params.get("service") ||
        r.required_service === params.get("service")) &&
      (!params.get("priority") || r.priority === params.get("priority")) &&
      normalize(`${r.patient_name} ${r.patient_document}`).includes(
        normalize(params.get("search") || ""),
      ),
  );
  const request = queue.data.find((r) => r.id === params.get("assign"));
  const select = (id?: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id) next.set("assign", id);
      else next.delete("assign");
      return next;
    });
  return (
    <>
      <Filters
        organizations={network.data.network.organizations}
        services={[
          ...new Set(network.data.beds.map((b) => b.service || "Sin servicio")),
        ]}
        searchLabel="Buscar paciente o documento"
        extra={{ key: "priority", label: "Prioridad", values: priorities }}
      />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-muted">
          Primero la prioridad clínica; a igual prioridad, la mayor criticidad
          (la solicitud sin puntaje va después); y a igual criticidad, quien
          lleva más tiempo esperando.
          {user!.role === "SPECIALIST" &&
            " Consulta de solo lectura, de tu IPS."}
        </p>
        {canCreate(user!.role) && (
          <Link
            className={`${buttonShape} ${interaction} ${buttonFocus} ${buttonLooks.primary}`}
            to="/patients?request=new"
          >
            Nueva solicitud
          </Link>
        )}
      </div>
      <Panel
        title={`${list.length} solicitudes encontradas`}
        description="Saltar el orden exige registrar un motivo."
      >
        <div ref={table}>
          <QueueTable
            requests={list}
            organizations={network.data.network.organizations}
            assign={canAssign(user!.role)}
            onAssign={select}
          />
        </div>
      </Panel>
      {request && canAssign(user!.role) && (
        <AssignDialog
          key={request.id}
          request={request}
          queue={queue.data}
          onClose={() => select()}
          onSelect={select}
          onAssigned={() => (refocus.current = true)}
        />
      )}
    </>
  );
}
