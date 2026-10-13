import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { accountsApi } from "../../api/accounts";
import type { Account, AuditLog } from "../../api/types";
import { useAuth } from "../auth/AuthProvider";
import { roleNames } from "../auth/permissions";
import { Action, DataTable, Loading, Panel } from "../../components/ui";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { dateTime } from "../../components/format";
import { auditDetail } from "./auditDetail";
import { CreateAccountDialog, LockAccountDialog } from "./AccountDialogs";
const actionNames: Record<string, string> = {
  LOGIN: "Ingreso",
  LOGIN_FAILED: "Ingreso fallido",
  ACCOUNT_LOCKED: "Cuenta bloqueada",
  ACCOUNT_UNLOCKED: "Cuenta desbloqueada",
  CREATE: "Creación",
  SOFT_EDIT: "Edición",
  SOFT_DELETE: "Borrado lógico",
  RESTORE: "Restauración",
  FHIR_SYNC: "Envío a FHIR",
  REFERRAL_CREATED: "Remisión creada",
  REFERRAL_ACCEPTED: "Remisión aceptada",
  REFERRAL_REJECTED: "Remisión rechazada",
  REFERRAL_COMPLETED: "Remisión completada",
  ALERT_CREATED: "Alerta creada",
  ALERT_ACKNOWLEDGED: "Alerta reconocida",
  ALERT_ESCALATED: "Alerta escalada",
};
const entityNames: Record<string, string> = {
  patients: "Paciente",
  users: "Cuenta",
  encounters: "Atención",
  observations: "Medición",
  imaging_studies: "Estudio de imagen",
  bed_requests: "Solicitud de cama",
  bed_assignments: "Asignación de cama",
  organizations: "Organización",
  locations: "Ubicación",
  referrals: "Remisión",
  alerts: "Alerta",
};
export function AccountsTable({
  accounts,
  selfId,
  onChange,
}: {
  accounts: Account[];
  selfId: string;
  onChange: (account: Account) => void;
}) {
  return accounts.length ? (
    <DataTable
      stack
      caption="Cuentas de la red"
      headers={[
        "Cuenta",
        "Rol",
        "Intentos fallidos",
        "Estado",
        "Último ingreso",
        "Acción",
      ]}
    >
      {accounts.map((account) => (
        <tr key={account.id}>
          <td>
            <strong>{account.full_name}</strong>
            <small className="block text-ink-muted">{account.username}</small>
          </td>
          <td>{roleNames[account.role]}</td>
          <td>
            <span className="tabular-nums">
              {Math.min(3, account.failed_login_attempts)} / 3
            </span>
          </td>
          <td>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${account.is_locked ? "bg-bed-cleaning-tint text-bed-cleaning-ink" : account.is_active ? "bg-bed-available-tint text-bed-available-ink" : "bg-surface-sunken text-ink-muted"}`}
            >
              {account.is_locked
                ? "Bloqueada"
                : account.is_active
                  ? "Activa"
                  : "Inactiva"}
            </span>
            {account.is_locked && (
              <small className="mt-1 block text-ink-muted">
                desde {dateTime(account.locked_at)}
              </small>
            )}
          </td>
          <td>{dateTime(account.last_login_at)}</td>
          <td>
            {account.id === selfId ? (
              <span className="text-sm text-ink-muted">Tu cuenta</span>
            ) : (
              <Action
                secondary
                danger={!account.is_locked}
                label={account.is_locked ? "Desbloquear" : "Bloquear"}
                aria-label={`${account.is_locked ? "Desbloquear" : "Bloquear"} a ${account.username}`}
                onClick={() => onChange(account)}
              />
            )}
          </td>
        </tr>
      ))}
    </DataTable>
  ) : (
    <EmptyState title="No hay cuentas para mostrar" />
  );
}
export function AuditTable({ entries }: { entries: AuditLog[] }) {
  return entries.length ? (
    <DataTable
      caption="Actividad reciente"
      headers={["Fecha", "Acción", "Sobre", "Quién", "Detalle"]}
    >
      {entries.slice(0, 20).map((entry) => (
        <tr key={entry.id}>
          <td>{dateTime(entry.created_at)}</td>
          <td>{actionNames[entry.action] || entry.action}</td>
          <td>{entityNames[entry.entity_type] || entry.entity_type}</td>
          <td>{entry.username || "Anónimo"}</td>
          <td>
            <span className="block max-w-xs whitespace-normal break-words text-xs text-ink-muted">
              {auditDetail(entry.metadata_json)}
            </span>
          </td>
        </tr>
      ))}
    </DataTable>
  ) : (
    <EmptyState title="Sin actividad registrada" />
  );
}
export function AccountsPage() {
  // The cuenta_bloqueada notification links here with the locked accounts only.
  const [params] = useSearchParams();
  const [locked, setLocked] = useState(params.get("bloqueadas") === "1");
  const [account, setAccount] = useState<Account | null>(null);
  const [creating, setCreating] = useState(false);
  const { user } = useAuth();
  const accounts = useQuery({
    queryKey: ["accounts", locked],
    queryFn: ({ signal }) => accountsApi.list(locked, signal),
  });
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: ({ signal }) => accountsApi.audit(signal),
  });
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm font-semibold">
          <input
            className="accent-accent"
            type="checkbox"
            checked={locked}
            onChange={(event) => setLocked(event.target.checked)}
          />
          Solo cuentas bloqueadas
        </label>
        <Action
          label="Nueva cuenta"
          icon="pi pi-plus"
          onClick={() => setCreating(true)}
        />
      </div>
      <Panel
        title="Acceso a la red"
        description="Cada bloqueo y desbloqueo queda en la auditoría con quién lo hizo y por qué."
      >
        {accounts.error ? (
          <ErrorState error={accounts.error} retry={accounts.refetch} />
        ) : !accounts.data ? (
          <Loading />
        ) : (
          <>
            <p className="px-5 py-3 text-sm text-ink-muted">
              {accounts.data.total} cuentas. Se muestran{" "}
              {accounts.data.items.length}.
            </p>
            <AccountsTable
              accounts={accounts.data.items}
              selfId={user!.id}
              onChange={setAccount}
            />
          </>
        )}
      </Panel>
      <Panel
        title="Actividad reciente"
        description="Los últimos 20 registros de la auditoría. Solo la administración los ve."
      >
        {audit.error ? (
          <ErrorState error={audit.error} retry={audit.refetch} />
        ) : !audit.data ? (
          <Loading />
        ) : (
          <AuditTable entries={audit.data.items} />
        )}
      </Panel>
      {account && (
        <LockAccountDialog account={account} onClose={() => setAccount(null)} />
      )}{" "}
      {creating && <CreateAccountDialog onClose={() => setCreating(false)} />}
    </div>
  );
}
