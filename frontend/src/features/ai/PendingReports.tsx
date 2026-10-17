import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { aiApi } from "../../api/ai";
import type { AiReport } from "../../api/ai";
import { patientsApi } from "../../api/patients";
import { dateTime, fullName } from "../../components/format";
import { Action, Loading, Panel } from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../auth/AuthProvider";
import { canReviewAi } from "../auth/permissions";
import { usePending } from "../alerts/useAlerts";
import { NivelChip } from "./reportParts";
import { percent } from "./words";
const visibleRows = 8;
// Shared by the inbox and the sidebar badge; both read the same cache entry.
export const usePendingReports = (enabled: boolean) =>
  useQuery({
    queryKey: ["ai-reports", "pending"],
    queryFn: ({ signal }) => aiApi.reports({ estado: "pendiente" }, signal),
    enabled,
    meta: { silent: true },
  });
function Row({
  report,
  onOpen,
}: {
  report: AiReport;
  onOpen: (patientId: string) => void;
}) {
  const patient = useQuery({
    queryKey: ["patient", report.patient_id],
    queryFn: ({ signal }) => patientsApi.get(report.patient_id, signal),
  });
  const name = patient.data ? fullName(patient.data) : "Paciente";
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-3">
      <div className="min-w-0">
        <strong className="block break-words text-sm">
          {patient.data
            ? name
            : patient.error
              ? "Paciente no disponible"
              : "Cargando paciente…"}
        </strong>
        <span className="text-xs text-ink-muted">
          {patient.data &&
            `${patient.data.document_type} ${patient.data.document_number}, `}
          {report.origen === "manual" ? "solicitado" : "generado"}{" "}
          <span className="tabular-nums">{dateTime(report.created_at)}</span>
        </span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-sm font-semibold tabular-nums">
          {percent(report.probability)}
        </span>
        <NivelChip critical={report.critico} />
        {!patient.error && (
          <Action
            secondary
            label="Revisar"
            aria-label={`Revisar reporte de ${name}`}
            onClick={() => onOpen(report.patient_id)}
          />
        )}
      </div>
    </li>
  );
}
/** Compact list of reports waiting for a signature. Each row opens the patient drawer. */
export function PendingReports() {
  const { user } = useAuth();
  const [, setParams] = useSearchParams();
  const allowed = !!user && canReviewAi(user.role);
  const query = usePendingReports(allowed);
  // The same counter as the sidebar badge (GET /pendientes, pushed live).
  const counter = usePending(allowed);
  if (!allowed) return null;
  const open = (patientId: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("patient", patientId);
      return next;
    });
  const reports = query.data ?? [];
  const waiting = counter.data?.reportes_por_revisar ?? reports.length;
  return (
    <div className="mb-6">
      <Panel
        title="Reportes por revisar"
        description="Análisis de IA que esperan la firma de un médico o especialista."
        action={
          waiting > 0 && (
            <span className="rounded-full bg-bed-cleaning-tint px-2 py-0.5 text-xs font-semibold text-bed-cleaning-ink tabular-nums">
              {waiting} {waiting === 1 ? "pendiente" : "pendientes"}
            </span>
          )
        }
      >
        {query.error ? (
          <ErrorState error={query.error} retry={query.refetch} />
        ) : !query.data ? (
          <Loading />
        ) : reports.length ? (
          <>
            <ul className="divide-y divide-line">
              {reports.slice(0, visibleRows).map((report) => (
                <Row key={report.id} report={report} onOpen={open} />
              ))}
            </ul>
            {reports.length > visibleRows && (
              <p className="border-t border-line px-5 py-3 text-xs text-ink-muted">
                Se muestran {visibleRows} de {reports.length}. Firma estos para
                ver los demás.
              </p>
            )}
          </>
        ) : (
          <p className="px-5 py-4 text-sm text-ink-muted">
            <strong className="font-semibold text-ink">
              No hay reportes por revisar.
            </strong>{" "}
            Cuando el sistema genere un análisis de IA, aparece aquí hasta que
            se firme.
          </p>
        )}
      </Panel>
    </div>
  );
}
