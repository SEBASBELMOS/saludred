import { expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderUi } from "../../test/render";
import { niceScale, OccupancyChart } from "./OccupancyChart";
import { ClusterResults, CohortClusters } from "./AnalyticsPage";
import type { CohortCluster } from "../../api/types";

const cohort: CohortCluster[] = [
  {
    cluster: 1,
    zona: "lejos del caso crítico",
    descripcion: "SpO2 media 99 %, FC 89, FR 17, PAS 154, ESI medio 2,7",
    n_reales: 77,
    n_sinteticos: 154,
    tasa_ingreso: 0.52,
    silueta: 0.12,
  },
  {
    cluster: 2,
    zona: "intermedio",
    descripcion: "Zona de transición: el modelo puede confundirse",
    n_reales: 72,
    n_sinteticos: 144,
    tasa_ingreso: 0.71,
    silueta: null,
  },
  {
    cluster: 3,
    zona: "cerca del caso crítico",
    descripcion: "FC 109, FR 20",
    n_reales: 43,
    n_sinteticos: 86,
    tasa_ingreso: 0.84,
    silueta: 0.2,
  },
];
it("lists each cohort group with its real and synthetic rows, admission rate and silhouette", () => {
  renderUi(<CohortClusters data={cohort} />);
  const table = screen.getByRole("table", {
    name: "Grupos de la cohorte de triage",
  });
  const rows = within(table).getAllByRole("row");
  // One header row and one row per group.
  expect(rows).toHaveLength(4);
  const first = within(rows[1]);
  expect(first.getByText("Grupo 1")).toBeInTheDocument();
  expect(first.getByText("Lejos del caso crítico")).toBeInTheDocument();
  expect(first.getByText(/ESI medio 2,7/)).toBeInTheDocument();
  expect(first.getByText("77")).toHaveClass("tabular-nums");
  expect(first.getByText("154")).toHaveClass("tabular-nums");
  expect(first.getByText("52 %")).toBeInTheDocument();
  expect(first.getByText("0,12")).toBeInTheDocument();
  // A group without silhouette shows a dash, not a made-up number.
  expect(within(rows[2]).getByText("—")).toBeInTheDocument();
  expect(screen.getByText(/192/)).toBeInTheDocument();
  expect(screen.getByText(/384/)).toBeInTheDocument();
});
it("says honestly why k is 3 even though the silhouette prefers 2", () => {
  renderUi(<CohortClusters data={cohort} />);
  expect(screen.getByText(/Sobre el número de grupos/)).toHaveTextContent(
    "se usa k = 3 porque el planteamiento pide tres zonas clínicas",
  );
  expect(screen.getByText(/Sobre el número de grupos/)).toHaveTextContent(
    "la mejor es la de k = 2",
  );
  expect(screen.getByText(/Sobre el número de grupos/)).toHaveTextContent(
    "«Lejos» significa «menos crítico», no «sano»",
  );
});
it("explains an empty cohort instead of showing an empty table", () => {
  renderUi(<CohortClusters data={[]} />);
  expect(
    screen.getByText("La cohorte de triage aún no se construyó"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});

const days = [
  { date: "2026-09-20", admissions: 7, discharges: 4 },
  { date: "2026-09-21", admissions: 3, discharges: 6 },
  { date: "2026-09-22", admissions: 9, discharges: 5 },
];
it("rounds the axis to whole steps", () => {
  expect(niceScale(9)).toEqual({ step: 5, top: 10 });
  expect(niceScale(0)).toEqual({ step: 1, top: 1 });
  expect(niceScale(47)).toEqual({ step: 20, top: 60 });
});
it("draws two labeled series and keeps the data in a table", () => {
  renderUi(<OccupancyChart days={days} />);
  const chart = screen.getByRole("img");
  expect(within(chart).getByText("Pacientes por día")).toBeInTheDocument();
  expect(within(chart).getByText("Día")).toBeInTheDocument();
  expect(screen.getAllByText("Ingresos").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Altas").length).toBeGreaterThan(0);
  expect(chart).toHaveAccessibleName(/19 ingresos y 15 altas/);
  expect(
    screen.getByText("Ver los datos en tabla").closest("details"),
  ).toBeTruthy();
  expect(
    screen.getByRole("table", { name: "Ingresos y altas por día" }),
  ).toBeInTheDocument();
});
it("explains the cluster scores in plain words", () => {
  renderUi(
    <ClusterResults
      data={{
        patients: 30,
        k: 3,
        k_chosen_by: "silhouette",
        silhouette: 0.41,
        adjusted_rand_index: 0.22,
        purity: 0.7,
        clusters: [],
      }}
    />,
  );
  expect(screen.getAllByText("Separación entre grupos").length).toBeGreaterThan(
    0,
  );
  expect(
    screen.getByText("Coincidencia con el perfil clínico"),
  ).toBeInTheDocument();
  expect(screen.getByText("Pureza del grupo")).toBeInTheDocument();
  expect(screen.queryByText(/Rand ajustado|Silueta/)).not.toBeInTheDocument();
});
