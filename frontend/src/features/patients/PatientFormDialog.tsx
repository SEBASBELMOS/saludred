import { useMutation, useQuery } from "@tanstack/react-query";
import type { Patient, PatientInput } from "../../api/types";
import { patientsApi } from "../../api/patients";
import { listEps } from "../../api/organizations";
import { useInvalidateClinical } from "../../api/query";
import {
  Field,
  FormActions,
  Loading,
  Modal,
  Notice,
  SelectField,
  options,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { documentTypes, genders, today } from "../../components/format";
export function PatientFormDialog({
  patient,
  onClose,
  onSaved,
}: {
  patient?: Patient;
  onClose: () => void;
  onSaved: (patient: Patient) => void;
}) {
  const eps = useQuery({
    queryKey: ["eps"],
    queryFn: ({ signal }) => listEps(signal),
    enabled: !patient,
  });
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const mutation = useMutation({
    mutationFn: async (form: FormData) => {
      const clean = (key: string) => String(form.get(key) ?? "").trim();
      const shared = {
        first_name: clean("first_name"),
        last_name: clean("last_name"),
        birth_date: clean("birth_date"),
        gender: clean("gender") as PatientInput["gender"],
        phone: clean("phone") || null,
        email: clean("email") || null,
      };
      if (!shared.first_name || !shared.last_name)
        throw new Error("Escribe los nombres y apellidos.");
      if (shared.birth_date > today())
        throw new Error("La fecha de nacimiento no puede ser futura.");
      if (patient) {
        const values = { ...shared, address: clean("address") || null };
        const changes = Object.fromEntries(
          Object.entries(values).filter(
            ([key, value]) =>
              (patient[key as keyof Patient] ?? "") !== (value ?? ""),
          ),
        );
        if (!Object.keys(changes).length) {
          toast("No había cambios que guardar");
          return patient;
        }
        const result = await patientsApi.update(patient.id, changes);
        toast("Datos del paciente actualizados");
        return result;
      }
      if (!eps.data?.items.length)
        throw new Error("No hay una EPS registrada para afiliar al paciente.");
      return patientsApi.create({
        ...shared,
        document_type: clean("document_type") as PatientInput["document_type"],
        document_number: clean("document_number"),
        eps_organization_id: eps.data.items[0].id,
      });
    },
    onSuccess: async (result) => {
      if (!patient)
        toast(`${result.first_name} ${result.last_name} quedó registrado`);
      await invalidate();
      onSaved(result);
    },
  });
  return (
    <Modal
      title={patient ? "Editar datos del paciente" : "Registrar paciente"}
      onClose={onClose}
    >
      {!patient && eps.error ? (
        <ErrorState error={eps.error} retry={eps.refetch} />
      ) : !patient && !eps.data ? (
        <Loading />
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate(new FormData(event.currentTarget));
          }}
        >
          <p className="mb-5 text-sm text-ink-muted">
            {patient
              ? "El documento identifica al paciente en la red y en FHIR. No se modifica aquí; la versión anterior queda en el historial."
              : "El paciente queda afiliado a la EPS de la red. El documento no puede repetirse."}
          </p>
          {mutation.error && <Notice>{mutation.error.message}</Notice>}
          <div className="grid min-w-0 gap-4 sm:grid-cols-2">
            <Field
              label="Nombres"
              name="first_name"
              defaultValue={patient?.first_name}
              required
              maxLength={120}
            />
            <Field
              label="Apellidos"
              name="last_name"
              defaultValue={patient?.last_name}
              required
              maxLength={120}
            />
            {patient ? (
              <Field
                label="Documento"
                value={`${patient.document_type} ${patient.document_number}`}
                readOnly
              />
            ) : (
              <>
                <SelectField
                  label="Tipo de documento"
                  name="document_type"
                  defaultValue="CC"
                >
                  {options(documentTypes)}
                </SelectField>
                <Field
                  label="Número de documento"
                  name="document_number"
                  required
                  minLength={4}
                  maxLength={32}
                />
              </>
            )}
            <Field
              label="Fecha de nacimiento"
              name="birth_date"
              type="date"
              defaultValue={patient?.birth_date}
              required
              max={today()}
            />
            <SelectField
              label="Sexo"
              name="gender"
              defaultValue={patient?.gender || "female"}
            >
              {options(genders)}
            </SelectField>
            <Field
              label="Teléfono (opcional)"
              name="phone"
              type="tel"
              maxLength={40}
              defaultValue={patient?.phone || ""}
            />
            <Field
              label="Correo (opcional)"
              name="email"
              type="email"
              maxLength={200}
              defaultValue={patient?.email || ""}
            />
            {patient && (
              <Field
                label="Dirección"
                name="address"
                maxLength={300}
                defaultValue={patient.address || ""}
              />
            )}
          </div>
          <FormActions
            label={patient ? "Guardar cambios" : "Guardar paciente"}
            pending={mutation.isPending}
            onClose={onClose}
          />
        </form>
      )}
    </Modal>
  );
}
