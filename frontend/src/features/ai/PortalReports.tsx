import { useQuery } from "@tanstack/react-query";
import { aiApi } from "../../api/ai";
import { dateTime } from "../../components/format";
import { Loading, Panel } from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { Probability, Signature, TopFeatures } from "./reportParts";
import { RecordDownload } from "./RecordDownload";
/** The patient's own approved AI reports and the PDF of their clinical record. */
export function PortalReports({
  patientId,
  documentNumber,
}: {
  patientId: string;
  documentNumber: string;
}) {
  const record = useQuery({
    queryKey: ["portal-record", patientId],
    queryFn: ({ signal }) => aiApi.record(patientId, signal),
  });
  return (
    <Panel
      title="Mis reportes de IA"
      description="Solo aparecen los análisis que un médico o especialista revisó y aprobó."
      action={
        <RecordDownload
          patientId={patientId}
          fileName={`historia-clinica-${documentNumber}.pdf`}
        />
      }
    >
      {record.error ? (
        <ErrorState error={record.error} retry={record.refetch} />
      ) : !record.data ? (
        <Loading />
      ) : record.data.reportes_ia.length ? (
        <ul className="divide-y divide-line">
          {record.data.reportes_ia.map((report) => (
            <li key={report.id} className="space-y-3 px-5 py-4">
              <time className="block text-xs text-ink-muted tabular-nums">
                Análisis del {dateTime(report.created_at)}
              </time>
              <Probability
                value={report.probability}
                critical={report.nivel === "critico"}
              />
              <dl className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <TopFeatures features={report.top_features} />
                </div>
                <Signature
                  estado={report.estado}
                  signedBy={report.firmado_por}
                  reviewedAt={report.reviewed_at}
                  observaciones={report.observaciones}
                />
              </dl>
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-5 py-6 text-sm text-ink-muted">
          Aún no tienes reportes de IA aprobados. Aparecen aquí cuando un médico
          o especialista los revisa y los aprueba.
        </p>
      )}
    </Panel>
  );
}
