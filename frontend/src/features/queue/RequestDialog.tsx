import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { patientsApi } from "../../api/patients";
import { createEncounter } from "../../api/encounters";
import { queueApi } from "../../api/queue";
import type { Patient, Priority } from "../../api/types";
import { useNetwork, useInvalidateClinical } from "../../api/query";
import { useAuth } from "../auth/AuthProvider";
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
import { fullName } from "../../components/format";
export async function requestBed({
  patient,
  document,
  organization,
  priority,
  service,
  reason,
}: {
  patient?: Patient;
  document: string;
  organization: string;
  priority: Priority;
  service: string;
  reason: string;
}) {
  if (!reason.trim()) throw new Error("Escribe el motivo de ingreso.");
  if (!patient && document.trim().length < 6)
    throw new Error("El documento debe tener al menos 6 caracteres.");
  const target =
    patient ||
    (await patientsApi.search("document_number", document.trim())).items[0];
  if (!target)
    throw new Error(
      "No hay un paciente con ese documento. Regístralo primero desde Pacientes.",
    );
  const encounters = await patientsApi.encounters(target.id);
  let encounter = encounters.find(
    (e) => e.organization_id === organization && e.status === "in-progress",
  );
  if (!encounter)
    encounter = await createEncounter({
      patient_id: target.id,
      organization_id: organization,
      encounter_class: priority === "EMERGENCY" ? "EMER" : "IMP",
      status: "in-progress",
      priority,
      reason_text: reason.trim(),
      started_at: new Date().toISOString(),
    });
  await queueApi.create({
    encounter_id: encounter.id,
    required_service: service,
    priority,
  });
  return target;
}
export function RequestDialog({
  patient,
  onClose,
}: {
  patient?: Patient;
  onClose: () => void;
}) {
  const network = useNetwork();
  const { user } = useAuth();
  const operator = user!.role === "IPS_CLINICAL_OPERATOR";
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const navigate = useNavigate();
  const mutation = useMutation({
    mutationFn: (form: FormData) =>
      requestBed({
        patient,
        document: String(form.get("document") || ""),
        organization: operator
          ? user!.organization_id!
          : String(form.get("org")),
        priority: form.get("priority") as Priority,
        service: String(form.get("service")),
        reason: String(form.get("reason")),
      }),
    onSuccess: async (target) => {
      toast(`Solicitud de ${fullName(target)} añadida a la cola`);
      await invalidate();
      onClose();
      navigate("/queue");
    },
  });
  return (
    <Modal title="Solicitar una cama" onClose={onClose}>
      <p className="mb-4 text-sm text-ink-muted">
        La solicitud entra a la cola según su prioridad. Si el paciente no tiene
        un ingreso abierto en la institución, se registra uno con el motivo
        indicado.
      </p>
      {network.error ? (
        <ErrorState error={network.error} retry={network.refetch} />
      ) : !network.data ? (
        <Loading />
      ) : (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate(new FormData(event.currentTarget));
          }}
        >
          {mutation.error && <Notice>{mutation.error.message}</Notice>}
          {patient ? (
            <p className="rounded-lg bg-surface-sunken p-3 font-semibold">
              {fullName(patient)}
              <span className="ml-2 font-normal text-ink-muted">
                {patient.document_number}
              </span>
            </p>
          ) : (
            <Field
              label="Documento del paciente"
              name="document"
              required
              minLength={6}
              maxLength={32}
            />
          )}
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
            <SelectField label="Servicio requerido" name="service" required>
              {[
                ...new Set(
                  network.data.beds.map((b) => b.service || "Sin servicio"),
                ),
              ]
                .sort()
                .map((service) => (
                  <option key={service}>{service}</option>
                ))}
            </SelectField>
            <SelectField label="Prioridad" name="priority" required>
              <option value="">Selecciona la prioridad</option>
              {options(priorities)}
            </SelectField>
          </div>
          <TextField
            label="Motivo de ingreso"
            name="reason"
            required
            maxLength={500}
            rows={3}
          />
          <FormActions
            label="Crear solicitud"
            pending={mutation.isPending}
            onClose={onClose}
          />
        </form>
      )}
    </Modal>
  );
}
