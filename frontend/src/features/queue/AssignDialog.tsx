import { MotionItems } from "../../components/motion";
import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queueApi } from "../../api/queue";
import { ApiError } from "../../api/client";
import { useInvalidateClinical } from "../../api/query";
import type { Candidate, QueuedRequest } from "../../api/types";
import {
  Action,
  FormActions,
  Loading,
  Modal,
  Notice,
  TextField,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { PriorityBadge } from "../../components/PriorityBadge";
import { priorRequest } from "./priority";
import { rankCandidates, recommendedCandidate } from "./recommend";
export function PriorityConflictDialog({
  previous,
  onPrevious,
  onException,
  onClose,
}: {
  previous?: QueuedRequest;
  onPrevious: () => void;
  onException: () => void;
  onClose: () => void;
}) {
  return (
    <Modal title="Hay una solicitud anterior" onClose={onClose}>
      <Notice amber>
        {previous
          ? `${previous.patient_name} espera por ${previous.required_service} con mayor prioridad o desde antes.`
          : "Hay otra solicitud que debe atenderse primero. Actualiza la cola para consultar su posición."}
      </Notice>
      <p className="text-sm text-ink-muted">
        El orden de la cola se respeta por defecto. Si hay un motivo clínico o
        logístico para atender esta solicitud primero, puedes asignarla como
        excepción: el motivo queda en la auditoría.
      </p>
      <div className="mt-5 flex flex-wrap justify-end gap-3">
        <Action secondary label="Volver" onClick={onClose} />
        <Action
          secondary
          label="Asignar como excepción"
          onClick={onException}
        />
        <Action label="Ver solicitud anterior" onClick={onPrevious} />
      </div>
    </Modal>
  );
}
export function AssignmentForm({
  candidates,
  exception,
  pending,
  submit,
  onClose,
}: {
  candidates: Candidate[];
  exception: boolean;
  pending: boolean;
  submit: (bed: string, reason: string) => void;
  onClose: () => void;
}) {
  const ranked = rankCandidates(candidates).slice(0, 8);
  const recommended = recommendedCandidate(candidates);
  const [bed, setBed] = useState(recommended?.location_id ?? "");
  const [reason, setReason] = useState("");
  const chosen = ranked.find((candidate) => candidate.location_id === bed);
  // A bed of another service is allowed, but it needs a recorded reason.
  const otherService = !!chosen && !chosen.same_service;
  const needsReason = exception || otherService;
  const blocked = !bed || (needsReason && !reason.trim());
  return (
    <form
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (!blocked) submit(bed, needsReason ? reason.trim() : "");
      }}
    >
      <fieldset className="space-y-2">
        <legend className="mb-3 text-sm font-semibold">
          {recommended
            ? "Revisa la cama recomendada o elige otra."
            : "Elige una cama. Ninguna es del servicio solicitado."}
        </legend>
        <MotionItems>
          {ranked.map((candidate) => (
            <label
              key={candidate.location_id}
              className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 focus-within:ring-2 focus-within:ring-accent ${bed === candidate.location_id ? "border-accent bg-accent-tint" : "border-line"}`}
            >
              <input
                type="radio"
                name="bed"
                className="mt-1 accent-accent"
                required
                value={candidate.location_id}
                checked={bed === candidate.location_id}
                onChange={() => setBed(candidate.location_id)}
              />
              <span className="min-w-0 break-words">
                <strong className="block text-sm">
                  {candidate.name}
                  <span className="ml-2 font-normal text-ink-muted">
                    {candidate.service}
                  </span>
                </strong>
                <span className="block text-xs text-ink-muted">
                  {candidate.organization_name}
                </span>
                <span className="mt-2 flex flex-wrap gap-2 text-xs font-semibold">
                  {candidate.location_id === recommended?.location_id && (
                    <span className="rounded-full bg-accent px-2 py-0.5 text-white">
                      Recomendada
                    </span>
                  )}
                  {candidate.same_organization && (
                    <span className="rounded-full bg-bed-available-tint px-2 py-0.5 text-bed-available-ink">
                      Misma IPS
                    </span>
                  )}
                  {!candidate.same_service && (
                    <span className="rounded-full bg-bed-cleaning-tint px-2 py-0.5 text-bed-cleaning-ink">
                      Otro servicio
                    </span>
                  )}
                </span>
              </span>
            </label>
          ))}
        </MotionItems>
      </fieldset>
      {needsReason && (
        <div className="mt-4">
          <TextField
            label={
              exception
                ? "Motivo de la excepción"
                : "Motivo para asignar una cama de otro servicio"
            }
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            required
            maxLength={300}
            rows={3}
          />
          {!exception && (
            <p className="mt-1 text-xs text-ink-muted">
              La cama no es del servicio solicitado. El motivo queda en la
              auditoría.
            </p>
          )}
        </div>
      )}
      <p className="mt-3 text-xs text-ink-muted">
        Se muestran hasta 8 alternativas de {candidates.length} disponibles.
      </p>
      <FormActions
        label={exception ? "Asignar como excepción" : "Confirmar asignación"}
        pending={pending}
        disabled={blocked}
        onClose={onClose}
      />
    </form>
  );
}
export function AssignDialog({
  request,
  queue,
  onClose,
  onSelect,
  onAssigned,
}: {
  request: QueuedRequest;
  queue: QueuedRequest[];
  onClose: () => void;
  onSelect: (id: string) => void;
  /** Called once the bed is assigned, before the queue refreshes. */
  onAssigned?: () => void;
}) {
  const client = useQueryClient();
  const previous = priorRequest(request, queue);
  const [conflict, setConflict] = useState(!!previous);
  const [exception, setException] = useState(false);
  const invalidate = useInvalidateClinical();
  const toast = useToast();
  const candidates = useQuery({
    queryKey: ["candidates", request.id],
    queryFn: ({ signal }) => queueApi.candidates(request.id, signal),
    enabled: !conflict,
  });
  const mutation = useMutation({
    mutationFn: ({ bed, reason }: { bed: string; reason: string }) =>
      queueApi.assign(request.id, {
        location_id: bed,
        override_priority: exception,
        // The server stores the reason for an exception and for a bed of another service.
        reason: reason || null,
      }),
    onSuccess: async (_, { bed }) => {
      const code = candidates.data?.find((c) => c.location_id === bed)?.code;
      toast(
        `${code ? `Cama ${code}` : "Cama"} asignada a ${request.patient_name}`,
      );
      onAssigned?.();
      onClose();
      await invalidate();
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        !exception &&
        /atenderse primero/i.test(error.message)
      )
        setConflict(true);
    },
  });
  if (conflict)
    return (
      <PriorityConflictDialog
        previous={previous}
        onClose={onClose}
        onException={() => {
          setException(true);
          setConflict(false);
          mutation.reset();
        }}
        onPrevious={async () => {
          const fresh = await queueApi.list().catch(() => null);
          if (fresh) {
            client.setQueryData(["queue"], fresh);
            const prior = priorRequest(request, fresh);
            if (prior) {
              onSelect(prior.id);
              return;
            }
          }
          toast(
            "Actualiza la cola para consultar la solicitud anterior.",
            "warn",
          );
          onClose();
          await invalidate();
        }}
      />
    );
  return (
    <Modal title="Encontrar una cama" onClose={onClose}>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-surface-sunken p-4">
        <div>
          <strong className="block">{request.patient_name}</strong>
          <p className="text-sm text-ink-muted">
            {request.patient_document}
            <span className="ml-2">{request.required_service}</span>
          </p>
        </div>
        <PriorityBadge priority={request.priority} />
      </div>
      {mutation.error && <Notice>{mutation.error.message}</Notice>}
      {candidates.error ? (
        <ErrorState error={candidates.error} retry={candidates.refetch} />
      ) : !candidates.data ? (
        <Loading />
      ) : candidates.data.length ? (
        <AssignmentForm
          candidates={candidates.data}
          exception={exception}
          pending={mutation.isPending}
          onClose={onClose}
          submit={(bed, reason) => mutation.mutate({ bed, reason })}
        />
      ) : (
        <EmptyState title="No hay camas disponibles">
          La solicitud sigue en la cola y se atenderá cuando se libere una cama.
        </EmptyState>
      )}
    </Modal>
  );
}
