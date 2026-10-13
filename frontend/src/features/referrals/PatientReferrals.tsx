import { ErrorState } from "../../components/ErrorState";
import { Loading, Panel } from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canSeeReferrals } from "../auth/permissions";
import { ReferralChip, ReferralOutcome, referralMoment } from "./ReferralParts";
import { useReferrals } from "./useReferrals";

/**
 * The referrals of this patient the user can see (the ones they sent or
 * received; the admin sees all). Same cache entry as the Remisiones screen, so
 * a live remision_* event refreshes both.
 */
export function PatientReferrals({ patientId }: { patientId: string }) {
  const { user } = useAuth();
  const allowed = !!user && canSeeReferrals(user.role);
  const referrals = useReferrals(allowed);
  if (!allowed) return null;
  const mine = (referrals.data ?? [])
    .filter((referral) => referral.paciente_id === patientId)
    .sort((a, b) => b.creada_en.localeCompare(a.creada_en));
  return (
    <Panel
      title="Remisiones"
      description="Remisiones de este paciente a especialistas."
    >
      {referrals.error ? (
        <ErrorState error={referrals.error} retry={referrals.refetch} />
      ) : !referrals.data ? (
        <Loading />
      ) : mine.length ? (
        <ul className="divide-y divide-line">
          {mine.map((referral) => (
            <li key={referral.id} className="px-5 py-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <ReferralChip estado={referral.estado} />
                <time
                  dateTime={referral.creada_en}
                  className="text-xs tabular-nums text-ink-muted"
                >
                  {referralMoment(referral)}
                </time>
              </div>
              <p className="mt-2 break-words text-sm">
                <strong className="font-semibold">
                  {referral.remitente_nombre}
                </strong>{" "}
                remitió a{" "}
                <strong className="font-semibold">
                  {referral.especialista_nombre}
                </strong>
                {referral.especialista_organizacion &&
                  `, ${referral.especialista_organizacion}`}
              </p>
              <p className="mt-1 break-words text-sm text-ink-muted">
                {referral.motivo}
              </p>
              <ReferralOutcome referral={referral} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-5 py-6 text-sm text-ink-muted">
          Este paciente no tiene remisiones.
        </p>
      )}
    </Panel>
  );
}
