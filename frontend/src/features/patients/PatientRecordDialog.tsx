import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Study } from "../../api/types";
import { patientsApi } from "../../api/patients";
import { imagingApi } from "../../api/imaging";
import { useAuth } from "../auth/AuthProvider";
import {
  canCreate,
  canDownloadRecord,
  canEditPatient,
  canRefer,
  canRegisterEncounter,
} from "../auth/permissions";
import { Action, Loading, Panel } from "../../components/ui";
import { Drawer } from "../../components/Drawer";
import { ErrorState } from "../../components/ErrorState";
import { PatientRecordView } from "./PatientRecordView";
import { PatientFormDialog } from "./PatientFormDialog";
import { EncounterDialog } from "./EncounterDialog";
import { RequestDialog } from "../queue/RequestDialog";
import { StudyList } from "../imaging/StudyList";
import { ImageViewer } from "../imaging/ImageViewer";
import { AiAnalysisSection } from "../ai/AiAnalysisSection";
import { RecordDownload } from "../ai/RecordDownload";
import { FhirExport } from "./FhirExport";
import { ResponsibleClinician } from "./ResponsibleClinician";
import { PatientReferrals } from "../referrals/PatientReferrals";
import { ReferDialog } from "../referrals/ReferralDialogs";
export function PatientRecordDialog({
  patientId,
  onClose,
}: {
  patientId: string;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const [mode, setMode] = useState("record");
  const [study, setStudy] = useState<Study | null>(null);
  const record = useQuery({
    queryKey: ["patient-record", patientId],
    queryFn: async ({ signal }) => {
      const [patient, encounters, observations] = await Promise.all([
        patientsApi.get(patientId, signal),
        patientsApi.encounters(patientId, signal),
        patientsApi.observations(patientId, signal),
      ]);
      return { patient, encounters, observations: observations.items };
    },
  });
  const studies = useQuery({
    queryKey: ["studies", patientId],
    queryFn: ({ signal }) => imagingApi.studies(patientId, signal),
  });
  const full = record.data?.patient
    ? `${record.data.patient.first_name} ${record.data.patient.last_name}`
    : "Ficha del paciente";
  const canAct = !!record.data;
  const hasActions =
    canAct &&
    (canCreate(user!.role) ||
      canEditPatient(user!, record.data!.patient) ||
      canRefer(user!.role) ||
      canDownloadRecord(user!.role));
  const footer = hasActions ? (
    <>
      <RecordDownload
        patientId={patientId}
        fileName={`historia-clinica-${record.data!.patient.document_number}.pdf`}
      />
      <FhirExport
        patientId={patientId}
        fileName={`paciente-${record.data!.patient.document_number}-fhir.json`}
      />
      {canEditPatient(user!, record.data!.patient) && (
        <Action
          secondary
          label="Editar datos"
          onClick={() => setMode("edit")}
        />
      )}
      {canRefer(user!.role) && (
        <Action
          secondary
          label="Remitir a especialista"
          onClick={() => setMode("refer")}
        />
      )}
      {canRegisterEncounter(user!.role) && (
        <Action
          secondary
          label="Registrar atención"
          onClick={() => setMode("encounter")}
        />
      )}
      {canCreate(user!.role) && (
        <Action label="Solicitar cama" onClick={() => setMode("request")} />
      )}
    </>
  ) : undefined;
  // The record stays in the panel; edits, requests and the viewer open above it.
  return (
    <>
      <Drawer
        visible
        wide
        onClose={onClose}
        title={full}
        label="Ficha del paciente"
        footer={footer}
      >
        {record.error ? (
          <ErrorState error={record.error} retry={record.refetch} />
        ) : !record.data ? (
          <Loading />
        ) : (
          <>
            <PatientRecordView
              {...record.data}
              showName={false}
              responsible={
                <ResponsibleClinician patient={record.data.patient} />
              }
            />
            <div className="mt-5">
              <AiAnalysisSection patientId={patientId} />
            </div>
            <div className="mt-5">
              <PatientReferrals patientId={patientId} />
            </div>
            <div className="mt-5">
              <Panel title="Estudios de imagen">
                {studies.error ? (
                  <ErrorState error={studies.error} retry={studies.refetch} />
                ) : !studies.data ? (
                  <Loading />
                ) : (
                  <StudyList
                    studies={studies.data.items}
                    canUpload={canRegisterEncounter(user!.role)}
                    onView={setStudy}
                  />
                )}
              </Panel>
            </div>
          </>
        )}
      </Drawer>
      {record.data && mode === "edit" && (
        <PatientFormDialog
          patient={record.data.patient}
          onClose={() => setMode("record")}
          onSaved={() => setMode("record")}
        />
      )}
      {record.data && mode === "encounter" && (
        <EncounterDialog
          patient={record.data.patient}
          onClose={() => setMode("record")}
          onSaved={() => setMode("record")}
        />
      )}
      {record.data && mode === "refer" && (
        <ReferDialog
          patient={record.data.patient}
          onClose={() => setMode("record")}
        />
      )}
      {record.data && mode === "request" && (
        <RequestDialog
          patient={record.data.patient}
          onClose={() => setMode("record")}
        />
      )}
      {study && (
        <ImageViewer
          studyId={study.id}
          title={study.description || "Imágenes del estudio"}
          onClose={() => setStudy(null)}
        />
      )}
    </>
  );
}
