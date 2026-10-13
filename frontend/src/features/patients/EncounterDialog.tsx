import { useMutation } from "@tanstack/react-query";
import type { EncounterInput, Patient, Priority } from "../../api/types";
import { createEncounter } from "../../api/encounters";
import { createObservation } from "../../api/observations";
import { ApiError } from "../../api/client";
import { useInvalidateClinical, useNetwork } from "../../api/query";
import { useAuth } from "../auth/AuthProvider";
import { isIpsScoped } from "../auth/permissions";
import { ErrorState } from "../../components/ErrorState";
import {
  Field,
  FormActions,
  Loading,
  Modal,
  Notice,
  SelectField,
  TextField,
  options,
  useToast,
} from "../../components/ui";
import { priorities } from "../../components/PriorityBadge";
import {
  encounterClasses,
  fullName,
  localDateTime,
} from "../../components/format";
import { validateVitals, vitalCatalog } from "./validation";
export function EncounterDialog({
  patient,
  onClose,
  onSaved,
}: {
  patient: Patient;
  onClose: () => void;
  onSaved: () => void;
}) {
  const network = useNetwork();
  const { user } = useAuth();
  // The operator and the especialista work inside one IPS: the API refuses any other.
  const operator = isIpsScoped(user!.role);
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const mutation = useMutation({
    mutationFn: async (form: FormData) => {
      const readings = validateVitals(
        Object.fromEntries(
          vitalCatalog.map((v) => [
            v.code,
            String(form.get(`v-${v.code}`) || ""),
          ]),
        ),
      );
      const reason = String(form.get("reason")).trim();
      if (!reason) throw new Error("Escribe el motivo de consulta.");
      const startedAt = new Date(String(form.get("started_at")));
      if (startedAt > new Date())
        throw new Error("La fecha de atención no puede ser futura.");
      const encounter = await createEncounter({
        patient_id: patient.id,
        organization_id: operator
          ? user!.organization_id!
          : String(form.get("org")),
        encounter_class: form.get(
          "encounter_class",
        ) as EncounterInput["encounter_class"],
        status: "in-progress",
        priority: form.get("priority") as Priority,
        reason_text: reason,
        started_at: startedAt.toISOString(),
      });
      const failed: string[] = [];
      for (const reading of readings) {
        try {
          await createObservation({
            patient_id: patient.id,
            encounter_id: encounter.id,
            status: "final",
            code: reading.code,
            display: reading.name,
            value_numeric: reading.value,
            unit: reading.ucum,
            observed_at: startedAt.toISOString(),
          });
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) throw error;
          failed.push(`${reading.name} (${(error as Error).message})`);
        }
      }
      if (failed.length)
        toast(
          `La atención quedó registrada, pero no se guardó: ${failed.join("; ")}`,
          "warn",
        );
      else toast(`Atención registrada con ${readings.length} mediciones`);
    },
    onSuccess: async () => {
      await invalidate();
      onSaved();
    },
  });
  return (
    <Modal title="Registrar atención" onClose={onClose}>
      <p className="mb-4 font-semibold">{fullName(patient)}</p>
      {network.error ? (
        <ErrorState error={network.error} retry={network.refetch} />
      ) : !network.data ? (
        <Loading />
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate(new FormData(event.currentTarget));
          }}
        >
          {mutation.error && <Notice>{mutation.error.message}</Notice>}
          <div className="grid gap-4 sm:grid-cols-2">
            {operator ? (
              <p className="text-sm">
                Institución:{" "}
                {network.data.network.organizations.find(
                  (o) => o.organization_id === user!.organization_id,
                )?.organization_name || "Mi IPS"}
              </p>
            ) : (
              <SelectField label="Institución que atiende" name="org" required>
                <option value="">Selecciona la IPS</option>
                {network.data.network.organizations.map((o) => (
                  <option key={o.organization_id} value={o.organization_id}>
                    {o.organization_name}
                  </option>
                ))}
              </SelectField>
            )}
            <SelectField
              label="Tipo de atención"
              name="encounter_class"
              defaultValue="AMB"
            >
              {options(encounterClasses)}
            </SelectField>
            <SelectField
              label="Prioridad"
              name="priority"
              defaultValue="ROUTINE"
            >
              {options(priorities)}
            </SelectField>
            <Field
              label="Fecha y hora"
              name="started_at"
              type="datetime-local"
              required
              defaultValue={localDateTime()}
              max={localDateTime()}
            />
            <div className="sm:col-span-2">
              <TextField
                label="Motivo de consulta"
                name="reason"
                required
                maxLength={500}
                rows={2}
              />
            </div>
          </div>
          <fieldset className="mt-5 rounded-lg border border-line p-4">
            <legend className="px-2 text-sm font-semibold">
              Signos vitales (opcionales)
            </legend>
            <div className="grid gap-4 sm:grid-cols-2">
              {vitalCatalog.map((v) => (
                <div key={v.code}>
                  <Field
                    label={v.name}
                    name={`v-${v.code}`}
                    type="number"
                    step="any"
                    min={v.min}
                    max={v.max}
                  />
                  <p className="mt-1 text-xs text-ink-muted">
                    {v.unit}, válido {v.min}–{v.max}
                  </p>
                </div>
              ))}
            </div>
          </fieldset>
          <p className="mt-3 text-xs text-ink-muted">
            Cada medición se guarda con su código LOINC y su unidad UCUM. Los
            valores fuera del rango plausible no se aceptan.
          </p>
          <FormActions
            label="Registrar atención"
            pending={mutation.isPending}
            onClose={onClose}
          />
        </form>
      )}
    </Modal>
  );
}
