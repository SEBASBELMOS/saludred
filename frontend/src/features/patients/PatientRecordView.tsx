import type { ReactNode } from "react";
import { MotionItems } from "../../components/motion";
import type { Patient, Encounter, Observation } from "../../api/types";
import {
  dateOnly,
  dateTime,
  encounterClasses,
  encounterStatuses,
  fullName,
  genders,
  profileNames,
} from "../../components/format";
import { PriorityBadge } from "../../components/PriorityBadge";
import { DataTable, Panel } from "../../components/ui";
import { EmptyState } from "../../components/EmptyState";
import { vitalCatalog } from "./validation";
export function PatientDetails({
  patient,
  showName = true,
  responsible,
}: {
  patient: Patient;
  showName?: boolean;
  // A <dt>/<dd> pair appended to the grid (the responsible clinician).
  responsible?: ReactNode;
}) {
  const fields = [
    ["Documento", `${patient.document_type} ${patient.document_number}`],
    ["Fecha de nacimiento", dateOnly(patient.birth_date)],
    ["Sexo", genders[patient.gender]],
    ["Teléfono de contacto", patient.phone || "Sin registrar"],
    ["Correo", patient.email || "Sin registrar"],
    ["Dirección", patient.address || "Sin registrar"],
    [
      "Perfil clínico",
      patient.clinical_profile
        ? profileNames[patient.clinical_profile] || patient.clinical_profile
        : "Sin perfil",
    ],
  ];
  return (
    <div className={showName ? "p-5" : ""}>
      {showName && (
        <h2 className="mb-5 break-words text-xl font-semibold">
          {fullName(patient)}
        </h2>
      )}
      <dl className="grid gap-4 sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-ink-muted">{label}</dt>
            <dd className="mt-1 break-words text-sm font-semibold">{value}</dd>
          </div>
        ))}
        {responsible}
      </dl>
    </div>
  );
}
export function Measurements({
  observations,
}: {
  observations: Observation[];
}) {
  const latest: Record<string, Observation> = {};
  observations.forEach((observation) => {
    if (
      observation.value_numeric != null &&
      (!latest[observation.code] ||
        observation.observed_at > latest[observation.code].observed_at)
    )
      latest[observation.code] = observation;
  });
  const shown = vitalCatalog.filter((v) => latest[v.code]);
  return shown.length ? (
    <DataTable
      caption="Últimas mediciones"
      headers={["Medición", "Resultado", "Fecha"]}
    >
      {shown.map((v) => (
        <tr key={v.code}>
          <td>
            {v.name}
            <small className="block text-xs text-ink-muted">
              LOINC {v.code}
            </small>
          </td>
          <td className="font-semibold tabular-nums">
            {latest[v.code].value_numeric!.toLocaleString("es-CO")} {v.unit}
            {latest[v.code].origin === "fhir" && (
              <small className="block text-xs font-normal text-ink-muted">
                Desde FHIR
              </small>
            )}
          </td>
          <td>{dateTime(latest[v.code].observed_at)}</td>
        </tr>
      ))}
    </DataTable>
  ) : (
    <EmptyState title="Sin mediciones registradas" />
  );
}
export function EncounterTimeline({ encounters }: { encounters: Encounter[] }) {
  return encounters.length ? (
    <ol className="divide-y divide-line px-5">
      <MotionItems>
        {[...encounters]
          .sort((a, b) => b.started_at.localeCompare(a.started_at))
          .map((encounter) => (
            <li key={encounter.id} className="py-3">
              <time className="text-xs text-ink-muted">
                {dateTime(encounter.started_at)}
              </time>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">
                  {encounterClasses[encounter.encounter_class]}
                </h3>
                <PriorityBadge priority={encounter.priority} />
              </div>
              <p className="mt-1 break-words text-sm text-ink-muted">
                {encounter.reason_text || "Sin motivo registrado"}
              </p>
              <p className="text-xs text-ink-muted">
                {encounterStatuses[encounter.status]}
              </p>
            </li>
          ))}
      </MotionItems>
    </ol>
  ) : (
    <EmptyState title="Sin atenciones registradas" />
  );
}
export function PatientRecordView({
  patient,
  encounters,
  observations,
  showName = true,
  responsible,
}: {
  patient: Patient;
  encounters: Encounter[];
  observations: Observation[];
  showName?: boolean;
  responsible?: ReactNode;
}) {
  return (
    <div className="space-y-5">
      <PatientDetails
        patient={patient}
        showName={showName}
        responsible={responsible}
      />
      <Panel
        title="Últimas mediciones"
        description="El último valor de cada medición entre los 100 registros más recientes."
      >
        <Measurements observations={observations} />
      </Panel>
      <Panel title="Atenciones">
        <EncounterTimeline encounters={encounters} />
      </Panel>
    </div>
  );
}
