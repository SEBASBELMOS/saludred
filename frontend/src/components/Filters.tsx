import { useSearchParams } from "react-router-dom";
import type { OrganizationCapacity } from "../api/types";
import { Action, Field, SelectField, options } from "./ui";
export function Filters({
  organizations,
  services,
  extra,
  searchLabel,
}: {
  organizations: OrganizationCapacity[];
  services: string[];
  extra: { key: string; label: string; values: Record<string, string> };
  searchLabel: string;
}) {
  const [params, setParams] = useSearchParams();
  const update = (key: string, value: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  return (
    <div className="mb-4 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(170px,1.3fr)_repeat(3,minmax(0,1fr))_auto]">
      <Field
        label={searchLabel}
        type="search"
        data-main-search
        aria-keyshortcuts="/"
        value={params.get("search") || ""}
        onChange={(event) => update("search", event.target.value)}
      />
      <SelectField
        label="Institución"
        value={params.get("org") || ""}
        onChange={(event) => update("org", event.target.value)}
      >
        <option value="">Todas las instituciones</option>
        {organizations.map((org) => (
          <option key={org.organization_id} value={org.organization_id}>
            {org.organization_name}
          </option>
        ))}
      </SelectField>
      <SelectField
        label="Servicio"
        value={params.get("service") || ""}
        onChange={(event) => update("service", event.target.value)}
      >
        <option value="">Todos los servicios</option>
        {services.map((service) => (
          <option key={service}>{service}</option>
        ))}
      </SelectField>
      <SelectField
        label={extra.label}
        value={params.get(extra.key) || ""}
        onChange={(event) => update(extra.key, event.target.value)}
      >
        <option value="">Todos</option>
        {options(extra.values)}
      </SelectField>
      <Action
        secondary
        label="Limpiar filtros"
        className="self-end"
        onClick={() => setParams({})}
      />
    </div>
  );
}
