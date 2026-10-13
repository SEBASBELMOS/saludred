import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { alertsApi, type Alert } from "../../api/alerts";
import { ApiError } from "../../api/client";
import { Action, Notice, TextField, useToast } from "../../components/ui";
import { ALERTS_KEY, PENDING_KEY } from "./useAlerts";

export const staleAlertMessage =
  "Otro clínico ya reconoció esta alerta. Actualizamos la lista.";

/**
 * Acknowledging is a clinical act, not a click: the note is required and the
 * button stays disabled until there is one. A 409 means somebody else got there
 * first, so the list is refreshed; a 422 keeps the form open with the reason.
 */
export function AcknowledgeForm({
  alert,
  onClose,
}: {
  alert: Pick<Alert, "id">;
  onClose: () => void;
}) {
  const [nota, setNota] = useState("");
  const client = useQueryClient();
  const toast = useToast();
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ALERTS_KEY }),
      client.invalidateQueries({ queryKey: PENDING_KEY }),
    ]);
  const mutation = useMutation({
    mutationFn: () => alertsApi.acknowledge(alert.id, nota.trim()),
    onSuccess: async () => {
      client.setQueryData<Alert[]>(ALERTS_KEY, (list) =>
        (list ?? []).filter((item) => item.id !== alert.id),
      );
      toast("Alerta reconocida. Queda registrada con tu nota.");
      onClose();
      await refresh();
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        toast(staleAlertMessage, "warn");
        onClose();
        await refresh();
      }
    },
  });
  const blocked = nota.trim().length === 0;
  const failure =
    mutation.error &&
    !(mutation.error instanceof ApiError && mutation.error.status === 409)
      ? mutation.error
      : null;
  return (
    <form
      aria-label="Reconocer alerta"
      className="mt-3 rounded-lg border border-line bg-surface p-3 text-ink"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (!blocked) mutation.mutate();
      }}
    >
      <TextField
        label="Nota de reconocimiento (obligatoria)"
        value={nota}
        required
        autoFocus
        maxLength={1000}
        rows={2}
        onChange={(event) => setNota(event.target.value)}
      />
      <p className="mt-1 text-xs text-ink-muted">
        Qué viste y qué vas a hacer. Queda en la auditoría con tu firma.
      </p>
      {failure && (
        <Notice>
          {failure instanceof ApiError && failure.status === 422
            ? "La nota es obligatoria. Escríbela e intenta de nuevo."
            : failure.message}
        </Notice>
      )}
      <div className="mt-3 flex flex-wrap justify-end gap-3">
        <Action
          secondary
          label="Cancelar"
          disabled={mutation.isPending}
          onClick={onClose}
        />
        <Action
          type="submit"
          label="Confirmar reconocimiento"
          loading={mutation.isPending}
          disabled={mutation.isPending || blocked}
        />
      </div>
    </form>
  );
}
