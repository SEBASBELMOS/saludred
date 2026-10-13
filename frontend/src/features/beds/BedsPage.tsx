import { useId, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Bed, OrganizationCapacity } from "../../api/types";
import { useNetwork } from "../../api/query";
import { useAuth } from "../auth/AuthProvider";
import { Filters } from "../../components/Filters";
import { EmptyState } from "../../components/EmptyState";
import { ErrorState } from "../../components/ErrorState";
import { StatusBadge, statusNames } from "../../components/StatusBadge";
import { Action, Loading, Field } from "../../components/ui";
import { normalize } from "../../components/format";
import { BedTile, BedTooltip } from "../census/BedTile";
import { AvailabilityAnnouncer } from "../census/AvailabilityAnnouncer";
import { useRovingTiles } from "../census/roving";
import { findBedByCode } from "./goToBed";
import { BedDrawer } from "./BedDrawer";
import { PendingReports } from "../ai/PendingReports";
import { PatientRecordDialog } from "../patients/PatientRecordDialog";

// Beds that share a parent location are one room: their tiles sit together.
type TileProps = (id: string) => { tabIndex: number; onFocus: () => void };
function Rooms({
  beds,
  selected,
  onOpen,
  tileProps,
}: {
  beds: Bed[];
  selected?: string;
  onOpen: (id: string) => void;
  tileProps: TileProps;
}) {
  const rooms = new Map<string, Bed[]>();
  const loose: Bed[] = [];
  for (const bed of beds) {
    if (bed.parent_location_id)
      rooms.set(bed.parent_location_id, [
        ...(rooms.get(bed.parent_location_id) ?? []),
        bed,
      ]);
    else loose.push(bed);
  }
  const tile = (bed: Bed) => (
    <BedTile
      key={bed.id}
      bed={bed}
      size="lg"
      selected={selected === bed.id}
      onSelect={onOpen}
      {...tileProps(bed.id)}
    />
  );
  return (
    <div className="grid min-w-0 flex-1 grid-cols-2 gap-1.5 sm:flex sm:flex-wrap">
      {[...rooms.entries()].map(([room, list]) => (
        <div
          key={room}
          className="flex justify-center gap-1 rounded-lg border border-line bg-surface-sunken p-1"
        >
          {list.map(tile)}
        </div>
      ))}
      {loose.map(tile)}
    </div>
  );
}
export function CensusBoard({
  beds,
  organizations,
  selected,
  onOpen,
}: {
  beds: Bed[];
  organizations: OrganizationCapacity[];
  selected?: string;
  onOpen: (id: string) => void;
}) {
  const ids = organizations.flatMap((org) =>
    beds
      .filter((b) => b.organization_id === org.organization_id)
      .map((b) => b.id),
  );
  const { boardProps, tileProps } = useRovingTiles(ids);
  return (
    <div className="space-y-8" {...boardProps}>
      <BedTooltip />
      {organizations.map((org) => {
        const list = beds.filter(
          (b) => b.organization_id === org.organization_id,
        );
        if (!list.length) return null;
        const services = [
          ...new Set(list.map((b) => b.service || "Sin servicio")),
        ].sort();
        const free = list.filter((b) => b.status === "AVAILABLE").length;
        return (
          <section key={org.organization_id}>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4">
              <h2 className="text-lg font-semibold">
                {org.organization_name}
                <span className="ml-2 text-xs font-normal text-ink-muted">
                  {org.organization_code}
                </span>
              </h2>
              <span
                className={`text-sm font-semibold ${free ? "text-bed-available-ink" : "text-urgent-ink"}`}
              >
                {free
                  ? `${free} ${free === 1 ? "libre" : "libres"}`
                  : "Sin camas libres"}
              </span>
            </div>
            <div className="divide-y divide-line border-y border-line">
              {services.map((service) => (
                <div
                  key={service}
                  className="flex flex-wrap gap-x-4 gap-y-2 py-3"
                >
                  <h3 className="w-full shrink-0 pt-1 text-sm font-semibold sm:w-40">
                    {service}
                    <span className="block text-xs font-normal text-ink-muted">
                      {
                        list.filter(
                          (b) => (b.service || "Sin servicio") === service,
                        ).length
                      }{" "}
                      camas
                    </span>
                  </h3>
                  <Rooms
                    beds={list.filter(
                      (b) => (b.service || "Sin servicio") === service,
                    )}
                    selected={selected}
                    onOpen={onOpen}
                    tileProps={tileProps}
                  />
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
// Type a bed code (N-HOS-01) and open it without hunting for the tile.
function GoToBed({
  beds,
  onOpen,
}: {
  beds: Bed[];
  onOpen: (id: string) => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const hint = useId();
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const found = findBedByCode(beds, code);
        if (found.kind === "found") {
          setError("");
          setCode("");
          onOpen(found.bed.id);
        } else
          setError(
            found.kind === "ambiguous"
              ? `Hay ${found.count} camas con ese código. Escribe el código completo.`
              : `No hay una cama con el código «${code.trim()}».`,
          );
      }}
    >
      <div className="w-44">
        <Field
          label="Ir a cama"
          value={code}
          placeholder="N-HOS-01"
          autoComplete="off"
          aria-describedby={error ? hint : undefined}
          aria-invalid={error ? true : undefined}
          onChange={(event) => {
            setCode(event.target.value);
            setError("");
          }}
        />
      </div>
      <Action secondary type="submit" label="Abrir" disabled={!code.trim()} />
      {error && (
        <p
          id={hint}
          role="alert"
          className="basis-full text-sm text-critical-ink"
        >
          {error}
        </p>
      )}
    </form>
  );
}
export function BedsPage() {
  const query = useNetwork();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  if (query.error)
    return <ErrorState error={query.error} retry={query.refetch} />;
  if (!query.data) return <Loading variant="cards" />;
  const { beds, network } = query.data;
  const list = beds.filter(
    (b) =>
      (!params.get("org") || b.organization_id === params.get("org")) &&
      (!params.get("status") || b.status === params.get("status")) &&
      (!params.get("service") ||
        (b.service || "Sin servicio") === params.get("service")) &&
      normalize(
        `${b.name} ${b.code} ${b.service} ${network.organizations.find((o) => o.organization_id === b.organization_id)?.organization_name}`,
      ).includes(normalize(params.get("search") || "")),
  );
  const bed = beds.find((b) => b.id === params.get("bed"));
  const select = (id?: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (id) next.set("bed", id);
      else next.delete("bed");
      return next;
    });
  const closePatient = () =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("patient");
      return next;
    });
  return (
    <>
      <PendingReports />
      <Filters
        organizations={network.organizations}
        services={[...new Set(beds.map((b) => b.service || "Sin servicio"))]}
        searchLabel="Buscar cama o servicio"
        extra={{ key: "status", label: "Estado", values: statusNames }}
      />
      <AvailabilityAnnouncer beds={beds} />
      <div className="mb-4">
        <GoToBed beds={beds} onOpen={select} />
      </div>
      <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="text-sm text-ink-muted">
          <strong className="font-semibold text-ink">{list.length}</strong>{" "}
          camas. Selecciona una ficha para consultar o cambiar su estado. Con el
          teclado, usa las flechas para moverte entre fichas.
        </p>
        <ul className="flex flex-wrap gap-1.5">
          {(Object.keys(statusNames) as (keyof typeof statusNames)[]).map(
            (status) => (
              <li key={status}>
                <StatusBadge status={status} />
              </li>
            ),
          )}
        </ul>
      </div>
      {list.length ? (
        <CensusBoard
          beds={list}
          organizations={network.organizations}
          selected={bed?.id}
          onOpen={select}
        />
      ) : (
        <EmptyState title="No hay camas que coincidan con estos filtros">
          Prueba con otro servicio o estado, o limpia los filtros.
        </EmptyState>
      )}
      <BedDrawer
        bed={bed}
        organization={
          network.organizations.find(
            (o) => o.organization_id === bed?.organization_id,
          )?.organization_name || "Institución"
        }
        role={user!.role}
        onClose={() => select()}
      />
      {params.get("patient") && (
        <PatientRecordDialog
          patientId={params.get("patient")!}
          onClose={closePatient}
        />
      )}
    </>
  );
}
