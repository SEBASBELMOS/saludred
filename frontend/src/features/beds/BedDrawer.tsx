import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Drawer } from "../../components/Drawer";
import type { Bed, BedStatus, Role } from "../../api/types";
import { bedsApi } from "../../api/beds";
import { useInvalidateClinical } from "../../api/query";
import { canAssign, canChangeBed } from "../auth/permissions";
import { StatusBadge, statusNames } from "../../components/StatusBadge";
import {
  Action,
  FormActions,
  Loading,
  Notice,
  SelectField,
  TextField,
  options,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { dateTime, minutes } from "../../components/format";
import { CleaningSection } from "../cleaning/CleaningSection";
import {
  bedBlockedByEvidence,
  evidenceBlockedMessage,
} from "../cleaning/cleaning";
export function BedStateForm({
  bed,
  role,
  pending,
  onChange,
  onRelease,
  onClose,
}: {
  bed: Bed;
  role: Role;
  pending: boolean;
  onChange: (status: BedStatus, reason: string) => void;
  onRelease: () => void;
  onClose: () => void;
}) {
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  if (bed.status === "OCCUPIED")
    return (
      <div>
        <p className="my-4 text-sm text-ink-muted">
          Cama con un paciente asignado. Al liberarla pasa a limpieza; no queda
          disponible hasta que se registre el alistamiento.
        </p>
        {canAssign(role) ? (
          <Action
            label="Liberar y enviar a limpieza"
            loading={pending}
            disabled={pending}
            onClick={onRelease}
          />
        ) : (
          <p className="text-sm text-ink-muted">
            La liberación corresponde a la coordinación EPS.
          </p>
        )}
      </div>
    );
  if (!canChangeBed(role))
    return (
      <p className="my-4 text-sm text-ink-muted">
        Tu rol consulta el estado de las camas. El cambio de estado lo hace la
        operación de la IPS o la coordinación EPS.
      </p>
    );
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (status && reason.trim())
          onChange(status as BedStatus, reason.trim());
      }}
      className="mt-4 space-y-4"
    >
      <SelectField
        label="Nuevo estado"
        value={status}
        onChange={(event) => setStatus(event.target.value)}
        required
      >
        <option value="">Selecciona un estado</option>
        {options(
          Object.fromEntries(
            Object.entries(statusNames).filter(
              ([key]) => key !== "OCCUPIED" && key !== bed.status,
            ),
          ),
        )}
      </SelectField>
      <TextField
        label="Motivo del cambio"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        required
        maxLength={300}
        rows={3}
      />
      <p className="text-xs text-ink-muted">
        Una cama se ocupa asignando una solicitud desde la cola, no cambiando su
        estado.
      </p>
      {bed.status === "CLEANING" && (
        <p className="text-xs text-ink-muted">
          Para dejarla disponible hace falta una limpieza aprobada. Revisa la
          sección «Evidencia de limpieza» más arriba.
        </p>
      )}
      <FormActions
        label="Guardar cambio"
        pending={pending}
        disabled={!status || !reason.trim()}
        onClose={onClose}
      />
    </form>
  );
}
function BedDetail({
  bed,
  organization,
  role,
  onClose,
}: {
  bed: Bed;
  organization: string;
  role: Role;
  onClose: () => void;
}) {
  const history = useQuery({
    queryKey: ["bed-history", bed.id],
    queryFn: ({ signal }) => bedsApi.history(bed.id, signal),
  });
  const invalidate = useInvalidateClinical();
  const toast = useToast();
  const mutation = useMutation({
    mutationFn: async (change: { status: BedStatus; reason: string } | null) =>
      change
        ? bedsApi.change(bed.id, change.status, change.reason)
        : bedsApi.release(bed.id),
    onSuccess: async (_, change) => {
      toast(
        `${bed.name}: ${change ? statusNames[change.status] : "enviada a limpieza"}`,
      );
      onClose();
      await invalidate();
    },
  });
  const events = history.data
    ? [...history.data].sort((a, b) => b.event_at.localeCompare(a.event_at))
    : [];
  const since = events.find((event) => event.new_status === bed.status);
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusBadge status={bed.status} />
        {since && (
          <span className="text-sm text-ink-muted">
            Desde hace{" "}
            <span className="font-semibold text-ink">
              {minutes(
                (Date.now() - new Date(since.event_at).getTime()) / 60000,
              )}
            </span>
          </span>
        )}
      </div>
      <dl className="mt-4 divide-y divide-line border-y border-line text-sm">
        {[
          ["Institución", organization],
          ["Servicio", bed.service || "Sin servicio"],
          ["Código", bed.code],
        ].map(([term, value]) => (
          <div key={term} className="flex justify-between gap-4 py-2">
            <dt className="text-ink-muted">{term}</dt>
            <dd className="break-words text-right font-semibold">{value}</dd>
          </div>
        ))}
      </dl>
      {mutation.error && (
        <Notice>
          {bedBlockedByEvidence(mutation.error)
            ? evidenceBlockedMessage
            : mutation.error.message}
        </Notice>
      )}
      {/* In cleaning, the evidence is the way out: it comes before the manual state change. */}
      {bed.status === "CLEANING" && (
        <CleaningSection
          bed={bed}
          role={role}
          entry={events.find((event) => event.new_status === "CLEANING")}
        />
      )}
      <BedStateForm
        bed={bed}
        role={role}
        pending={mutation.isPending}
        onChange={(status, reason) => mutation.mutate({ status, reason })}
        onRelease={() => mutation.mutate(null)}
        onClose={onClose}
      />
      <section className="mt-6 border-t border-line pt-4">
        <h3 className="text-base font-semibold">Últimos movimientos</h3>
        {history.error ? (
          <ErrorState error={history.error} retry={history.refetch} />
        ) : !history.data ? (
          <Loading />
        ) : events.length ? (
          <ol className="mt-2 divide-y divide-line">
            {events.slice(0, 4).map((event) => (
              <li key={event.id} className="py-2 text-sm">
                <time className="block text-xs text-ink-muted">
                  {dateTime(event.event_at)}
                </time>
                <p>
                  {event.previous_status
                    ? `${statusNames[event.previous_status]} a `
                    : "Registro inicial: "}
                  <strong>{statusNames[event.new_status]}</strong>
                </p>
                {event.reason && (
                  <p className="break-words text-ink-muted">{event.reason}</p>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-3 text-sm text-ink-muted">
            Sin movimientos registrados.
          </p>
        )}
      </section>
    </>
  );
}
// Detail of one bed in a right-side panel: quicker to open and close than a modal.
export function BedDrawer({
  bed,
  organization,
  role,
  onClose,
}: {
  bed: Bed | undefined;
  organization: string;
  role: Role;
  onClose: () => void;
}) {
  // Keep the last bed while the panel slides out.
  const last = useRef<Bed>();
  if (bed) last.current = bed;
  const shown = bed ?? last.current;
  const lastOrganization = useRef(organization);
  if (bed) lastOrganization.current = organization;
  return (
    <Drawer
      visible={!!bed}
      onClose={onClose}
      title={shown?.name}
      label="Detalle de la cama"
    >
      {shown && (
        <BedDetail
          key={shown.id}
          bed={shown}
          organization={lastOrganization.current}
          role={role}
          onClose={onClose}
        />
      )}
    </Drawer>
  );
}
