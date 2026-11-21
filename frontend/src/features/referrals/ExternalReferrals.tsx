import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import {
  externalReferralsApi,
  type ExternalPriority,
  type ExternalReferral,
  type ExternalReferralState,
} from "../../api/referrals";
import type { Patient } from "../../api/types";
import { fullName, dateTime } from "../../components/format";
import { ErrorState } from "../../components/ErrorState";
import {
  FormActions,
  Loading,
  Modal,
  Notice,
  Panel,
  SelectField,
  TextField,
  useToast,
} from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canRefer } from "../auth/permissions";
import { REFERRALS_KEY } from "./keys";
import { useRefreshReferrals } from "./ReferralDialogs";

// Same key prefix as every referral list: a live respuesta_socio event refreshes it.
const EXTERNAL_KEY = [...REFERRALS_KEY, "external"] as const;

export const externalStates: Record<ExternalReferralState, string> = {
  enviando: "Enviando",
  enviada: "Enviada",
  error_envio: "Error de envío",
  aceptada: "Aceptada",
  rechazada: "Rechazada",
  rechazada_por_validacion: "Rechazada por validación",
};
const stateTone: Record<ExternalReferralState, string> = {
  enviando: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  enviada: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  error_envio: "bg-critical-tint text-critical-ink",
  aceptada: "bg-bed-available-tint text-bed-available-ink",
  rechazada: "bg-surface-sunken text-ink-muted",
  rechazada_por_validacion: "bg-critical-tint text-critical-ink",
};
const priorities: Record<ExternalPriority, string> = {
  rutina: "Rutina",
  urgente: "Urgente",
  inmediata: "Inmediata",
};

export const noConsentMessage =
  "No hay un consentimiento vigente del paciente para compartir sus datos con esta institución. Regístralo antes de remitir.";

export function ExternalChip({ estado }: { estado: ExternalReferralState }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${stateTone[estado]}`}
    >
      {externalStates[estado]}
    </span>
  );
}

export function ExternalReferDialog({
  patient,
  onClose,
}: {
  patient: Patient;
  onClose: () => void;
}) {
  const [partner, setPartner] = useState("");
  const [servicio, setServicio] = useState("");
  const [motivo, setMotivo] = useState("");
  const [prioridad, setPrioridad] = useState<ExternalPriority>("rutina");
  const toast = useToast();
  const refresh = useRefreshReferrals();
  const destinations = useQuery({
    queryKey: ["external-destinations"],
    queryFn: ({ signal }) => externalReferralsApi.destinations(signal),
    meta: { silent: true },
  });
  const mutation = useMutation({
    mutationFn: () =>
      externalReferralsApi.create({
        paciente_id: patient.id,
        cliente_interop_id: partner,
        motivo: motivo.trim(),
        servicio: servicio.trim(),
        prioridad,
      }),
    onSuccess: async (created) => {
      toast(
        created.estado === "enviada"
          ? "Remisión enviada a la institución externa."
          : "La institución externa rechazó la remisión por validación.",
        created.estado === "enviada" ? "info" : "warn",
      );
      onClose();
      await refresh();
    },
  });
  const blocked = !partner || !servicio.trim() || !motivo.trim();
  const error = mutation.error;
  return (
    <Modal title="Remitir a otra institución" onClose={onClose}>
      <p className="font-semibold">{fullName(patient)}</p>
      <p className="my-3 text-sm text-ink-muted">
        Solo se envía un identificador federado, no el documento ni los datos de
        contacto del paciente. Se necesita su consentimiento vigente para esa
        institución.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) mutation.mutate();
        }}
      >
        {error && (
          <Notice>
            {error instanceof ApiError && error.status === 403
              ? noConsentMessage
              : error.message}
          </Notice>
        )}
        <SelectField
          label="Institución"
          value={partner}
          required
          disabled={!destinations.data}
          onChange={(event) => setPartner(event.target.value)}
        >
          <option value="">
            {destinations.error
              ? "No se pudo cargar la lista"
              : "Selecciona una institución"}
          </option>
          {(destinations.data ?? []).map((item) => (
            <option key={item.client_id} value={item.client_id}>
              {item.nombre}
            </option>
          ))}
        </SelectField>
        <div className="mt-4">
          <TextField
            label="Servicio o especialidad solicitada (obligatorio)"
            required
            maxLength={200}
            rows={1}
            value={servicio}
            onChange={(event) => setServicio(event.target.value)}
          />
        </div>
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
        <div className="mt-4">
          <SelectField
            label="Prioridad"
            value={prioridad}
            onChange={(event) =>
              setPrioridad(event.target.value as ExternalPriority)
            }
          >
            {Object.entries(priorities).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </SelectField>
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

function ExternalRow({ referral }: { referral: ExternalReferral }) {
  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <ExternalChip estado={referral.estado} />
        <span className="text-xs text-ink-muted">
          {referral.cliente_interop_id} · {priorities[referral.prioridad]}
        </span>
        <time
          dateTime={referral.creada_en}
          className="text-xs tabular-nums text-ink-muted"
        >
          {dateTime(referral.creada_en)}
        </time>
      </div>
      <p className="mt-2 break-words text-sm">
        <strong className="font-semibold">{referral.servicio}</strong>
      </p>
      <p className="mt-1 break-words text-sm text-ink-muted">{referral.motivo}</p>
      {referral.resumen_respuesta && (
        <p className="mt-2 break-words text-sm">
          <span className="text-xs text-ink-muted">Respuesta </span>
          <span className="block">{referral.resumen_respuesta}</span>
        </p>
      )}
    </li>
  );
}

/**
 * Referrals the doctor sent to other institutions. It lives under the referral
 * key prefix, so a live respuesta_socio event updates the status with no reload.
 * Pass ``patientId`` to show only the referrals of one patient.
 */
export function ExternalReferrals({ patientId }: { patientId?: string }) {
  const { user } = useAuth();
  const allowed = !!user && (canRefer(user.role) || user.role === "ADMIN");
  const query = useQuery({
    queryKey: EXTERNAL_KEY,
    queryFn: async ({ signal }) => {
      const list = await externalReferralsApi.list(signal);
      return Array.isArray(list) ? list : [];
    },
    enabled: allowed,
  });
  if (!allowed) return null;
  const shown = (query.data ?? []).filter(
    (item) => !patientId || item.paciente_id === patientId,
  );
  return (
    <Panel
      title="Enviadas a otras instituciones"
      description="El estado cambia cuando la institución responde."
    >
      {query.error ? (
        <ErrorState error={query.error} retry={query.refetch} />
      ) : !query.data ? (
        <Loading />
      ) : shown.length ? (
        <ul className="divide-y divide-line">
          {shown.map((referral) => (
            <ExternalRow key={referral.id} referral={referral} />
          ))}
        </ul>
      ) : (
        <p className="px-5 py-6 text-sm text-ink-muted">
          No hay remisiones enviadas a otras instituciones.
        </p>
      )}
    </Panel>
  );
}
