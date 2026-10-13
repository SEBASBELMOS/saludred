import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { patientsApi } from "../../api/patients";
import type { Patient } from "../../api/types";
import { Action, SelectField, linkButton, useToast } from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canChangeResponsible } from "../auth/permissions";

const roleLabel: Record<string, string> = {
  medico: "Médico",
  especialista: "Especialista",
};

// A <dt>/<dd> pair for the patient details grid.
export function ResponsibleClinician({ patient }: { patient: Patient }) {
  const { user } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [choice, setChoice] = useState("");
  const editable = !!user && canChangeResponsible(user.role);
  const clinicians = useQuery({
    queryKey: ["eligible-clinicians", patient.id],
    queryFn: ({ signal }) => patientsApi.eligibleClinicians(patient.id, signal),
    enabled: editable && editing,
    meta: { silent: true },
  });
  const save = useMutation({
    mutationFn: () => patientsApi.setResponsible(patient.id, choice),
    onSuccess: async () => {
      setEditing(false);
      await client.invalidateQueries({
        queryKey: ["patient-record", patient.id],
      });
      toast("Médico responsable actualizado");
    },
    onError: (error) =>
      toast(
        `No se pudo cambiar el médico responsable. ${error.message}`,
        "error",
      ),
  });
  return (
    <div className="min-w-0 sm:col-span-2">
      <dt className="text-xs text-ink-muted">Médico responsable</dt>
      <dd className="mt-1 break-words text-sm font-semibold">
        {patient.responsible_clinician_name || "Sin asignar"}
        {editable && !editing && (
          <button
            type="button"
            className={`${linkButton} ml-3 text-sm max-sm:min-h-11`}
            onClick={() => {
              setChoice(patient.responsible_clinician_id ?? "");
              setEditing(true);
            }}
          >
            Cambiar
          </button>
        )}
      </dd>
      {editing && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (choice) save.mutate();
          }}
        >
          <div className="min-w-60 flex-1">
            <SelectField
              label="Nuevo médico responsable"
              value={choice}
              disabled={!clinicians.data}
              onChange={(event) => setChoice(event.target.value)}
            >
              <option value="">
                {clinicians.error
                  ? "No se pudo cargar la lista"
                  : "Selecciona un médico"}
              </option>
              {(clinicians.data ?? []).map((clinician) => (
                <option key={clinician.id} value={clinician.id}>
                  {clinician.nombre},{" "}
                  {roleLabel[clinician.rol] ?? clinician.rol}
                  {" · "}
                  {clinician.organizacion}
                </option>
              ))}
            </SelectField>
            {clinicians.data?.length === 0 && (
              <p className="mt-1 text-xs text-ink-muted">
                No hay otro médico o especialista activo en la IPS que atiende a
                este paciente.
              </p>
            )}
          </div>
          <Action
            type="submit"
            label="Guardar"
            loading={save.isPending}
            disabled={
              !choice ||
              choice === patient.responsible_clinician_id ||
              save.isPending
            }
          />
          <Action
            secondary
            label="Cancelar"
            onClick={() => setEditing(false)}
          />
        </form>
      )}
    </div>
  );
}
