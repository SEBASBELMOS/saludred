import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import type { Alert } from "../../api/alerts";
import { patientsApi } from "../../api/patients";
import { dateTime, fullName, minutes } from "../../components/format";
import { Action } from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canSeeCriticalAlerts } from "../auth/permissions";
import { AcknowledgeForm } from "./AcknowledgeForm";
import { byUrgency, minutesSince, useActiveAlerts, useNow } from "./useAlerts";

const levelName: Record<string, string> = { critico: "Crítico", alto: "Alto" };
const levelTone: Record<string, string> = {
  critico: "bg-surface text-critical-ink",
  alto: "bg-urgent-tint text-urgent-ink",
};

export const ago = (created: string, now: number) => {
  const waited = minutesSince(created, now);
  return waited < 1 ? "Hace menos de 1 min" : `Hace ${minutes(waited)}`;
};

function AlertRow({ alert, now }: { alert: Alert; now: number }) {
  const navigate = useNavigate();
  const [acknowledging, setAcknowledging] = useState(false);
  const patient = useQuery({
    queryKey: ["patient", alert.paciente_id],
    queryFn: ({ signal }) => patientsApi.get(alert.paciente_id!, signal),
    enabled: !!alert.paciente_id,
    meta: { silent: true },
  });
  const name = patient.data ? fullName(patient.data) : "Paciente";
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ${levelTone[alert.nivel] ?? levelTone.alto}`}
            >
              {levelName[alert.nivel] ?? alert.nivel}
            </span>
            <strong className="min-w-0 break-words text-sm font-semibold">
              {name}
            </strong>
            <time
              dateTime={alert.created_at}
              className="text-xs tabular-nums"
              title={dateTime(alert.created_at)}
            >
              {ago(alert.created_at, now)}
            </time>
            {alert.escalated_at && (
              <span className="inline-flex whitespace-nowrap rounded-full bg-surface px-2 py-0.5 text-xs font-semibold text-critical-ink">
                Escalada{" "}
                <time
                  dateTime={alert.escalated_at}
                  className="ml-1 font-normal tabular-nums"
                >
                  {dateTime(alert.escalated_at)}
                </time>
              </span>
            )}
          </div>
          <p className="mt-1 break-words text-sm">{alert.mensaje}</p>
        </div>
        {!acknowledging && (
          <div className="flex shrink-0 flex-wrap gap-2">
            {alert.paciente_id && (
              <Action
                secondary
                label="Ver paciente"
                aria-label={`Ver paciente ${name}`}
                onClick={() => navigate(`/patients?patient=${alert.paciente_id}`)}
              />
            )}
            <Action
              danger
              label="Reconocer"
              aria-label={`Reconocer la alerta de ${name}`}
              onClick={() => setAcknowledging(true)}
            />
          </div>
        )}
      </div>
      {acknowledging && (
        <AcknowledgeForm
          alert={alert}
          onClose={() => setAcknowledging(false)}
        />
      )}
    </li>
  );
}

/**
 * R20.2: every ACTIVE critical alert of the clinician, at the top of the shell,
 * on every screen. There is no close button: the only way out is to acknowledge
 * it with a note. It appears and clears from the live channel.
 */
export function CriticalAlertBanner() {
  const { user } = useAuth();
  const allowed = !!user && canSeeCriticalAlerts(user.role);
  const alerts = useActiveAlerts(allowed);
  const now = useNow();
  const items = (alerts.data ?? [])
    .filter((alert) => alert.kind === "paciente_critico")
    .sort(byUrgency);
  if (!allowed || !items.length) return null;
  return (
    <section
      aria-label="Alertas críticas activas"
      aria-live="assertive"
      className="sticky top-0 z-30 border-b border-critical/30 bg-critical-tint text-critical-ink"
    >
      <div className="mx-auto max-w-[1560px] px-4 py-3 sm:px-8">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <i
            className="pi pi-exclamation-circle text-critical"
            aria-hidden="true"
          />
          {items.length === 1
            ? "1 alerta crítica sin reconocer"
            : `${items.length} alertas críticas sin reconocer`}
        </h2>
        <ul className="mt-1 max-h-[45dvh] divide-y divide-critical/20 overflow-y-auto">
          {items.map((alert) => (
            <AlertRow key={alert.id} alert={alert} now={now} />
          ))}
        </ul>
      </div>
    </section>
  );
}
