import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { cleaningApi } from "../../api/cleaning";
import type {
  CleaningDisinfectant,
  CleaningIsolation,
} from "../../api/cleaning";
import type { Bed } from "../../api/types";
import { useInvalidateClinical } from "../../api/query";
import {
  Action,
  Field,
  FormActions,
  Modal,
  Notice,
  SelectField,
  TextField,
  options,
  useToast,
} from "../../components/ui";
import { localDateTime } from "../../components/format";
import {
  checklistItems,
  cleaningError,
  cleaningProblems,
  disinfectants,
  isolations,
  photoProblem,
  preparePhoto,
} from "./cleaning";

// A datetime-local value for a moment given as an ISO string.
const local = (iso: string) => {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};

// The bed is in cleaning: this form is the proof that the terminal cleaning happened.
// It sends the record and then the photo, because the API takes them in two calls.
export function CleaningDialog({
  bed,
  startedAt,
  onClose,
}: {
  bed: Bed;
  /** When the bed entered cleaning, used as the default start. */
  startedAt?: string;
  onClose: () => void;
}) {
  const now = useRef(localDateTime()).current;
  const [checklist, setChecklist] = useState<Record<string, boolean>>({});
  const [isolation, setIsolation] = useState<CleaningIsolation>("NONE");
  const [disinfectant, setDisinfectant] =
    useState<CleaningDisinfectant>("CHLORINE_5000PPM");
  const [curtains, setCurtains] = useState(false);
  const [started, setStarted] = useState(startedAt ? local(startedAt) : now);
  const [finished, setFinished] = useState(now);
  const [notes, setNotes] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoError, setPhotoError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const preview = useMemo(
    () =>
      photo && typeof URL.createObjectURL === "function"
        ? URL.createObjectURL(photo)
        : null,
    [photo],
  );
  useEffect(
    () => () => {
      if (preview && typeof URL.revokeObjectURL === "function")
        URL.revokeObjectURL(preview);
    },
    [preview],
  );
  const problems = cleaningProblems({
    checklist,
    isolation,
    disinfectant,
    curtainsChanged: curtains,
    startedAt: started,
    finishedAt: finished,
    hasPhoto: !!photo,
  });
  const mutation = useMutation({
    mutationFn: async () => {
      const record = await cleaningApi.create(bed.id, {
        started_at: new Date(started).toISOString(),
        finished_at: new Date(finished).toISOString(),
        isolation,
        disinfectant,
        curtains_changed: curtains,
        checklist: Object.fromEntries(checklistItems.map(([key]) => [key, true])),
        notes: notes.trim() || null,
      });
      try {
        await cleaningApi.uploadPhoto(record.id, await preparePhoto(photo!));
        return { failure: null };
      } catch (error) {
        // The record exists without a photo: it can be completed from the panel.
        return { failure: cleaningError(error as Error) };
      }
    },
    onSuccess: async ({ failure }) => {
      if (failure)
        toast(
          `La limpieza quedó registrada, pero la foto no se subió. ${failure} Súbela desde el panel de la cama.`,
          "warn",
        );
      else
        toast(
          `Limpieza de ${bed.name} registrada. Queda pendiente de revisión.`,
        );
      onClose();
      await invalidate();
    },
  });
  const pick = (file: File | undefined) => {
    if (!file) return;
    const problem = photoProblem(file);
    setPhotoError(problem ?? "");
    setPhoto(problem ? null : file);
  };
  return (
    <Modal title="Registrar limpieza" onClose={onClose}>
      <p className="font-semibold">
        {bed.name}
        <span className="ml-2 text-sm font-normal text-ink-muted">
          {bed.code}
        </span>
      </p>
      <p className="my-3 text-sm text-ink-muted">
        Marca cada superficie que limpiaste y agrega una foto de la cama lista.
        Una persona de la coordinación revisa el registro: hasta que lo apruebe,
        la cama no queda disponible.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!problems.length) mutation.mutate();
        }}
      >
        {mutation.error && <Notice>{cleaningError(mutation.error)}</Notice>}
        <fieldset className="rounded-lg border border-line p-4">
          <legend className="px-2 text-sm font-semibold">
            Lista de chequeo ({checklistItems.filter(([k]) => checklist[k]).length}{" "}
            de {checklistItems.length})
          </legend>
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {checklistItems.map(([key, label]) => (
              <label
                key={key}
                className="flex min-h-9 cursor-pointer items-center gap-3 text-sm max-sm:min-h-11"
              >
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-accent"
                  checked={!!checklist[key]}
                  onChange={(event) =>
                    setChecklist({ ...checklist, [key]: event.target.checked })
                  }
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <SelectField
            label="Aislamiento"
            value={isolation}
            onChange={(event) =>
              setIsolation(event.target.value as CleaningIsolation)
            }
          >
            {options(isolations)}
          </SelectField>
          <SelectField
            label="Desinfectante"
            value={disinfectant}
            onChange={(event) =>
              setDisinfectant(event.target.value as CleaningDisinfectant)
            }
          >
            {options(disinfectants)}
          </SelectField>
          <Field
            label="Inicio de la limpieza"
            type="datetime-local"
            value={started}
            max={now}
            required
            onChange={(event) => setStarted(event.target.value)}
          />
          <Field
            label="Fin de la limpieza"
            type="datetime-local"
            value={finished}
            max={now}
            required
            onChange={(event) => setFinished(event.target.value)}
          />
        </div>
        <label className="mt-4 flex min-h-9 cursor-pointer items-center gap-3 text-sm max-sm:min-h-11">
          <input
            type="checkbox"
            className="h-4 w-4 accent-accent"
            checked={curtains}
            onChange={(event) => setCurtains(event.target.checked)}
          />
          Se cambiaron las cortinas
        </label>
        <div className="mt-4">
          <TextField
            label="Notas (opcional)"
            value={notes}
            maxLength={500}
            rows={2}
            onChange={(event) => setNotes(event.target.value)}
          />
        </div>
        <div className="mt-4">
          <p className="text-sm font-semibold">Foto de la cama limpia</p>
          <input
            ref={input}
            type="file"
            accept="image/*"
            capture="environment"
            className="sr-only"
            tabIndex={-1}
            aria-label="Foto de la cama limpia"
            onChange={(event) => {
              pick(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Action
              secondary
              label={photo ? "Cambiar foto" : "Tomar o elegir foto"}
              icon="pi pi-camera"
              onClick={() => input.current?.click()}
            />
            {photo && (
              <span className="min-w-0 break-all text-sm text-ink-muted">
                {photo.name}
              </span>
            )}
          </div>
          {preview && (
            <img
              src={preview}
              alt="Vista previa de la foto de la cama limpia"
              className="mt-3 max-h-56 rounded-lg border border-line object-contain"
            />
          )}
          {photoError && (
            <p role="alert" className="mt-2 text-sm text-urgent-ink">
              {photoError}
            </p>
          )}
          <p className="mt-2 text-xs text-ink-muted">
            En el celular se abre la cámara. PNG o JPEG, máximo 5 MB.
          </p>
        </div>
        {!!problems.length && (
          <div className="mt-4 text-sm text-ink-muted">
            <p className="font-semibold text-ink">Para registrar falta:</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </div>
        )}
        <FormActions
          label="Registrar limpieza"
          pending={mutation.isPending}
          disabled={!!problems.length}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}
