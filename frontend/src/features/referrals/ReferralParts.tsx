import type { Referral, ReferralState } from "../../api/referrals";
import { dateTime } from "../../components/format";

export const referralStates: Record<ReferralState, string> = {
  pendiente: "Pendiente",
  aceptada: "Aceptada",
  rechazada: "Rechazada",
  completada: "Completada",
};
// Calm treatments, as in the AI reports: pending is ochre (a person has to answer),
// accepted is green, completed is slate and rejected is grey. Red is left for "crítico".
const stateTone: Record<ReferralState, string> = {
  pendiente: "bg-bed-cleaning-tint text-bed-cleaning-ink",
  aceptada: "bg-bed-available-tint text-bed-available-ink",
  completada: "bg-bed-occupied-tint text-bed-occupied-ink",
  rechazada: "bg-surface-sunken text-ink-muted",
};

export function ReferralChip({ estado }: { estado: ReferralState }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${stateTone[estado]}`}
    >
      {referralStates[estado]}
    </span>
  );
}

// What the other side wrote: the rejection reason and the specialist's report.
export function ReferralOutcome({ referral }: { referral: Referral }) {
  if (!referral.motivo_rechazo && !referral.informe) return null;
  return (
    <div className="mt-2 space-y-2 text-sm">
      {referral.motivo_rechazo && (
        <p className="break-words">
          <span className="text-xs text-ink-muted">Motivo del rechazo </span>
          <span className="block">{referral.motivo_rechazo}</span>
        </p>
      )}
      {referral.informe && (
        <details>
          <summary className="cursor-pointer text-sm font-semibold text-accent">
            Informe del especialista
          </summary>
          <p className="mt-1 whitespace-pre-wrap break-words">
            {referral.informe}
          </p>
        </details>
      )}
    </div>
  );
}

// The latest step of a referral, for the "when" line.
export function referralMoment(referral: Referral) {
  const moment =
    referral.completada_en ??
    referral.rechazada_en ??
    referral.aceptada_en ??
    referral.creada_en;
  return dateTime(moment);
}
