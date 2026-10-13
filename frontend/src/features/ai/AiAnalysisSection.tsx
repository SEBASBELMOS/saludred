import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { aiApi, type AiReport } from "../../api/ai";
import { ApiError } from "../../api/client";
import { useInvalidateClinical } from "../../api/query";
import { dateTime } from "../../components/format";
import {
  Action,
  Field,
  FormActions,
  Loading,
  Notice,
  Panel,
  SelectField,
  TextField,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { useAuth } from "../auth/AuthProvider";
import { canReviewAi } from "../auth/permissions";
import { PayloadView } from "./PayloadView";
import {
  EstadoChip,
  Probability,
  Signature,
  TopFeatures,
  imputedList,
} from "./reportParts";
export const unavailableMessage =
  "El servicio de IA no responde. El reporte no se creó; intenta de nuevo en unos minutos.";
export const missingDataMessage =
  "Faltan datos clínicos para el análisis. Registra al menos 3 de estas mediciones: temperatura, frecuencia cardíaca, frecuencia respiratoria, saturación de oxígeno, presión sistólica o diastólica. Luego pide el análisis otra vez.";
export const conflictMessage =
  "Otro clínico ya revisó este reporte. Actualizamos la lista para que veas su decisión.";
export function requestErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 503) return unavailableMessage;
    if (error.status === 422) return missingDataMessage;
    return error.message;
  }
  return "No se pudo pedir el análisis. Intenta de nuevo.";
}
const triageOptions = [
  ["1", "1 (más urgente)"],
  ["2", "2"],
  ["3", "3"],
  ["4", "4"],
  ["5", "5 (menos urgente)"],
];
function RequestAnalysis({ patientId }: { patientId: string }) {
  const [triage, setTriage] = useState("");
  const invalidate = useInvalidateClinical();
  const toast = useToast();
  const mutation = useMutation({
    mutationFn: () =>
      aiApi.analyze(patientId, triage ? Number(triage) : undefined),
    onSuccess: async () => {
      toast("Análisis creado. Queda pendiente hasta que lo firmes.");
      await invalidate();
    },
  });
  return (
    <form
      className="border-b border-line px-5 py-4"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56 max-w-full">
          <SelectField
            label="Nivel de triage ESI (opcional)"
            value={triage}
            onChange={(event) => setTriage(event.target.value)}
          >
            <option value="">Sin informar</option>
            {triageOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </SelectField>
        </div>
        <Action
          type="submit"
          label="Solicitar análisis"
          loading={mutation.isPending}
          disabled={mutation.isPending}
        />
      </div>
      <p className="mt-2 text-xs text-ink-muted">
        El análisis usa las últimas mediciones registradas. Si no informas el
        nivel de triage, el modelo usa un valor típico y lo señala como
        imputado.
      </p>
      {mutation.error && <Notice>{requestErrorMessage(mutation.error)}</Notice>}
    </form>
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
  const [motivo, setMotivo] = useState("");
  const [observaciones, setObservaciones] = useState("");
  const invalidate = useInvalidateClinical();
  const toast = useToast();
  const approving = decision === "approve";
  const mutation = useMutation({
    mutationFn: () =>
      approving
        ? aiApi.approve(report.id, observaciones.trim())
        : aiApi.reject(report.id, motivo.trim(), observaciones.trim()),
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
  const blocked = !approving && motivo.trim().length < 3;
  return (
    <form
      aria-label={approving ? "Aprobar reporte" : "Rechazar reporte"}
      className="rounded-lg bg-surface-sunken p-4"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (!blocked) mutation.mutate();
      }}
    >
      {approving ? (
        <p className="mb-3 text-sm">
          Al aprobar, el reporte entra a la historia clínica y su probabilidad
          pasa a ser el puntaje de criticidad de la cola de camas, si el
          paciente tiene una solicitud abierta.
        </p>
      ) : (
        <>
          <p className="mb-3 text-sm">
            El rechazo no cambia la cola. El reporte queda como evidencia, fuera
            de la historia clínica.
          </p>
          <Field
            label="Motivo del rechazo"
            value={motivo}
            required
            maxLength={500}
            autoComplete="off"
            onChange={(event) => setMotivo(event.target.value)}
          />
          <p className="mb-3 mt-1 text-xs text-ink-muted">
            Escribe al menos 3 caracteres. Queda registrado con tu firma.
          </p>
        </>
      )}
      <TextField
        label="Observaciones (opcional)"
        value={observaciones}
        maxLength={1000}
        rows={3}
        onChange={(event) => setObservaciones(event.target.value)}
      />
      {mutation.error && <Notice>{mutation.error.message}</Notice>}
      <FormActions
        label={approving ? "Confirmar aprobación" : "Confirmar rechazo"}
        pending={mutation.isPending}
        disabled={blocked}
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
        <time className="text-xs text-ink-muted tabular-nums">
          Solicitado {dateTime(report.created_at)}
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
export function AiAnalysisSection({ patientId }: { patientId: string }) {
  const { user } = useAuth();
  const canReview = canReviewAi(user!.role);
  const reports = useQuery({
    queryKey: ["ai-reports", "patient", patientId],
    queryFn: ({ signal }) => aiApi.reports({ patient_id: patientId }, signal),
  });
  return (
    <Panel
      title="Análisis de IA"
      description="La IA sugiere; el reporte solo entra a la historia cuando un médico o especialista lo aprueba."
    >
      {canReview && <RequestAnalysis patientId={patientId} />}
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
          Este paciente no tiene análisis de IA.
          {canReview
            ? " Solicita uno para que el modelo sugiera su nivel de criticidad."
            : " Los pide un médico o especialista de su IPS."}
        </p>
      )}
    </Panel>
  );
}
