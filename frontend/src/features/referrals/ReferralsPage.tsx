import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { referralsApi, type Referral } from "../../api/referrals";
import type { Role } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import {
  Action,
  DataTable,
  Loading,
  Panel,
  SelectField,
  useToast,
} from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canAnswerReferral } from "../auth/permissions";
import { usePending } from "../alerts/useAlerts";
import { PatientRecordDialog } from "../patients/PatientRecordDialog";
import { useReferrals } from "./useReferrals";
import {
  isStale,
  RejectDialog,
  ReportDialog,
  useReferralFailure,
  useRefreshReferrals,
} from "./ReferralDialogs";
import {
  ReferralChip,
  ReferralOutcome,
  referralMoment,
  referralStates,
} from "./ReferralParts";

type Answer = { kind: "reject" | "report"; referral: Referral };

// What each role is looking at, in the words of the screen.
const views: Partial<Record<Role, { title: string; description: string }>> = {
  SPECIALIST: {
    title: "Recibidas",
    description:
      "Remisiones que otros médicos te enviaron. Mientras no la rechaces, puedes abrir la historia del paciente aunque sea de otra IPS.",
  },
  IPS_CLINICAL_OPERATOR: {
    title: "Enviadas",
    description:
      "Remisiones que enviaste. El estado cambia solo cuando el especialista responde.",
  },
  ADMIN: {
    title: "Todas las remisiones",
    description: "Todas las remisiones de la red, de cualquier IPS.",
  },
};

function AcceptButton({ referral }: { referral: Referral }) {
  const toast = useToast();
  const failed = useReferralFailure();
  const refresh = useRefreshReferrals();
  const mutation = useMutation({
    mutationFn: () => referralsApi.accept(referral.id),
    onSuccess: () => toast(`Remisión de ${referral.paciente_nombre} aceptada.`),
    onError: async (error) => {
      await failed(error);
      if (!isStale(error)) toast(error.message, "error");
    },
    // The list and the counters also refresh from the live event; this keeps the
    // screen right if the channel is down.
    onSettled: () => void refresh(),
  });
  return (
    <Action
      label="Aceptar"
      aria-label={`Aceptar la remisión de ${referral.paciente_nombre}`}
      loading={mutation.isPending}
      disabled={mutation.isPending}
      onClick={() => mutation.mutate()}
    />
  );
}

function RowActions({
  referral,
  answering,
  onOpen,
  onAnswer,
}: {
  referral: Referral;
  answering: boolean;
  onOpen: () => void;
  onAnswer: (answer: Answer) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      <Action
        secondary
        label="Ver paciente"
        aria-label={`Ver paciente ${referral.paciente_nombre}`}
        onClick={onOpen}
      />
      {answering && referral.estado === "pendiente" && (
        <>
          <AcceptButton referral={referral} />
          <Action
            secondary
            danger
            label="Rechazar"
            aria-label={`Rechazar la remisión de ${referral.paciente_nombre}`}
            onClick={() => onAnswer({ kind: "reject", referral })}
          />
        </>
      )}
      {answering && referral.estado === "aceptada" && (
        <Action
          label="Escribir informe"
          aria-label={`Escribir informe de ${referral.paciente_nombre}`}
          onClick={() => onAnswer({ kind: "report", referral })}
        />
      )}
    </div>
  );
}

export function ReferralTable({
  referrals,
  role,
  onOpen,
  onAnswer,
}: {
  referrals: Referral[];
  role: Role;
  onOpen: (patientId: string) => void;
  onAnswer: (answer: Answer) => void;
}) {
  const specialist = role === "SPECIALIST";
  const answering = canAnswerReferral(role);
  return (
    <DataTable
      stack
      caption="Remisiones"
      headers={[
        "Paciente",
        "Estado",
        specialist ? "De" : "Especialista",
        "Motivo",
        "Última novedad",
        "Acciones",
      ]}
    >
      {referrals.map((referral) => (
        <tr key={referral.id}>
          <td className="font-semibold">{referral.paciente_nombre}</td>
          <td>
            <ReferralChip estado={referral.estado} />
          </td>
          <td>
            {specialist ? (
              referral.remitente_nombre
            ) : (
              <>
                {referral.especialista_nombre}
                {referral.especialista_organizacion && (
                  <small className="block text-ink-muted">
                    {referral.especialista_organizacion}
                  </small>
                )}
                {role === "ADMIN" && (
                  <small className="block text-ink-muted">
                    De {referral.remitente_nombre}
                  </small>
                )}
              </>
            )}
          </td>
          <td>
            <span className="block max-w-sm whitespace-normal break-words">
              {referral.motivo}
            </span>
            <ReferralOutcome referral={referral} />
          </td>
          <td className="tabular-nums">{referralMoment(referral)}</td>
          <td>
            <RowActions
              referral={referral}
              answering={answering}
              onOpen={() => onOpen(referral.paciente_id)}
              onAnswer={onAnswer}
            />
          </td>
        </tr>
      ))}
    </DataTable>
  );
}

export function ReferralsPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const [estado, setEstado] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const pending = usePending(user!.role === "SPECIALIST");
  const view = views[user!.role] ?? views.ADMIN!;
  const query = useReferrals();
  const shown = (query.data ?? []).filter(
    (referral) => !estado || referral.estado === estado,
  );
  const waiting = pending.data?.remisiones_recibidas ?? 0;
  const patientId = params.get("patient");
  const setPatient = (id: string | null) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id) next.set("patient", id);
      else next.delete("patient");
      return next;
    });
  return (
    <>
      <Panel
        title={view.title}
        description={view.description}
        action={
          user!.role === "SPECIALIST" && waiting > 0 ? (
            <span className="rounded-full bg-bed-cleaning-tint px-2 py-0.5 text-xs font-semibold text-bed-cleaning-ink tabular-nums">
              {waiting} por responder
            </span>
          ) : undefined
        }
      >
        <div className="border-b border-line px-5 py-3">
          <div className="w-56 max-w-full">
            <SelectField
              label="Estado"
              value={estado}
              onChange={(event) => setEstado(event.target.value)}
            >
              <option value="">Todos</option>
              {Object.entries(referralStates).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </SelectField>
          </div>
        </div>
        {query.error ? (
          <ErrorState error={query.error} retry={query.refetch} />
        ) : !query.data ? (
          <Loading />
        ) : shown.length ? (
          <ReferralTable
            referrals={shown}
            role={user!.role}
            onOpen={setPatient}
            onAnswer={setAnswer}
          />
        ) : (
          <EmptyState
            title={
              estado
                ? "No hay remisiones con ese estado"
                : "No hay remisiones todavía"
            }
          >
            {user!.role === "SPECIALIST"
              ? "Cuando un médico te remita un paciente, aparece aquí al instante."
              : "Las remisiones nuevas aparecen aquí cuando se envían."}
          </EmptyState>
        )}
      </Panel>
      {answer?.kind === "reject" && (
        <RejectDialog
          referral={answer.referral}
          onClose={() => setAnswer(null)}
        />
      )}
      {answer?.kind === "report" && (
        <ReportDialog
          referral={answer.referral}
          onClose={() => setAnswer(null)}
        />
      )}
      {patientId && (
        <PatientRecordDialog
          patientId={patientId}
          onClose={() => setPatient(null)}
        />
      )}
    </>
  );
}
