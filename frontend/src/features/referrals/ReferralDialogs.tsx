import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { referralsApi, type Referral } from "../../api/referrals";
import type { Patient } from "../../api/types";
import { PENDING_KEY } from "../alerts/useAlerts";
import { fullName } from "../../components/format";
import {
  FormActions,
  Modal,
  Notice,
  SelectField,
  TextField,
  useToast,
} from "../../components/ui";
import { REFERRALS_KEY } from "./keys";

// One refresh for every list and counter a referral touches.
export function useRefreshReferrals() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: REFERRALS_KEY }),
      client.invalidateQueries({ queryKey: PENDING_KEY }),
    ]);
}

export const staleReferralMessage =
  "La remisión cambió de estado mientras la veías. Actualizamos la lista.";

// A 409 means the referral is no longer in the state the screen assumed.
export function useReferralFailure(onStale?: () => void) {
  const toast = useToast();
  const refresh = useRefreshReferrals();
  return async (error: Error) => {
    if (error instanceof ApiError && error.status === 409) {
      toast(staleReferralMessage, "warn");
      onStale?.();
      await refresh();
    }
  };
}

export const isStale = (error: Error | null) =>
  error instanceof ApiError && error.status === 409;

export function ReferDialog({
  patient,
  onClose,
}: {
  patient: Patient;
  onClose: () => void;
}) {
  const [specialist, setSpecialist] = useState("");
  const [motivo, setMotivo] = useState("");
  const toast = useToast();
  const refresh = useRefreshReferrals();
  const specialists = useQuery({
    queryKey: ["referral-specialists"],
    queryFn: ({ signal }) => referralsApi.specialists(signal),
    meta: { silent: true },
  });
  const mutation = useMutation({
    mutationFn: () =>
      referralsApi.create({
        paciente_id: patient.id,
        especialista_id: specialist,
        motivo: motivo.trim(),
      }),
    onSuccess: async (created) => {
      toast(`Remisión enviada a ${created.especialista_nombre}.`);
      onClose();
      await refresh();
    },
  });
  const blocked = !specialist || motivo.trim().length === 0;
  return (
    <Modal title="Remitir a especialista" onClose={onClose}>
      <p className="font-semibold">{fullName(patient)}</p>
      <p className="my-3 text-sm text-ink-muted">
        El especialista recibe el aviso al instante y, mientras la remisión no
        sea rechazada, puede leer la historia clínica de este paciente aunque
        trabaje en otra IPS.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) mutation.mutate();
        }}
      >
        {mutation.error && <Notice>{mutation.error.message}</Notice>}
        <SelectField
          label="Especialista"
          value={specialist}
          required
          disabled={!specialists.data}
          onChange={(event) => setSpecialist(event.target.value)}
        >
          <option value="">
            {specialists.error
              ? "No se pudo cargar la lista"
              : "Selecciona un especialista"}
          </option>
          {(specialists.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.nombre}
              {item.organizacion ? `, ${item.organizacion}` : ""}
            </option>
          ))}
        </SelectField>
        <div className="mt-4">
          <TextField
            label="Motivo de la remisión (obligatorio)"
            required
            maxLength={1000}
            rows={4}
            value={motivo}
            onChange={(event) => setMotivo(event.target.value)}
          />
        </div>
        <FormActions
          label="Enviar remisión"
          pending={mutation.isPending}
          disabled={blocked}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}

export function RejectDialog({
  referral,
  onClose,
}: {
  referral: Referral;
  onClose: () => void;
}) {
  const [motivo, setMotivo] = useState("");
  const toast = useToast();
  const refresh = useRefreshReferrals();
  const failed = useReferralFailure(onClose);
  const mutation = useMutation({
    mutationFn: () => referralsApi.reject(referral.id, motivo.trim()),
    onSuccess: async () => {
      toast("Remisión rechazada. El médico recibe tu motivo.");
      onClose();
      await refresh();
    },
    onError: failed,
  });
  const blocked = motivo.trim().length === 0;
  return (
    <Modal title="Rechazar remisión" onClose={onClose}>
      <p className="font-semibold">{referral.paciente_nombre}</p>
      <p className="my-3 text-sm text-ink-muted">
        Al rechazarla pierdes el acceso a la historia de este paciente y la
        remisión no se factura.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) mutation.mutate();
        }}
      >
        {mutation.error && !isStale(mutation.error) && (
          <Notice>{mutation.error.message}</Notice>
        )}
        <TextField
          label="Motivo del rechazo (obligatorio)"
          required
          maxLength={500}
          rows={3}
          value={motivo}
          onChange={(event) => setMotivo(event.target.value)}
        />
        <FormActions
          danger
          label="Rechazar remisión"
          pending={mutation.isPending}
          disabled={blocked}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}

export function ReportDialog({
  referral,
  onClose,
}: {
  referral: Referral;
  onClose: () => void;
}) {
  const [informe, setInforme] = useState("");
  const toast = useToast();
  const refresh = useRefreshReferrals();
  const failed = useReferralFailure(onClose);
  const mutation = useMutation({
    mutationFn: () => referralsApi.report(referral.id, informe.trim()),
    onSuccess: async () => {
      toast("Informe enviado. La remisión quedó completada.");
      onClose();
      await refresh();
    },
    onError: failed,
  });
  const blocked = informe.trim().length === 0;
  return (
    <Modal title="Escribir informe" onClose={onClose} wide>
      <p className="font-semibold">{referral.paciente_nombre}</p>
      <p className="my-3 text-sm text-ink-muted">
        Motivo de la remisión: {referral.motivo}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) mutation.mutate();
        }}
      >
        {mutation.error && !isStale(mutation.error) && (
          <Notice>{mutation.error.message}</Notice>
        )}
        <TextField
          label="Informe del especialista (obligatorio)"
          required
          maxLength={10000}
          rows={8}
          value={informe}
          onChange={(event) => setInforme(event.target.value)}
        />
        <FormActions
          label="Enviar informe"
          pending={mutation.isPending}
          disabled={blocked}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}
