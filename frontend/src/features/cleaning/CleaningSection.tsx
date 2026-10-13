import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { cleaningApi, type CleaningRecord } from "../../api/cleaning";
import { bedsApi } from "../../api/beds";
import type { Bed, BedEvent, Role } from "../../api/types";
import { useInvalidateClinical, useOverdueCleaning } from "../../api/query";
import {
  Action,
  Loading,
  Notice,
  TextField,
  focus,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { dateTime, minutes } from "../../components/format";
import {
  canChangeBed,
  canRegisterCleaning,
  canReviewCleaning,
} from "../auth/permissions";
import { useAuth } from "../auth/AuthProvider";
import { CleaningDialog } from "./CleaningDialog";
import {
  bedBlockedByEvidence,
  checklistItems,
  cleaningError,
  evidenceBlockedMessage,
  disinfectants,
  isolations,
  percent,
  photoProblem,
  preparePhoto,
  statusClasses,
  statusLabels,
  statusSymbols,
} from "./cleaning";

export function CleaningStatusChip({
  status,
}: {
  status: CleaningRecord["status"];
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${statusClasses[status]}`}
    >
      <span aria-hidden="true">{statusSymbols[status]}</span>
      {statusLabels[status]}
    </span>
  );
}

// The AI guess made when the bed entered cleaning. It informs, it does not decide.
export function DelayPrediction({
  probability,
  late,
}: {
  probability?: number | null;
  late?: boolean | null;
}) {
  if (probability == null && late == null) return null;
  return (
    <p className="text-sm text-ink-muted">
      Predicción de retraso:{" "}
      {probability != null && (
        <strong className="font-semibold tabular-nums text-ink">
          {percent(probability)}
        </strong>
      )}
      {late != null && (
        <span
          className={`ml-2 rounded-full px-2 py-0.5 text-xs font-semibold ${late ? "bg-bed-cleaning-tint text-bed-cleaning-ink" : "bg-surface-sunken text-ink-muted"}`}
        >
          {late ? "Se prevé retraso" : "Sin retraso previsto"}
        </span>
      )}
      <small className="mt-0.5 block text-ink-muted">
        Estimación del modelo: más de 90 min hasta dejar la cama lista.
      </small>
    </p>
  );
}

function Photo({ record }: { record: CleaningRecord }) {
  const query = useQuery({
    queryKey: ["cleaning-photo", record.id],
    queryFn: ({ signal }) => cleaningApi.photo(record.id, signal),
    enabled: record.has_photo,
    staleTime: Infinity,
    retry: false,
    meta: { silent: true },
  });
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!query.data || typeof URL.createObjectURL !== "function") return;
    const created = URL.createObjectURL(query.data);
    setUrl(created);
    return () => URL.revokeObjectURL(created);
  }, [query.data]);
  if (!record.has_photo)
    return (
      <p className="text-sm text-bed-cleaning-ink">
        Sin foto: no se puede aprobar hasta que se suba.
      </p>
    );
  if (query.error)
    return <p className="text-sm text-ink-muted">No se pudo cargar la foto.</p>;
  return url ? (
    <img
      src={url}
      alt="Foto de evidencia de la limpieza"
      className="max-h-48 w-full rounded-lg border border-line bg-surface-sunken object-contain"
    />
  ) : (
    <div
      aria-hidden="true"
      className="h-24 rounded-lg bg-surface-sunken motion-safe:animate-pulse"
    />
  );
}

function PhotoUpload({
  record,
  label,
}: {
  record: CleaningRecord;
  label: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const [problem, setProblem] = useState("");
  const mutation = useMutation({
    mutationFn: async (file: File) =>
      cleaningApi.uploadPhoto(record.id, await preparePhoto(file)),
    onSuccess: async () => {
      toast("Foto guardada. El registro sigue pendiente de revisión.");
      await invalidate();
    },
  });
  return (
    <div>
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        aria-label="Archivo de la foto de limpieza"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (!file) return;
          const issue = photoProblem(file);
          setProblem(issue ?? "");
          if (!issue) mutation.mutate(file);
        }}
      />
      <Action
        secondary
        label={label}
        icon="pi pi-camera"
        loading={mutation.isPending}
        disabled={mutation.isPending}
        onClick={() => input.current?.click()}
      />
      {problem && <Notice>{problem}</Notice>}
      {mutation.error && <Notice>{cleaningError(mutation.error)}</Notice>}
    </div>
  );
}

// The coordinator approves or rejects. Whoever registered the cleaning never does:
// the screen says so up front, and the server enforces it anyway.
export function ReviewControls({
  record,
  mine,
}: {
  record: CleaningRecord;
  mine: boolean;
}) {
  const [note, setNote] = useState("");
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const mutation = useMutation({
    mutationFn: (approved: boolean) =>
      cleaningApi.review(record.id, approved, note.trim() || null),
    onSuccess: async (updated) => {
      toast(
        updated.status === "APPROVED"
          ? "Limpieza aprobada. Ya se puede dejar la cama disponible."
          : "Limpieza rechazada. Quien la registró verá tu nota.",
      );
      await invalidate();
    },
    onError: async (error) => {
      if (/ya fue revisado/i.test(error.message)) await invalidate();
    },
  });
  if (mine)
    return (
      <p className="rounded-lg bg-surface-sunken p-3 text-sm text-ink-muted">
        Registraste esta limpieza: no puedes aprobarla. La debe revisar otra
        persona de la coordinación.
      </p>
    );
  return (
    <form
      onSubmit={(event) => event.preventDefault()}
      className="space-y-3 rounded-lg border border-line p-3"
    >
      {mutation.error && <Notice>{cleaningError(mutation.error)}</Notice>}
      <TextField
        label="Nota de la revisión (obligatoria para rechazar)"
        value={note}
        maxLength={300}
        rows={2}
        onChange={(event) => setNote(event.target.value)}
      />
      <div className="flex flex-wrap justify-end gap-3">
        <Action
          secondary
          label="Rechazar"
          disabled={mutation.isPending || !note.trim()}
          onClick={() => mutation.mutate(false)}
        />
        <Action
          label="Aprobar"
          loading={mutation.isPending}
          disabled={mutation.isPending || !record.has_photo}
          onClick={() => mutation.mutate(true)}
        />
      </div>
      {!record.has_photo && (
        <p className="text-xs text-ink-muted">
          Aprobar exige la foto de evidencia.
        </p>
      )}
    </form>
  );
}

export function CleaningRecordItem({
  record,
  role,
  userId,
}: {
  record: CleaningRecord;
  role: Role;
  userId: string;
}) {
  const mine = record.created_by === userId;
  const pending = record.status === "PENDING_REVIEW";
  const canEditPhoto =
    pending && canRegisterCleaning(role) && (mine || role === "ADMIN");
  const done = checklistItems.filter(([key]) => record.checklist[key]).length;
  return (
    <li className="space-y-3 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CleaningStatusChip status={record.status} />
        <time
          dateTime={record.created_at}
          className="text-xs tabular-nums text-ink-muted"
        >
          {dateTime(record.created_at)}
        </time>
      </div>
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
        {[
          [
            "Limpieza",
            `${dateTime(record.started_at)} a ${dateTime(record.finished_at)}`,
          ],
          ["Lista de chequeo", `${done} de ${checklistItems.length}`],
          ["Aislamiento", isolations[record.isolation]],
          ["Desinfectante", disinfectants[record.disinfectant]],
          ["Cortinas", record.curtains_changed ? "Cambiadas" : "Sin cambio"],
          ["Registró", mine ? "Tú" : "Otra persona"],
        ].map(([term, value]) => (
          <div key={term} className="flex justify-between gap-3">
            <dt className="text-ink-muted">{term}</dt>
            <dd className="text-right font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {record.notes && (
        <p className="break-words text-sm text-ink-muted">
          Notas: {record.notes}
        </p>
      )}
      <Photo record={record} />
      <DelayPrediction
        probability={record.predicted_delay_probability}
        late={record.predicted_delay}
      />
      {record.status === "REJECTED" && (
        <p className="break-words rounded-lg bg-urgent-tint p-3 text-sm text-urgent-ink">
          Motivo del rechazo: {record.review_notes || "Sin nota"}
        </p>
      )}
      {record.status === "APPROVED" && record.reviewed_at && (
        <p className="text-sm text-ink-muted">
          Aprobada el {dateTime(record.reviewed_at)}
          {record.review_notes ? `. Nota: ${record.review_notes}` : ""}
        </p>
      )}
      {canEditPhoto && (
        <PhotoUpload
          record={record}
          label={record.has_photo ? "Cambiar foto" : "Subir foto"}
        />
      )}
      {pending && canReviewCleaning(role) && (
        <ReviewControls record={record} mine={mine} />
      )}
    </li>
  );
}

// Is there an approved record made after the bed entered cleaning? Same rule as the API.
export function hasCurrentApproval(
  records: CleaningRecord[],
  enteredAt?: string,
) {
  return records.some(
    (record) =>
      record.status === "APPROVED" &&
      (!enteredAt ||
        new Date(record.created_at).getTime() >= new Date(enteredAt).getTime()),
  );
}

// "Evidencia de limpieza" in the panel of a bed that is in cleaning.
export function CleaningSection({
  bed,
  role,
  entry,
}: {
  bed: Bed;
  role: Role;
  /** The latest event that moved the bed into cleaning. */
  entry?: BedEvent;
}) {
  const { user } = useAuth();
  const [registering, setRegistering] = useState(false);
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const records = useQuery({
    queryKey: ["cleaning", bed.id],
    queryFn: ({ signal }) => cleaningApi.forBed(bed.id, signal),
  });
  const overdue = useOverdueCleaning(canReviewCleaning(role));
  const late = overdue.data?.find((item) => item.location_id === bed.id);
  const release = useMutation({
    mutationFn: () =>
      bedsApi.change(bed.id, "AVAILABLE", "Limpieza aprobada por la coordinación"),
    onSuccess: async () => {
      toast(`${bed.name}: disponible`);
      await invalidate();
    },
  });
  const approved =
    !!records.data && hasCurrentApproval(records.data, entry?.event_at);
  return (
    <section className="mt-6 border-t border-line pt-4">
      <h3 className="text-base font-semibold">Evidencia de limpieza</h3>
      <p className="mt-1 text-sm text-ink-muted">
        La cama queda disponible cuando una persona distinta de quien la
        registró aprueba la limpieza.
      </p>
      {late && (
        <p className="mt-2 text-sm text-bed-cleaning-ink">
          Lleva{" "}
          <strong className="tabular-nums">
            {minutes(late.minutes_in_cleaning)}
          </strong>{" "}
          en limpieza sin aprobación.{" "}
          <Link
            to="/capacity"
            className={`rounded font-semibold underline underline-offset-2 hov:text-accent-strong ${focus}`}
          >
            Ver en «Requiere atención»
          </Link>
        </p>
      )}
      {/* Once there are records each one carries the guess; before that, the entry event does. */}
      {records.data && !records.data.length && (
        <div className="mt-2">
          <DelayPrediction
            probability={entry?.predicted_delay_probability}
            late={entry?.predicted_delay}
          />
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {canRegisterCleaning(role) ? (
          <Action
            label="Registrar limpieza"
            icon="pi pi-check-square"
            onClick={() => setRegistering(true)}
          />
        ) : (
          <p className="text-sm text-ink-muted">
            La limpieza la registra la operación de la IPS.
          </p>
        )}
        {approved && canChangeBed(role) && (
          <Action
            secondary
            label="Dejar la cama disponible"
            loading={release.isPending}
            disabled={release.isPending}
            onClick={() => release.mutate()}
          />
        )}
      </div>
      {release.error && (
        <Notice>
          {bedBlockedByEvidence(release.error)
            ? evidenceBlockedMessage
            : release.error.message}
        </Notice>
      )}
      {records.error ? (
        <ErrorState error={records.error} retry={records.refetch} />
      ) : !records.data ? (
        <Loading />
      ) : records.data.length ? (
        <ul className="mt-2 divide-y divide-line">
          {records.data.slice(0, 3).map((record) => (
            <CleaningRecordItem
              key={record.id}
              record={record}
              role={role}
              userId={user!.id}
            />
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-ink-muted">
          Aún no hay limpieza registrada para esta cama.
        </p>
      )}
      {registering && (
        <CleaningDialog
          bed={bed}
          startedAt={entry?.event_at}
          onClose={() => setRegistering(false)}
        />
      )}
    </section>
  );
}
