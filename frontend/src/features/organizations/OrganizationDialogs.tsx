import { useMutation, useQuery } from "@tanstack/react-query";
import { Drawer } from "../../components/Drawer";
import type { Organization } from "../../api/types";
import { organizationsApi } from "../../api/organizations";
import {
  Action,
  Field,
  FormActions,
  Loading,
  Modal,
  Notice,
  useToast,
} from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { MotionItems } from "../../components/motion";
import { dateTime } from "../../components/format";
import {
  organizationError,
  organizationKeys,
  useInvalidateOrganizations,
  validateOrganization,
} from "./organizationState";

export function OrganizationFormDialog({
  organization,
  eps,
  onClose,
}: {
  organization?: Organization;
  eps?: Organization;
  onClose: () => void;
}) {
  const invalidate = useInvalidateOrganizations();
  const toast = useToast();
  const mutation = useMutation({
    mutationFn: async (form: FormData) => {
      const fields = validateOrganization(
        String(form.get("code") || ""),
        String(form.get("name") || ""),
      );
      if (organization) {
        const changes: Partial<typeof fields> = {};
        if (fields.code !== organization.code) changes.code = fields.code;
        if (fields.name !== organization.name) changes.name = fields.name;
        if (!Object.keys(changes).length) return organization;
        return organizationsApi.update(organization.id, changes);
      }
      return eps
        ? organizationsApi.createIps(eps.id, fields)
        : organizationsApi.createEps(fields);
    },
    onSuccess: async () => {
      toast(
        organization
          ? "Organización actualizada"
          : eps
            ? "IPS creada"
            : "EPS creada",
      );
      onClose();
      await invalidate();
    },
  });
  return (
    <Modal
      title={
        organization ? "Editar organización" : eps ? "Nueva IPS" : "Nueva EPS"
      }
      onClose={onClose}
    >
      <p className="mb-5 text-sm text-ink-muted">
        {organization
          ? `${organization.organization_type} ${organization.name}. Aquí solo puedes cambiar el código y el nombre.`
          : eps
            ? `La nueva IPS pertenecerá a ${eps.name} (${eps.code}).`
            : "Una EPS es la raíz de la red. Solo necesita un código y un nombre."}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(new FormData(event.currentTarget));
        }}
      >
        {mutation.error && <Notice>{organizationError(mutation.error)}</Notice>}
        <div className="space-y-4">
          <Field
            label="Código"
            name="code"
            defaultValue={organization?.code}
            required
            minLength={1}
            maxLength={32}
          />
          <Field
            label="Nombre"
            name="name"
            defaultValue={organization?.name}
            required
            minLength={1}
            maxLength={200}
          />
        </div>
        <FormActions
          label={
            organization ? "Guardar cambios" : eps ? "Crear IPS" : "Crear EPS"
          }
          pending={mutation.isPending}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}
export function DeleteOrganizationDialog({
  organization,
  onDeleted,
  onClose,
}: {
  organization: Organization;
  onDeleted: (item: Organization) => void;
  onClose: () => void;
}) {
  const invalidate = useInvalidateOrganizations();
  const mutation = useMutation({
    mutationFn: () => organizationsApi.remove(organization.id),
    onSuccess: async () => {
      onDeleted(organization);
      onClose();
      await invalidate();
    },
  });
  return (
    <Modal title="Eliminar organización" onClose={onClose}>
      <p className="font-semibold">
        {organization.name}
        <span className="ml-2 text-sm font-normal text-ink-muted">
          {organization.code}
        </span>
      </p>
      <p className="mt-3 text-sm text-ink-muted">
        Es un borrado lógico: la organización dejará de aparecer en los
        listados, pero su registro y su historial se conservan. Podrás
        restaurarla con «Deshacer» o desde «Eliminadas recientemente» durante
        esta sesión.
      </p>
      {organization.organization_type === "EPS" && (
        <p className="mt-3 text-sm text-ink-muted">
          Una EPS con IPS activas no se puede eliminar. El servidor comprobará
          esta condición.
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        {mutation.error && <Notice>{organizationError(mutation.error)}</Notice>}
        <FormActions
          danger
          label="Confirmar eliminación"
          pending={mutation.isPending}
          onClose={onClose}
        />
      </form>
    </Modal>
  );
}
export function OrganizationHistoryDrawer({
  organization,
  onClose,
}: {
  organization: Organization;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: organizationKeys.history(organization.id),
    queryFn: ({ signal }) => organizationsApi.history(organization.id, signal),
  });
  const fieldNames: Record<string, string> = {
    name: "Nombre",
    code: "Código",
    organization_type: "Tipo",
    parent_organization_id: "Organización padre",
    deleted_at: "Borrado lógico",
  };
  return (
    <Drawer
      visible
      wide
      onClose={onClose}
      title="Historial de versiones"
      label={`Historial de ${organization.name}`}
      footer={<Action secondary label="Cerrar" onClick={onClose} />}
    >
      <p className="break-words font-semibold">
        {organization.name}
        <span className="ml-2 text-sm font-normal text-ink-muted">
          {organization.code}
        </span>
      </p>
      <p className="my-3 text-sm text-ink-muted">
        Versiones anteriores conservadas por el servidor. El autor se identifica
        con su código de usuario.
      </p>
      {query.error ? (
        <ErrorState
          error={new Error(organizationError(query.error))}
          retry={query.refetch}
        />
      ) : !query.data ? (
        <Loading />
      ) : !query.data.length ? (
        <EmptyState title="Sin versiones anteriores">
          El historial aparecerá cuando se modifique el registro.
        </EmptyState>
      ) : (
        <ol className="divide-y divide-line">
          <MotionItems>
            {[...query.data]
              .sort((a, b) => b.version_number - a.version_number)
              .map((version) => (
                <li key={version.version_number} className="py-4">
                  <h3 className="font-semibold">
                    Versión {version.version_number}
                  </h3>
                  <p className="mt-1 text-sm text-ink-muted">
                    <time dateTime={version.changed_at}>
                      {dateTime(version.changed_at)}
                    </time>
                  </p>
                  <p className="mt-2 break-all text-xs text-ink-muted">
                    Autor: {version.changed_by || "No registrado"}
                  </p>
                  <dl className="mt-4 space-y-2 text-sm">
                    {[
                      "code",
                      "name",
                      "organization_type",
                      "parent_organization_id",
                    ].map((key) => (
                      <div key={key}>
                        <dt className="text-ink-muted">{fieldNames[key]}</dt>
                        <dd className="break-words font-semibold">
                          {String(
                            version.snapshot_json[key] ??
                              (key === "parent_organization_id"
                                ? "Sin organización padre"
                                : "Sin registrar"),
                          )}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p className="mt-3 text-xs text-ink-muted">
                    Campos modificados:{" "}
                    {version.changed_fields
                      ?.map((field) => fieldNames[field] || "Otro campo")
                      .join(", ") || "No detallados"}
                  </p>
                </li>
              ))}
          </MotionItems>
        </ol>
      )}
    </Drawer>
  );
}
