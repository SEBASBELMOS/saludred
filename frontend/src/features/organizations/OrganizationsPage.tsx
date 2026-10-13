import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { sessionToken } from "../../api/client";
import { organizationsApi } from "../../api/organizations";
import type { Organization } from "../../api/types";
import {
  Action,
  Loading,
  Notice,
  Section,
  focus,
  interaction,
  linkButton,
  useToast,
} from "../../components/ui";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { MotionItems } from "../../components/motion";
import { useAuth } from "../auth/AuthProvider";
import {
  DeleteOrganizationDialog,
  OrganizationFormDialog,
  OrganizationHistoryDrawer,
} from "./OrganizationDialogs";
import {
  organizationError,
  organizationKeys,
  useInvalidateOrganizations,
  useRecentDeletions,
} from "./organizationState";

interface OrganizationActions {
  edit: (organization: Organization) => void;
  history: (organization: Organization) => void;
  remove: (organization: Organization) => void;
}
// Edit is the quiet default, history is a text link, delete wears the danger look.
function RecordActions({
  organization,
  actions,
}: {
  organization: Organization;
  actions: OrganizationActions;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <button
        type="button"
        className={`${linkButton} text-sm max-sm:min-h-11`}
        aria-label={`Historial de ${organization.name}`}
        onClick={() => actions.history(organization)}
      >
        Historial
      </button>
      <Action
        secondary
        label="Editar"
        aria-label={`Editar ${organization.name}`}
        onClick={() => actions.edit(organization)}
      />
      <Action
        secondary
        danger
        label="Eliminar"
        aria-label={`Eliminar ${organization.name}`}
        onClick={() => actions.remove(organization)}
      />
    </div>
  );
}
function EpsRow({
  eps,
  actions,
  createIps,
}: {
  eps: Organization;
  actions: OrganizationActions;
  createIps: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const query = useQuery({
    queryKey: organizationKeys.ips(eps.id),
    queryFn: ({ signal }) => organizationsApi.ips(eps.id, signal),
  });
  return (
    <section className="min-w-0">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-6 gap-y-3 py-3">
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={() => setExpanded(!expanded)}
          className={`flex min-w-0 flex-[1_1_16rem] items-start gap-3 rounded-lg p-2 text-left hov:bg-surface-sunken ${interaction} ${focus}`}
        >
          <i
            aria-hidden="true"
            className={`pi mt-1 text-accent-strong ${expanded ? "pi-chevron-down" : "pi-chevron-right"}`}
          />
          <span className="min-w-0 flex-1">
            <span className="block break-words text-base font-semibold">
              {eps.name}
              <span className="ml-2 break-all text-xs font-normal text-ink-muted">
                {eps.code}
              </span>
            </span>
            <span className="mt-0.5 block text-sm text-ink-muted">
              {query.error
                ? "No se pudo consultar el número de IPS"
                : query.data
                  ? `${query.data.length} IPS en esta red`
                  : "Consultando IPS…"}
            </span>
          </span>
        </button>
        <RecordActions organization={eps} actions={actions} />
      </div>
      <div id={id} hidden={!expanded} className="pb-4 pl-9">
        {expanded && (
          <>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
              <h3 className="font-semibold">Instituciones prestadoras</h3>
              <Action
                secondary
                label="Nueva IPS"
                icon="pi pi-plus"
                aria-label={`Nueva IPS en ${eps.name}`}
                onClick={createIps}
              />
            </div>
            {query.error ? (
              <ErrorState
                error={new Error(organizationError(query.error))}
                retry={query.refetch}
              />
            ) : !query.data ? (
              <Loading />
            ) : !query.data.length ? (
              <EmptyState title="Esta EPS aún no tiene IPS">
                Crea su primera institución con «Nueva IPS».
              </EmptyState>
            ) : (
              <ul className="divide-y divide-line border-y border-line">
                <MotionItems>
                  {query.data.map((ips) => (
                    <li
                      key={ips.id}
                      className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2.5"
                    >
                      <div className="min-w-0 break-words">
                        <h4 className="inline font-semibold">{ips.name}</h4>
                        <span className="ml-2 break-all text-xs text-ink-muted">
                          {ips.code}
                        </span>
                      </div>
                      <RecordActions organization={ips} actions={actions} />
                    </li>
                  ))}
                </MotionItems>
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}
type FormTarget =
  | { kind: "eps" }
  | { kind: "ips"; eps: Organization }
  | { kind: "edit"; organization: Organization };
export function OrganizationsPage() {
  const { user } = useAuth();
  const toast = useToast();
  const invalidate = useInvalidateOrganizations();
  const recent = useRecentDeletions(user!.id);
  const query = useQuery({
    queryKey: organizationKeys.eps,
    queryFn: ({ signal }) => organizationsApi.eps(signal),
  });
  const [form, setForm] = useState<FormTarget | null>(null);
  const [deleting, setDeleting] = useState<Organization | null>(null);
  const [history, setHistory] = useState<Organization | null>(null);
  const restore = useMutation({
    mutationFn: (organization: Organization) =>
      organizationsApi.restore(organization.id),
    onSuccess: async (organization) => {
      recent.remove(organization.id);
      toast(`${organization.name} restaurada`);
      await invalidate();
    },
  });
  const actions: OrganizationActions = {
    edit: (organization) => setForm({ kind: "edit", organization }),
    history: setHistory,
    remove: setDeleting,
  };
  const onDeleted = (organization: Organization) => {
    recent.add(organization);
    const token = sessionToken.get();
    toast(`${organization.name} eliminada`, "success", {
      label: "Deshacer",
      onClick: async () => {
        if (!token || token !== sessionToken.get())
          throw new Error(
            "Tu sesión cambió. Vuelve a ingresar para administrar la red.",
          );
        await restore.mutateAsync(organization);
      },
    });
  };
  return (
    <div className="min-w-0 space-y-8">
      <Section
        title="EPS e IPS registradas"
        description="Organiza las EPS y las instituciones que prestan atención en cada red."
        action={
          <Action
            label="Nueva EPS"
            icon="pi pi-plus"
            onClick={() => setForm({ kind: "eps" })}
          />
        }
      >
        {query.error ? (
          <ErrorState
            error={new Error(organizationError(query.error))}
            retry={query.refetch}
          />
        ) : !query.data ? (
          <Loading variant="cards" />
        ) : (
          <>
            <p className="mb-3 text-sm text-ink-muted">
              {query.data.total} EPS registradas
              {query.data.total > query.data.items.length
                ? `. Se muestran las primeras ${query.data.items.length}`
                : ""}
              . Abre una EPS para ver sus IPS.
            </p>
            {!query.data.items.length ? (
              <EmptyState title="La red está por comenzar">
                Crea una EPS y luego añade sus instituciones prestadoras.
              </EmptyState>
            ) : (
              <div className="min-w-0 divide-y divide-line border-y border-line">
                <MotionItems>
                  {query.data.items.map((eps) => (
                    <EpsRow
                      key={eps.id}
                      eps={eps}
                      actions={actions}
                      createIps={() => setForm({ kind: "ips", eps })}
                    />
                  ))}
                </MotionItems>
              </div>
            )}
          </>
        )}
      </Section>
      {restore.error && <Notice>{organizationError(restore.error)}</Notice>}
      {!!recent.items.length && (
        <Section
          title="Eliminadas recientemente"
          description="Solo incluye lo que eliminaste durante esta sesión. Esta lista se borra al cerrar sesión o recargar la página; los registros siguen conservados en el servidor."
        >
          <ul className="divide-y divide-line border-y border-line">
            <MotionItems>
              {recent.items.map((item) => (
                <li
                  key={item.id}
                  className="flex min-w-0 flex-wrap items-center justify-between gap-3 py-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="break-words font-semibold">
                      {item.name}
                      <span className="ml-2 break-all text-xs font-normal text-ink-muted">
                        {item.code}
                      </span>
                    </p>
                    <p className="text-sm text-ink-muted">
                      {item.organization_type}
                    </p>
                  </div>
                  <Action
                    secondary
                    label="Restaurar"
                    aria-label={`Restaurar ${item.name}`}
                    disabled={restore.isPending}
                    loading={
                      restore.isPending && restore.variables?.id === item.id
                    }
                    onClick={() => restore.mutate(item)}
                  />
                </li>
              ))}
            </MotionItems>
          </ul>
        </Section>
      )}
      {form && (
        <OrganizationFormDialog
          organization={form.kind === "edit" ? form.organization : undefined}
          eps={form.kind === "ips" ? form.eps : undefined}
          onClose={() => setForm(null)}
        />
      )}
      {deleting && (
        <DeleteOrganizationDialog
          organization={deleting}
          onDeleted={onDeleted}
          onClose={() => setDeleting(null)}
        />
      )}
      {history && (
        <OrganizationHistoryDrawer
          organization={history}
          onClose={() => setHistory(null)}
        />
      )}
    </div>
  );
}
