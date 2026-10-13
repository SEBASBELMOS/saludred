import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { portalApi } from "../../api/portal";
import type { Study } from "../../api/types";
import { ErrorState } from "../../components/ErrorState";
import { Loading, Panel } from "../../components/ui";
import {
  PatientDetails,
  EncounterTimeline,
  Measurements,
} from "../patients/PatientRecordView";
import { StudyList } from "../imaging/StudyList";
import { ImageViewer } from "../imaging/ImageViewer";
import { PortalReports } from "../ai/PortalReports";
export function PortalPage() {
  const [study, setStudy] = useState<Study | null>(null);
  const record = useQuery({
    queryKey: ["portal"],
    queryFn: async ({ signal }) => {
      const [patient, encounters, observations] = await Promise.all([
        portalApi.patient(signal),
        portalApi.encounters(signal),
        portalApi.observations(signal),
      ]);
      return { patient, encounters, observations };
    },
  });
  const studies = useQuery({
    queryKey: ["portal-studies"],
    queryFn: ({ signal }) => portalApi.studies(signal),
  });
  if (record.error)
    return <ErrorState error={record.error} retry={record.refetch} />;
  if (!record.data) return <Loading />;
  return (
    <>
      <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5">
          <Panel title="Mi información personal">
            <PatientDetails patient={record.data.patient} />
          </Panel>
          <aside className="rounded-xl bg-surface-sunken p-5 text-sm leading-relaxed">
            Solo tú y el personal autorizado de la red pueden ver tu
            información. Las mediciones se presentan sin interpretación clínica.
          </aside>
        </div>
        <div className="min-w-0 space-y-5">
          <Panel
            title="Mis atenciones"
            description="Tu recorrido de atención, en orden."
          >
            <EncounterTimeline encounters={record.data.encounters} />
          </Panel>
          <Panel
            title="Mis mediciones"
            description="El último valor registrado de cada medición."
          >
            <Measurements observations={record.data.observations} />
          </Panel>
          <PortalReports
            patientId={record.data.patient.id}
            documentNumber={record.data.patient.document_number}
          />
          <Panel title="Mis estudios de imagen">
            {studies.error ? (
              <ErrorState error={studies.error} retry={studies.refetch} />
            ) : !studies.data ? (
              <Loading />
            ) : (
              <StudyList studies={studies.data} onView={setStudy} />
            )}
          </Panel>
        </div>
      </div>
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
