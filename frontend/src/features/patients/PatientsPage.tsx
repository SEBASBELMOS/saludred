import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { Patient } from "../../api/types";
import { patientsApi } from "../../api/patients";
import { useAuth } from "../auth/AuthProvider";
import { canCreate } from "../auth/permissions";
import {
  Action,
  DataTable,
  Field,
  Loading,
  Panel,
  SelectField,
} from "../../components/ui";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { dateOnly, fullName, profileNames } from "../../components/format";
import { searchCount, searchCriterion, type SearchMode } from "./validation";
import { PatientFormDialog } from "./PatientFormDialog";
import { PatientRecordDialog } from "./PatientRecordDialog";
import { RequestDialog } from "../queue/RequestDialog";
import { PendingReports } from "../ai/PendingReports";
export function PatientResults({
  patients,
  total,
  onOpen,
}: {
  patients: Patient[];
  total: number;
  onOpen: (id: string) => void;
}) {
  return (
    <>
      <p className="mb-3 text-sm text-ink-muted">{searchCount(total)}</p>
      <Panel title="Resultados de la búsqueda">
        {patients.length ? (
          <DataTable
            caption="Pacientes encontrados"
            headers={[
              "Paciente",
              "Documento",
              "Nacimiento",
              "Perfil clínico",
              "Ficha",
            ]}
          >
            {patients.slice(0, 10).map((patient) => (
              <tr key={patient.id}>
                <td className="font-semibold">{fullName(patient)}</td>
                <td>
                  {patient.document_type} {patient.document_number}
                </td>
                <td>{dateOnly(patient.birth_date)}</td>
                <td>
                  {patient.clinical_profile
                    ? profileNames[patient.clinical_profile] ||
                      patient.clinical_profile
                    : "—"}
                </td>
                <td>
                  <Action
                    secondary
                    label="Ver paciente"
                    onClick={() => onOpen(patient.id)}
                  />
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <EmptyState title="No encontramos pacientes con esta búsqueda">
            El documento se busca completo.
          </EmptyState>
        )}
      </Panel>
    </>
  );
}
export function PatientsPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [term, setTerm] = useState(params.get("search") || "");
  const [debounced, setDebounced] = useState(term);
  const [mode, setMode] = useState<SearchMode>("auto");
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(term), 350);
    return () => clearTimeout(timer);
  }, [term]);
  const criterion = searchCriterion(debounced, mode);
  const current = searchCriterion(term, mode);
  const query = useQuery({
    queryKey: ["patients", criterion?.key, criterion?.value],
    queryFn: ({ signal }) =>
      patientsApi.search(criterion!.key, criterion!.value, signal),
    enabled:
      !!criterion &&
      !criterion.error &&
      !!current &&
      !current.error &&
      term === debounced,
  });
  const close = () =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("patient");
      next.delete("request");
      return next;
    });
  return (
    <>
      <PendingReports />
      <div className="mb-6 grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px_auto]">
        <Field
          label="Documento completo o nombre"
          type="search"
          data-main-search
          aria-keyshortcuts="/"
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          autoComplete="off"
        />
        <SelectField
          label="Buscar por"
          value={mode}
          onChange={(event) => setMode(event.target.value as SearchMode)}
        >
          <option value="auto">Detectar criterio</option>
          <option value="document_number">Documento exacto</option>
          <option value="name">Nombre o apellido</option>
        </SelectField>
        {canCreate(user!.role) && (
          <Action
            label="Nuevo paciente"
            icon="pi pi-plus"
            className="self-end"
            onClick={() => setCreating(true)}
          />
        )}
      </div>
      {!current || current.error ? (
        <Panel title="Encuentra una ficha">
          <EmptyState title={current?.error || "Busca a un paciente"}>
            Escribe el documento completo (6 caracteres o más) o al menos 3
            letras del nombre o apellido. Por privacidad, los pacientes no se
            listan: se buscan.
          </EmptyState>
        </Panel>
      ) : term !== debounced || query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorState error={query.error} retry={query.refetch} />
      ) : query.data ? (
        <PatientResults
          patients={query.data.items}
          total={query.data.total}
          onOpen={(id) =>
            setParams((prev) => {
              const next = new URLSearchParams(prev);
              next.set("patient", id);
              return next;
            })
          }
        />
      ) : null}
      {creating && (
        <PatientFormDialog
          onClose={() => setCreating(false)}
          onSaved={(patient) => {
            setCreating(false);
            setMode("document_number");
            setTerm(patient.document_number);
          }}
        />
      )}
      {params.get("patient") && (
        <PatientRecordDialog
          patientId={params.get("patient")!}
          onClose={close}
        />
      )}{" "}
      {params.get("request") === "new" && canCreate(user!.role) && (
        <RequestDialog onClose={close} />
      )}
    </>
  );
}
