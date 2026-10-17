import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { aiApi, type AiReport } from "../../api/ai";
import { ApiError } from "../../api/client";
import { useInvalidateClinical } from "../../api/query";
import { dateTime } from "../../components/format";
import {
  Action,
  FormActions,
  Loading,
  Notice,
  Panel,
  TextField,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../auth/AuthProvider";
import { canReviewAi } from "../auth/permissions";
import { vitalCatalog } from "../patients/validation";
import { PayloadView } from "./PayloadView";
import {
  EstadoChip,
  OrigenChip,
  Probability,
  Signature,
  TopFeatures,
  imputedList,
} from "./reportParts";
export const conflictMessage =
  "Otro clínico ya revisó este reporte. Actualizamos la lista para que veas su decisión.";
export const conceptMinLength = 10;
export const conceptTooShortMessage = `El concepto clínico debe tener al menos ${conceptMinLength} caracteres.`;
export const autoStatusMessage =
  "El análisis se genera automáticamente cuando el paciente tiene signos vitales registrados.";

// The six vital signs the AI service waits for before it analyzes on its own.
const coreVitalCodes = [
  "8867-4",
  "9279-1",
  "59408-5",
  "8480-6",
  "8462-4",
  "8310-5",
];
interface Measured {
  code: string;
  value_numeric?: number | null;
  status?: string;
}
/** Names of the core vital signs that have no usable measurement yet. */
export function missingVitals(observations: Measured[]) {
  const present = new Set(
    observations
      .filter((o) => o.value_numeric != null && o.status !== "cancelled")
      .map((o) => o.code),
  );
  return coreVitalCodes
    .filter((code) => !present.has(code))
    .map(
      (code) =>
        vitalCatalog.find((vital) => vital.code === code)?.name.toLowerCase() ??
        code,
    );
}
// A quiet line, not a form: nobody asks for the analysis, it just appears.
function AutoStatus({ observations }: { observations?: Measured[] }) {
  const missing = observations ? missingVitals(observations) : [];
  return (
    <p
      role="status"
      className="border-b border-line px-5 py-3 text-xs text-ink-muted"
    >
      {missing.length
        ? `Faltan mediciones: ${missing.join(", ")}.`
        : autoStatusMessage}
    </p>
  );
}
type Decision = "approve" | "reject";
function ReviewForm({
  report,
  decision,
  onClose,
  onConflict,
}: {
  report: AiReport;
  decision: Decision;
  onClose: () => void;
  onConflict: () => void;
}) {
  // One required text per action: the clinician's judgment. On approval it is
  // the note of the signature; on rejection it is the reason.
  const [concept, setConcept] = useState("");
  const invalidate = useInvalidateClinical();
  const toast = useToast();
  const approving = decision === "approve";
  const text = concept.trim();
  const tooShort = text.length < conceptMinLength;
  const mutation = useMutation({
    mutationFn: () =>
      approving
        ? aiApi.approve(report.id, text)
        : aiApi.reject(report.id, text, ""),
    onSuccess: async (signed) => {
      toast(
        approving
          ? signed.solicitud_cama_id
            ? "Reporte aprobado. Su puntaje ya alimenta la cola de camas."
            : "Reporte aprobado y agregado a la historia clínica."
          : "Reporte rechazado. Queda como evidencia, fuera de la historia clínica.",
      );
      onClose();
      await invalidate();
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        onConflict();
        await invalidate();
      }
    },
  });
  return (
    <form
      aria-label={approving ? "Aprobar reporte" : "Rechazar reporte"}
      className="rounded-lg bg-surface-sunken p-4"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (!tooShort) mutation.mutate();
      }}
    >
      <p className="mb-3 text-sm">
        {approving
          ? "Al aprobar, el reporte entra a la historia clínica y su probabilidad pasa a ser el puntaje de criticidad de la cola de camas, si el paciente tiene una solicitud abierta."
          : "El rechazo no cambia la cola. El reporte queda como evidencia, fuera de la historia clínica."}
      </p>
      <TextField
        label="Concepto clínico"
        value={concept}
        required
        maxLength={approving ? 1000 : 500}
        rows={4}
        aria-invalid={concept.length > 0 && tooShort}
        aria-describedby="concept-help"
        onChange={(event) => setConcept(event.target.value)}
      />
      <p
        id="concept-help"
        className={`mt-1 text-xs ${concept.length > 0 && tooShort ? "font-semibold text-critical-ink" : "text-ink-muted"}`}
      >
        {concept.length > 0 && tooShort
          ? conceptTooShortMessage
          : `Obligatorio. Escribe tu juicio clínico sobre este análisis (al menos ${conceptMinLength} caracteres). Queda registrado con tu firma.`}
      </p>
      {mutation.error && <Notice>{mutation.error.message}</Notice>}
      <FormActions
        label={approving ? "Confirmar aprobación" : "Confirmar rechazo"}
        pending={mutation.isPending}
        disabled={tooShort}
        onClose={onClose}
      />
    </form>
  );
}
function ReportItem({
  report,
  canReview,
}: {
  report: AiReport;
  canReview: boolean;
}) {
  const [payload, setPayload] = useState(false);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [stale, setStale] = useState(false);
  const review = canReview && report.estado === "pendiente";
  return (
    <li className="space-y-3 px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <EstadoChip estado={report.estado} />
        <OrigenChip origen={report.origen} />
        <time className="text-xs text-ink-muted tabular-nums">
          {report.origen === "manual" ? "Solicitado" : "Generado"}{" "}
          {dateTime(report.created_at)}
        </time>
      </div>
      <Probability
        value={report.probability}
        critical={report.critico}
        threshold={report.threshold}
      />
      <dl className="grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-xs text-ink-muted">Versión del modelo</dt>
          <dd className="mt-1 break-all text-sm tabular-nums">
            {report.model_version}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-ink-muted">Variables imputadas</dt>
          <dd className="mt-1 text-sm">{imputedList(report.imputed)}</dd>
        </div>
        <div className="sm:col-span-2">
          <TopFeatures features={report.top_features} />
        </div>
        <Signature
          estado={report.estado}
          signedBy={report.firmado_por}
          reviewedAt={report.reviewed_at}
          observaciones={report.observaciones}
          motivo={report.motivo_rechazo}
        />
      </dl>
      {report.estado === "pendiente" && !canReview && (
        <p className="text-sm text-ink-muted">
          Falta la firma de un médico o especialista. Mientras tanto no forma
          parte de la historia clínica.
        </p>
      )}
      {stale && <Notice amber>{conflictMessage}</Notice>}
      <div className="flex flex-wrap gap-2">
        <Action
          secondary
          label="Ver datos enviados al modelo"
          aria-expanded={payload}
          onClick={() => setPayload((open) => !open)}
        />
        {review && !decision && (
          <>
            <Action label="Aprobar" onClick={() => setDecision("approve")} />
            <Action
              secondary
              label="Rechazar"
              onClick={() => setDecision("reject")}
            />
          </>
        )}
      </div>
      {payload && <PayloadView reportId={report.id} />}
      {review && decision && (
        <ReviewForm
          report={report}
          decision={decision}
          onClose={() => setDecision(null)}
          onConflict={() => {
            setDecision(null);
            setStale(true);
          }}
        />
      )}
    </li>
  );
}
export function AiAnalysisSection({
  patientId,
  observations,
}: {
  patientId: string;
  // The patient's measurements, to say which vital signs are still missing.
  observations?: Measured[];
}) {
  const { user } = useAuth();
  const canReview = canReviewAi(user!.role);
  // Live events (analisis_ia_pendiente, pendientes_actualizados) invalidate the
  // ["ai-reports"] prefix, so a report that arrives shows up here with no reload.
  const reports = useQuery({
    queryKey: ["ai-reports", "patient", patientId],
    queryFn: ({ signal }) => aiApi.reports({ patient_id: patientId }, signal),
  });
  return (
    <Panel
      title="Análisis de IA"
      description="La IA sugiere; el reporte solo entra a la historia cuando un médico o especialista lo aprueba."
    >
      <AutoStatus observations={observations} />
      {reports.error ? (
        <ErrorState error={reports.error} retry={reports.refetch} />
      ) : !reports.data ? (
        <Loading />
      ) : reports.data.length ? (
        <ul className="divide-y divide-line">
          {[...reports.data]
            .sort((a, b) => b.created_at.localeCompare(a.created_at))
            .map((report) => (
              <ReportItem
                key={report.id}
                report={report}
                canReview={canReview}
              />
            ))}
        </ul>
      ) : (
        <p className="px-5 py-6 text-sm text-ink-muted">
          Este paciente todavía no tiene análisis de IA. Aparece aquí solo, sin
          recargar, apenas el sistema lo genera.
        </p>
      )}
    </Panel>
  );
}
