import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { accountsApi } from "../../api/accounts";
import { listEps } from "../../api/organizations";
import type { Account, AccountCreate, Role } from "../../api/types";
import { useInvalidateClinical, useNetwork } from "../../api/query";
import { roleNames } from "../auth/permissions";
import { accountError, roleLink } from "./accountState";
import { ErrorState } from "../../components/ErrorState";
import {
  Field,
  FormActions,
  Loading,
  Modal,
  Notice,
  SelectField,
  TextField,
  options,
  useToast,
} from "../../components/ui";
export function LockAccountDialog({
  account,
  onClose,
}: {
  account: Account;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const mutation = useMutation({
    mutationFn: () =>
      accountsApi.change(
        account.id,
        account.is_locked ? "unlock" : "lock",
        reason.trim(),
      ),
    onSuccess: async () => {
      toast(
        account.is_locked
          ? `${account.username} puede volver a entrar`
          : `${account.username} quedó bloqueada`,
      );
      onClose();
      await invalidate();
    },
  });
  return (
    <Modal
      title={account.is_locked ? "Desbloquear cuenta" : "Bloquear cuenta"}
      onClose={onClose}
    >
      <p className="font-semibold">
        {account.full_name}
        <span className="ml-2 font-normal text-ink-muted">
          {account.username}
        </span>
      </p>
      <p className="my-3 text-sm text-ink-muted">
        {account.is_locked
          ? "La cuenta vuelve a entrar y su contador de intentos fallidos se reinicia."
          : "La cuenta no podrá entrar hasta que la administración la desbloquee."}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (reason.trim()) mutation.mutate();
        }}
      >
        {mutation.error && <Notice>{mutation.error.message}</Notice>}
        <TextField
          label="Motivo (queda en la auditoría)"
          required
          maxLength={300}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
        />
        <FormActions
          danger={!account.is_locked}
          label={account.is_locked ? "Desbloquear" : "Bloquear"}
          pending={mutation.isPending}
          disabled={!reason.trim()}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}
export function CreateAccountDialog({ onClose }: { onClose: () => void }) {
  const [role, setRole] = useState<Role>("IPS_CLINICAL_OPERATOR");
  const network = useNetwork();
  const eps = useQuery({
    queryKey: ["eps"],
    queryFn: ({ signal }) => listEps(signal),
  });
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const mutation = useMutation({
    mutationFn: (form: FormData) => {
      const clean = (key: string) => String(form.get(key) || "").trim() || null;
      const body: AccountCreate = {
        role,
        username: clean("username")!,
        full_name: role === "PATIENT" ? null : clean("full_name"),
        email: clean("email"),
        password: String(form.get("password")),
      };
      if (role === "IPS_CLINICAL_OPERATOR" || role === "SPECIALIST")
        body.organization_id = clean("ips");
      if (role === "EPS_COORDINATOR" || role === "ACCOUNTANT")
        body.organization_id = clean("eps");
      if (role === "PATIENT") body.patient_document = clean("document");
      return accountsApi.create(body);
    },
    onSuccess: async (account) => {
      toast(`Cuenta ${account.username} creada`);
      onClose();
      await invalidate();
    },
  });
  return (
    <Modal title="Nueva cuenta" onClose={onClose}>
      {network.error || eps.error ? (
        <ErrorState
          error={(network.error || eps.error)!}
          retry={() => Promise.all([network.refetch(), eps.refetch()])}
        />
      ) : !network.data || !eps.data ? (
        <Loading />
      ) : (
        <form
          autoComplete="off"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate(new FormData(event.currentTarget));
          }}
        >
          <p className="mb-4 text-sm text-ink-muted">
            La clave no aparece en la auditoría. Entrégala a la persona por un
            canal seguro.
          </p>
          {mutation.error && (
            <Notice>{accountError(mutation.error, role)}</Notice>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="min-w-0">
              <SelectField
                label="Rol"
                value={role}
                onChange={(event) => setRole(event.target.value as Role)}
              >
                {options(roleNames)}
              </SelectField>
              {roleLink[role] && (
                <p className="mt-1 text-xs text-ink-muted">{roleLink[role]}</p>
              )}
            </div>
            <Field
              label="Usuario"
              name="username"
              required
              minLength={3}
              maxLength={80}
              pattern="[a-z0-9._\-]+"
              autoComplete="off"
            />
            {(role === "IPS_CLINICAL_OPERATOR" || role === "SPECIALIST") && (
              <SelectField label="IPS" name="ips" required>
                <option value="">Selecciona la IPS</option>
                {network.data.network.organizations.map((org) => (
                  <option key={org.organization_id} value={org.organization_id}>
                    {org.organization_name}
                  </option>
                ))}
              </SelectField>
            )}
            {(role === "EPS_COORDINATOR" || role === "ACCOUNTANT") && (
              <SelectField label="EPS" name="eps" required>
                <option value="">Selecciona la EPS</option>
                {eps.data.items.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </SelectField>
            )}
            {role === "PATIENT" ? (
              <Field
                label="Documento del paciente"
                name="document"
                required
                minLength={4}
                maxLength={32}
              />
            ) : (
              <Field
                label="Nombre completo"
                name="full_name"
                required
                maxLength={200}
              />
            )}
            <Field
              label="Correo (opcional)"
              type="email"
              name="email"
              maxLength={200}
            />
            <Field
              label="Clave inicial"
              name="password"
              type="password"
              required
              minLength={8}
              maxLength={128}
              autoComplete="new-password"
            />
          </div>
          <FormActions
            label="Crear cuenta"
            pending={mutation.isPending}
            onClose={onClose}
          />
        </form>
      )}
    </Modal>
  );
}
