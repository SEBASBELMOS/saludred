import { useEffect, useState, type ReactNode } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { analyticsApi, occupancyTimeline } from "../../api/analytics";
import { cohortApi } from "../../api/cohort";
import type { Clusters, CohortCluster } from "../../api/types";
import { DataTable, Loading, Panel, SelectField } from "../../components/ui";
import { ErrorState } from "../../components/ErrorState";
import { EmptyState } from "../../components/EmptyState";
import { PriorityBadge } from "../../components/PriorityBadge";
import { minutes, profileNames } from "../../components/format";
import { OccupancyChart } from "./OccupancyChart";
import { ModelsSection } from "../ai/ModelsSection";
function Result<T>({
  query,
  children,
}: {
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
}) {
  return query.error ? (
    <ErrorState error={query.error} retry={query.refetch} />
  ) : query.data === undefined ? (
    <Loading />
  ) : (
    <>{children(query.data)}</>
  );
}
const number = (value?: number | null, digits = 0) =>
  value == null
    ? "—"
    : value.toLocaleString("es-CO", { maximumFractionDigits: digits });
export function ClusterResults({ data }: { data: Clusters }) {
  if (!data.clusters)
    return <EmptyState title={data.message || "Sin datos para agrupar"} />;
  return (
    <>
      <div className="mx-5 mb-5">
        <p className="text-sm">
          {data.k_chosen_by === "silhouette"
            ? "El análisis eligió"
            : "Agrupamiento fijado a mano en"}{" "}
          <strong>{data.k} grupos</strong>. Así de bien resultaron:
        </p>
        <dl className="mt-3 grid gap-x-8 gap-y-4 sm:grid-cols-3">
          {[
            [
              "Separación entre grupos",
              number(data.silhouette, 2),
              "Qué tan distintos son los grupos entre sí. Cuanto más cerca de 1, mejor.",
            ],
            [
              "Coincidencia con el perfil clínico",
              number(data.adjusted_rand_index, 2),
              "Qué tanto los grupos coinciden con el perfil clínico conocido. 1 es total; cerca de 0, casualidad.",
            ],
            [
              "Pureza del grupo",
              `${number((data.purity ?? 0) * 100)} %`,
              "Qué parte de cada grupo comparte el mismo perfil clínico.",
            ],
          ].map(([term, value, help]) => (
            <div key={term}>
              <dt className="text-sm font-semibold">{term}</dt>
              <dd>
                <span className="font-display text-xl font-semibold tabular-nums">
                  {value}
                </span>
                <span className="mt-0.5 block text-xs text-ink-muted">
                  {help}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <DataTable
        numeric={[1, 3, 4, 5, 6, 7, 8]}
        caption="Grupos de pacientes"
        headers={[
          "Grupo",
          "Pacientes",
          "Perfil que predomina",
          "PA sistólica",
          "Glucosa",
          "Saturación",
          "Frec. cardíaca",
          "IMC",
          "Edad",
        ]}
      >
        {data.clusters.map((cluster) => (
          <tr key={cluster.cluster}>
            <td className="font-semibold">Grupo {cluster.cluster}</td>
            <td>{cluster.patients}</td>
            <td>
              {cluster.dominant_profile
                ? profileNames[cluster.dominant_profile] ||
                  cluster.dominant_profile
                : "—"}
              <small className="block text-ink-muted">
                {number(cluster.dominant_share * 100)} % del grupo
              </small>
            </td>
            {[
              "sistolica",
              "glucosa",
              "saturacion",
              "frecuencia_cardiaca",
              "imc",
              "edad",
            ].map((key) => (
              <td key={key}>
                {number(cluster.centroid[key], key === "imc" ? 1 : 0)}
              </td>
            ))}
          </tr>
        ))}
      </DataTable>
      <details className="p-5">
        <summary className="cursor-pointer text-sm font-semibold text-accent-strong">
          Cómo se eligió el número de grupos
        </summary>
        <p className="my-3 text-sm text-ink-muted">
          Se prueba cada cantidad de grupos y se elige la que deja los grupos
          mejor separados entre sí.
        </p>
        <DataTable
          numeric={[1]}
          caption="Evaluación del número de grupos"
          headers={["Opción", "Separación entre grupos"]}
        >
          {data.silhouette_by_k?.map((row) => (
            <tr key={row.k}>
              <td>
                {row.k} grupos{row.k === data.k ? " (elegido)" : ""}
              </td>
              <td>{number(row.silhouette, 3)}</td>
            </tr>
          ))}
        </DataTable>
      </details>
    </>
  );
}
const upperFirst = (text: string) =>
  text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
// The triage cohort: real emergency-room stays plus synthetic rows made inside each group.
export function CohortClusters({ data }: { data: CohortCluster[] }) {
  if (!data.length)
    return (
      <EmptyState title="La cohorte de triage aún no se construyó">
        Cuando se cargue, aquí aparecerán sus grupos con las filas reales y
        sintéticas.
      </EmptyState>
    );
  const reales = data.reduce((sum, row) => sum + row.n_reales, 0);
  const sinteticos = data.reduce((sum, row) => sum + row.n_sinteticos, 0);
  return (
    <>
      <p className="mx-5 mb-4 text-sm">
        <strong className="tabular-nums">{number(reales)}</strong> visitas
        reales y{" "}
        <strong className="tabular-nums">{number(sinteticos)}</strong> filas
        sintéticas en {data.length} grupos.
      </p>
      <DataTable
        stack
        numeric={[3, 4, 5, 6]}
        caption="Grupos de la cohorte de triage"
        headers={[
          "Grupo",
          "Zona",
          "Lectura clínica",
          "Reales",
          "Sintéticas",
          "Ingreso (reales)",
          "Silueta",
        ]}
      >
        {data.map((row) => (
          <tr key={row.cluster}>
            <td className="font-semibold">Grupo {row.cluster}</td>
            <td>{upperFirst(row.zona)}</td>
            <td className="min-w-64 max-w-xl !whitespace-normal">
              {row.descripcion}
            </td>
            <td className="tabular-nums">{number(row.n_reales)}</td>
            <td className="tabular-nums">{number(row.n_sinteticos)}</td>
            <td className="tabular-nums">
              {number(row.tasa_ingreso * 100)} %
            </td>
            <td className="tabular-nums">{number(row.silueta, 2)}</td>
          </tr>
        ))}
      </DataTable>
      <p className="m-5 max-w-3xl text-sm text-ink-muted">
        Sobre el número de grupos: se usa k = 3 porque el planteamiento pide tres
        zonas clínicas (lejos, intermedia y cerca del caso crítico), no porque
        los datos lo elijan. La silueta es baja en todos los k probados (0,13 a
        0,17) y la mejor es la de k = 2 (0,17 contra 0,14), así que los grupos
        son solo una lectura que ordena el riesgo: la tasa de ingreso sube de un
        grupo al siguiente. «Lejos» significa «menos crítico», no «sano».
      </p>
    </>
  );
}
const sections = [
  ["destino", "Dónde enviar"],
  ["espera", "Esperas"],
  ["movimiento", "Ingresos y altas"],
  ["perfiles", "Perfiles"],
  ["agrupamiento", "Agrupamiento"],
  ["cohorte", "Cohorte de triage"],
  ["preparacion", "Preparación de camas"],
  ["modelos", "Modelos de IA"],
];
// Highlights the section being read; without IntersectionObserver nothing is marked.
function useActiveSection() {
  const [active, setActive] = useState("");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries)
          if (entry.isIntersecting) visible.add(entry.target.id);
          else visible.delete(entry.target.id);
        const first = sections.find(([id]) => visible.has(id));
        if (first) setActive(first[0]);
      },
      { rootMargin: "-10% 0px -60% 0px" },
    );
    for (const [id] of sections) {
      const node = document.getElementById(id);
      if (node) observer.observe(node);
    }
    return () => observer.disconnect();
  }, []);
  return active;
}
function SectionNav() {
  const active = useActiveSection();
  return (
    <nav
      aria-label="Secciones de indicadores"
      className="sticky top-0 z-10 -mx-4 overflow-x-auto border-b border-line bg-canvas px-4 sm:-mx-8 sm:px-8"
    >
      <ul className="flex min-w-max gap-5">
        {sections.map(([id, label]) => (
          <li key={id}>
            <a
              href={`#${id}`}
              aria-current={active === id ? "location" : undefined}
              className={`inline-flex min-h-11 items-center border-b-2 text-sm hov:text-accent-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent ${active === id ? "border-accent font-bold text-accent-strong" : "border-transparent font-semibold text-ink-muted"}`}
            >
              {label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
export function AnalyticsPage() {
  const [k, setK] = useState("");
  const decision = useQuery({
    queryKey: ["analytics", "decision"],
    queryFn: ({ signal }) => analyticsApi.decision(signal),
  });
  const waits = useQuery({
    queryKey: ["analytics", "waits"],
    queryFn: ({ signal }) => analyticsApi.waits(signal),
  });
  const cohorts = useQuery({
    queryKey: ["analytics", "cohorts"],
    queryFn: ({ signal }) => analyticsApi.cohorts(signal),
  });
  const turnaround = useQuery({
    queryKey: ["analytics", "turnaround"],
    queryFn: ({ signal }) => analyticsApi.turnaround(signal),
  });
  const clusters = useQuery({
    queryKey: ["analytics", "clusters", k],
    queryFn: ({ signal }) => analyticsApi.clusters(k, signal),
  });
  const cohortClusters = useQuery({
    queryKey: ["analytics", "cohort-clusters"],
    queryFn: ({ signal }) => cohortApi.clusters(signal),
  });
  const timeline = useQuery({
    queryKey: ["analytics", "timeline"],
    queryFn: ({ signal }) => occupancyTimeline(signal),
  });
  const recommendations: Record<string, string> = {
    recibir: "Puede recibir",
    "al limite": "Al límite",
    saturada: "Saturada",
  };
  return (
    <div className="space-y-6">
      <SectionNav />
      <Panel
        id="destino"
        title="Dónde enviar al próximo paciente"
        description="Índice de capacidad = camas libres − solicitudes en espera. Si es negativo, la institución está bajo presión."
      >
        <Result query={decision}>
          {(data) =>
            data.organizations.length ? (
              <DataTable
                numeric={[1, 2, 3]}
                caption="Apoyo a la coordinación"
                headers={[
                  "Institución",
                  "Libres",
                  "En espera",
                  "Índice",
                  "Recomendación",
                ]}
              >
                {data.organizations.map((org) => (
                  <tr key={org.organization_id}>
                    <td className="font-semibold">{org.organization_name}</td>
                    <td>{org.available_beds}</td>
                    <td>{org.waiting_requests}</td>
                    <td className="font-semibold tabular-nums">
                      {org.capacity_index > 0 ? "+" : ""}
                      {org.capacity_index}
                    </td>
                    <td>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-semibold ${org.recommendation === "recibir" ? "bg-bed-available-tint text-bed-available-ink" : org.recommendation === "saturada" ? "bg-urgent-tint text-urgent-ink" : "bg-bed-cleaning-tint text-bed-cleaning-ink"}`}
                      >
                        {recommendations[org.recommendation] ||
                          org.recommendation}
                      </span>
                    </td>
                  </tr>
                ))}
              </DataTable>
            ) : (
              <EmptyState title="Sin instituciones para analizar" />
            )
          }
        </Result>
      </Panel>
      <Panel
        id="espera"
        title="Espera por una cama"
        description="Desde la solicitud hasta la asignación. Mediana: la mitad esperó menos que este tiempo, y unos pocos casos largos no la distorsionan."
      >
        <Result query={waits}>
          {(data) =>
            data.by_priority.length ? (
              <DataTable
                numeric={[1, 2, 3, 4]}
                caption="Tiempos de espera por prioridad"
                headers={[
                  "Prioridad",
                  "Asignaciones",
                  "Mediana",
                  "9 de cada 10 esperaron menos de",
                  "Máximo",
                ]}
              >
                {data.by_priority.map((row) => (
                  <tr key={row.priority}>
                    <td>
                      <PriorityBadge priority={row.priority} />
                    </td>
                    <td>{row.assignments}</td>
                    <td className="font-semibold">
                      {minutes(row.median_minutes)}
                    </td>
                    <td>{minutes(row.p90_minutes)}</td>
                    <td>{minutes(row.max_minutes)}</td>
                  </tr>
                ))}
              </DataTable>
            ) : (
              <EmptyState title="Aún no hay asignaciones para medir" />
            )
          }
        </Result>
      </Panel>
      <Panel
        id="movimiento"
        title="Ingresos y altas"
        description="Pacientes que ingresaron y que recibieron el alta cada día, en los últimos 14 días."
      >
        <Result query={timeline}>
          {(data) =>
            data.timeline.length ? (
              <OccupancyChart days={data.timeline} />
            ) : (
              <EmptyState title="Sin movimientos en este período" />
            )
          }
        </Result>
      </Panel>
      <Panel
        id="perfiles"
        title="Grupos por perfil clínico"
        description="Medianas por perfil de referencia: mmHg, mg/dL, %, lat/min."
      >
        <Result query={cohorts}>
          {(data) =>
            data.cohorts.length ? (
              <DataTable
                numeric={[1, 2, 3, 4, 5, 6]}
                caption="Cohortes clínicas"
                headers={[
                  "Perfil",
                  "Pacientes",
                  "Edad media",
                  "PA sistólica",
                  "Glucosa",
                  "Saturación",
                  "Frec. cardíaca",
                ]}
              >
                {data.cohorts.map((row) => (
                  <tr key={row.profile}>
                    <td className="font-semibold">
                      {profileNames[row.profile] || row.profile}
                    </td>
                    <td>{row.patients}</td>
                    <td>{number(row.avg_age)}</td>
                    {[
                      "sistolica",
                      "glucosa",
                      "saturacion",
                      "frecuencia_cardiaca",
                    ].map((key) => (
                      <td key={key}>{number(row.vitals[key]?.median)}</td>
                    ))}
                  </tr>
                ))}
              </DataTable>
            ) : (
              <EmptyState title="Sin perfiles para analizar" />
            )
          }
        </Result>
      </Panel>
      <Panel
        id="agrupamiento"
        title="Agrupamiento automático de pacientes"
        description="Reúne a los pacientes con mediciones parecidas: presión sistólica, glucosa, saturación, frecuencia cardíaca, IMC y edad. El perfil clínico no se usa para formar los grupos; solo sirve después para comprobar el resultado."
      >
        <div className="max-w-xs p-5">
          <SelectField
            label="Número de grupos"
            value={k}
            onChange={(event) => setK(event.target.value)}
          >
            <option value="">Automático (el que mejor separa)</option>
            {[2, 3, 4, 5, 6, 7, 8].map((value) => (
              <option key={value} value={value}>
                {value} grupos
              </option>
            ))}
          </SelectField>
        </div>
        <Result query={clusters}>
          {(data) => <ClusterResults data={data} />}
        </Result>
      </Panel>
      <Panel
        id="cohorte"
        title="Cohorte de triage: reales y sintéticas"
        description="Visitas reales a urgencias (MIMIC-IV-ED Demo) agrupadas por su cercanía al caso crítico. Las filas sintéticas se generan dentro de cada grupo y no se usan para evaluar los modelos."
      >
        <Result query={cohortClusters}>
          {(data) => <CohortClusters data={data} />}
        </Result>
      </Panel>
      <Panel
        id="preparacion"
        title="Preparación de camas"
        description="Tiempo desde que empieza la limpieza hasta que la cama queda lista para el siguiente paciente."
      >
        <Result query={turnaround}>
          {(data) =>
            data.by_organization.length ? (
              <DataTable
                numeric={[1, 2, 3]}
                caption="Preparación de camas por institución"
                headers={["Institución", "Ciclos", "Mediana", "Máximo"]}
              >
                {data.by_organization.map((row) => (
                  <tr key={row.organization_id}>
                    <td className="font-semibold">{row.organization_name}</td>
                    <td>{row.cycles}</td>
                    <td>{minutes(row.median_minutes)}</td>
                    <td>{minutes(row.max_minutes)}</td>
                  </tr>
                ))}
              </DataTable>
            ) : (
              <EmptyState title="Aún no hay ciclos de limpieza completos" />
            )
          }
        </Result>
      </Panel>
      <ModelsSection />
    </div>
  );
}
