import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AiReport, ModelMetrics } from "../../api/ai";
import type { Role } from "../../api/types";
import { sessionToken } from "../../api/client";
import { AuthProvider } from "../auth/AuthProvider";
import { canDownloadRecord, canReviewAi } from "../auth/permissions";
import { renderUi } from "../../test/render";
import { patient, response, user } from "../../test/fixtures";
import { AiAnalysisSection } from "./AiAnalysisSection";
import { ModelsSection, subgroupLabel } from "./ModelsSection";
import { PendingReports } from "./PendingReports";
import { RecordDownload } from "./RecordDownload";
const report: AiReport = {
  id: "report-1",
  patient_id: patient.id,
  kind: "criticality",
  estado: "pendiente",
  pseudonym: "pz-3f9a1c2d4e5b6a70",
  probability: 0.874,
  nivel: "critico",
  critico: true,
  threshold: 0.465,
  model_version: "criticality-lr-1.0-ab12cd34",
  imputed: ["acuity"],
  top_features: [
    { feature: "o2sat", value: 88, contribution: 0.41, imputed: false },
    { feature: "acuity", value: 3, contribution: -0.12, imputed: true },
  ],
  requested_by: "user-1",
  reviewed_by: null,
  firmado_por: null,
  reviewed_at: null,
  observaciones: null,
  motivo_rechazo: null,
  solicitud_cama_id: null,
  created_at: "2026-10-02T20:36:00Z",
};
const payload = {
  pseudonym: "pz-3f9a1c2d4e5b6a70",
  age: 41,
  sex: "female",
  o2sat: 88,
  heart_rate: 112,
};
interface Stub {
  reports?: AiReport[];
  analyze?: Response;
  review?: Response;
}
// Answers the endpoints the AI screens call and records every request.
function stubApi(role: Role, stub: Stub = {}) {
  sessionToken.set("test-session");
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/auth/me")) return response({ ...user, role });
    if (url.includes("/ai-analyses") && method === "POST")
      return stub.analyze ?? response(report, 201);
    if (/\/ai-reports\/[^/]+\/(approve|reject)$/.test(url))
      return stub.review ?? response({ ...report, estado: "aprobado" });
    if (url.endsWith("/payload")) return response({ payload });
    if (url.includes("/ai-reports")) return response(stub.reports ?? [report]);
    if (url.includes("/patients/")) return response(patient);
    return response({}, 404);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const posts = (fetch: ReturnType<typeof stubApi>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === "POST");
const renderSection = () =>
  renderUi(
    <AuthProvider>
      <AiAnalysisSection patientId={patient.id} />
    </AuthProvider>,
  );
describe("role gating", () => {
  it.each(["IPS_CLINICAL_OPERATOR", "SPECIALIST"] as Role[])(
    "lets %s request and review",
    async (role) => {
      stubApi(role);
      renderSection();
      expect(
        await screen.findByRole("button", { name: "Solicitar análisis" }),
      ).toBeInTheDocument();
      expect(
        await screen.findByRole("button", { name: "Aprobar" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Rechazar" }),
      ).toBeInTheDocument();
    },
  );
  it.each(["ADMIN", "EPS_COORDINATOR"] as Role[])(
    "shows %s the report but no request or review buttons",
    async (role) => {
      stubApi(role);
      renderSection();
      expect(await screen.findByText("Pendiente de firma")).toBeInTheDocument();
      expect(
        screen.getByText(/Falta la firma de un médico o especialista/),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Solicitar análisis" }),
      ).toBeNull();
      expect(screen.queryByRole("button", { name: "Aprobar" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Rechazar" })).toBeNull();
    },
  );
  it("does not offer the review of a report that is already signed", async () => {
    stubApi("SPECIALIST", {
      reports: [
        { ...report, estado: "aprobado", firmado_por: "Dr. Ricardo Torres" },
      ],
    });
    renderSection();
    expect(await screen.findByText("Dr. Ricardo Torres")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aprobar" })).toBeNull();
  });
  it("limits the helpers to the roles the API accepts", () => {
    expect(canReviewAi("IPS_CLINICAL_OPERATOR")).toBe(true);
    expect(canReviewAi("SPECIALIST")).toBe(true);
    for (const role of [
      "ADMIN",
      "EPS_COORDINATOR",
      "ACCOUNTANT",
      "PATIENT",
    ] as Role[])
      expect(canReviewAi(role)).toBe(false);
    expect(canDownloadRecord("PATIENT")).toBe(true);
    expect(canDownloadRecord("ACCOUNTANT")).toBe(false);
  });
  it("shows the PDF download to the patient and hides it from the accountant", async () => {
    stubApi("PATIENT");
    const patientView = renderUi(
      <AuthProvider>
        <RecordDownload patientId={patient.id} fileName="historia.pdf" />
      </AuthProvider>,
    );
    expect(
      await screen.findByRole("button", {
        name: "Descargar historia clínica (PDF)",
      }),
    ).toBeInTheDocument();
    patientView.unmount();
    stubApi("ACCOUNTANT");
    renderUi(
      <AuthProvider>
        <RecordDownload patientId={patient.id} fileName="historia.pdf" />
      </AuthProvider>,
    );
    await waitFor(() => expect(sessionToken.get()).toBeTruthy());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      screen.queryByRole("button", {
        name: "Descargar historia clínica (PDF)",
      }),
    ).toBeNull();
  });
});
describe("review", () => {
  it("requires a reason to reject and sends it with the observations", async () => {
    const fetch = stubApi("IPS_CLINICAL_OPERATOR", {
      review: response({ ...report, estado: "rechazado" }),
    });
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Rechazar" }),
    );
    const confirm = screen.getByRole("button", { name: "Confirmar rechazo" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Motivo del rechazo"), "  ");
    expect(confirm).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("Motivo del rechazo"),
      "Datos desactualizados",
    );
    await userEvent.type(
      screen.getByLabelText("Observaciones (opcional)"),
      "Repetir con signos nuevos",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    await waitFor(() => expect(posts(fetch)).toHaveLength(1));
    const [url, init] = posts(fetch)[0];
    expect(url).toBe("/api/v1/ai-reports/report-1/reject");
    expect(JSON.parse(String(init?.body))).toEqual({
      motivo: "Datos desactualizados",
      observaciones: "Repetir con signos nuevos",
    });
  });
  it("approves without a reason and says the score feeds the bed queue", async () => {
    const fetch = stubApi("SPECIALIST");
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Aprobar" }),
    );
    expect(screen.getByText(/cola de camas/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Confirmar aprobación" }),
    );
    await waitFor(() => expect(posts(fetch)).toHaveLength(1));
    expect(posts(fetch)[0][0]).toBe("/api/v1/ai-reports/report-1/approve");
  });
  it("tells the clinician when someone else already signed the report", async () => {
    stubApi("SPECIALIST", {
      review: response(
        { detail: "El reporte ya fue revisado (aprobado)" },
        409,
      ),
    });
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Aprobar" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Confirmar aprobación" }),
    );
    expect(
      await screen.findByText(/Otro clínico ya revisó este reporte/),
    ).toBeInTheDocument();
  });
});
describe("request an analysis", () => {
  it("explains that nothing was created when the AI service is down", async () => {
    stubApi("IPS_CLINICAL_OPERATOR", {
      reports: [],
      analyze: response(
        { detail: "El servicio de IA no esta disponible" },
        503,
      ),
    });
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Solicitar análisis" }),
    );
    expect(
      await screen.findByText(
        "El servicio de IA no responde. El reporte no se creó; intenta de nuevo en unos minutos.",
      ),
    ).toBeInTheDocument();
  });
  it("says which data to record when there is not enough", async () => {
    stubApi("SPECIALIST", {
      reports: [],
      analyze: response(
        { detail: "El paciente no tiene suficientes datos clinicos" },
        422,
      ),
    });
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", { name: "Solicitar análisis" }),
    );
    expect(
      await screen.findByText(/Registra al menos 3 de estas mediciones/),
    ).toBeInTheDocument();
  });
  it("sends the triage level only when one is chosen", async () => {
    const fetch = stubApi("SPECIALIST", { reports: [] });
    renderSection();
    await userEvent.selectOptions(
      await screen.findByLabelText("Nivel de triage ESI (opcional)"),
      "2",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Solicitar análisis" }),
    );
    await waitFor(() => expect(posts(fetch)).toHaveLength(1));
    expect(JSON.parse(String(posts(fetch)[0][1]?.body))).toEqual({
      triage_level: 2,
    });
  });
});
describe("report details", () => {
  it("shows probability, level, threshold, model, imputed variables and top features", async () => {
    stubApi("SPECIALIST");
    renderSection();
    expect(await screen.findByText("87,4 %")).toBeInTheDocument();
    expect(screen.getByText("Crítico")).toBeInTheDocument();
    expect(screen.getByText("46,5 %")).toBeInTheDocument();
    expect(screen.getByText("criticality-lr-1.0-ab12cd34")).toBeInTheDocument();
    expect(screen.getByText("Saturación de oxígeno 88 %")).toBeInTheDocument();
    expect(
      screen.getByText("Variables imputadas").nextSibling,
    ).toHaveTextContent("Nivel de triage ESI");
  });
  it("shows the exact payload sent to the model in plain words and as raw JSON", async () => {
    stubApi("SPECIALIST");
    renderSection();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Ver datos enviados al modelo",
      }),
    );
    const view = await screen.findByRole("region", {
      name: "Datos enviados al modelo",
    });
    expect(within(view).getByText("pz-3f9a1c2d4e5b6a70")).toBeInTheDocument();
    expect(within(view).getByText("41 años")).toBeInTheDocument();
    expect(within(view).getByText("mujer")).toBeInTheDocument();
    expect(within(view).getByText("112 lat/min")).toBeInTheDocument();
    expect(
      within(view).getByText(/No incluye nombre, documento/),
    ).toBeInTheDocument();
    expect(within(view).queryByLabelText("JSON enviado al modelo")).toBeNull();
    await userEvent.click(
      within(view).getByRole("button", { name: "Ver JSON sin formato" }),
    );
    expect(
      within(view).getByLabelText("JSON enviado al modelo").textContent,
    ).toBe(JSON.stringify(payload, null, 2));
  });
});
describe("reports to review", () => {
  it("says there is nothing to review when the list is empty", async () => {
    stubApi("IPS_CLINICAL_OPERATOR", { reports: [] });
    renderUi(
      <AuthProvider>
        <PendingReports />
      </AuthProvider>,
    );
    expect(
      await screen.findByText("No hay reportes por revisar."),
    ).toBeInTheDocument();
  });
  it("lists the pending reports of the clinician and opens the patient", async () => {
    const fetch = stubApi("IPS_CLINICAL_OPERATOR");
    renderUi(
      <AuthProvider>
        <PendingReports />
      </AuthProvider>,
    );
    expect(
      await screen.findByRole("button", {
        name: "Revisar reporte de María Pérez",
      }),
    ).toBeInTheDocument();
    expect(
      fetch.mock.calls.some(([url]) =>
        String(url).includes("estado=pendiente"),
      ),
    ).toBe(true);
  });
  it("marks a report whose patient no longer exists and offers no review", async () => {
    const fetch = stubApi("SPECIALIST");
    fetch.mockImplementation(async (url: string) =>
      url.endsWith("/auth/me")
        ? response({ ...user, role: "SPECIALIST" })
        : url.includes("/ai-reports")
          ? response([report])
          : response({ detail: "Paciente no encontrado" }, 404),
    );
    renderUi(
      <AuthProvider>
        <PendingReports />
      </AuthProvider>,
    );
    expect(
      await screen.findByText("Paciente no disponible"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Revisar/ })).toBeNull();
  });
  it("is not requested for roles that cannot sign", async () => {
    const fetch = stubApi("ADMIN");
    renderUi(
      <AuthProvider>
        <PendingReports />
      </AuthProvider>,
    );
    await screen
      .findByText("Cargando información", { exact: false })
      .catch(() => null);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      fetch.mock.calls.some(([url]) => String(url).includes("/ai-reports")),
    ).toBe(false);
  });
});
describe("model metrics", () => {
  const metrics: ModelMetrics = {
    model_version: "criticality-lr-1.0-ab12cd34",
    data: { real_rows: 192, positive_rate: 0.661 },
    validation: { scheme: "5x5", evaluated_on_synthetic_rows: false },
    evaluation: {
      auc: { mean: 0.64, std: 0.036 },
      threshold: 0.465,
      target_recall: 0.85,
      at_threshold: {
        recall: 0.85,
        precision: 0.688,
        specificity: 0.246,
        f1: 0.76,
      },
      flagged_rate: 0.818,
      baseline_flag_everyone: { recall: 1, precision: 0.661 },
    },
    bias_by_subgroup: {
      "sex=female": {
        n: 106,
        n_positive: 66,
        positive_rate: 0.6,
        recall: 0.824,
        precision: 0.67,
        specificity: 0.3,
        f1: 0.7,
        auc: 0.663,
        flagged_rate: 0.77,
      },
      "age=<45": {
        n: 14,
        n_positive: 9,
        positive_rate: 0.6,
        recall: 0.889,
        precision: 0.77,
        specificity: 0.3,
        f1: 0.8,
        auc: 0.804,
        flagged_rate: 0.74,
      },
    },
  };
  it("reads every figure from the API, flags small samples and states the modest AUC", async () => {
    sessionToken.set("test-session");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/auth/me")
          ? response({ ...user, role: "EPS_COORDINATOR" })
          : response({ seed: 2026, criticality: metrics }),
      ),
    );
    renderUi(<ModelsSection />);
    expect(
      await screen.findByText("Criticidad del paciente"),
    ).toBeInTheDocument();
    expect(screen.getByText("0,640")).toBeInTheDocument();
    expect(screen.getByText(/El AUC es modesto \(0,640\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/la de marcar a todos \(66,1 %\)/),
    ).toBeInTheDocument();
    const small = screen.getByText("Edad: menos de 45 años").closest("tr")!;
    expect(within(small).getByText("muestra pequeña")).toBeInTheDocument();
    expect(subgroupLabel("age=>=65")).toBe("Edad: 65 años o más");
    const large = screen.getByText("Sexo: mujeres").closest("tr")!;
    expect(within(large).queryByText("muestra pequeña")).toBeNull();
    // Without a comparison the section still renders and shows no comparison block.
    expect(screen.queryByText("Comparación de modelos")).toBeNull();
  });
  const compared = (
    id: string,
    nombre: string,
    f1: number,
    winner: boolean,
  ): NonNullable<ModelMetrics["comparacion"]>["models"][number] => ({
    id,
    nombre,
    familia: "x",
    auc: { mean: 0.7, std: 0.01 },
    threshold: 0.4,
    f1,
    f1_se: 0.02,
    precision: 0.7,
    recall: 0.85,
    specificity: 0.3,
    confusion: { tp: 40.5, fn: 7.5, fp: 20, tn: 24 },
    meets_recall_target: true,
    fit_seconds: 0.4,
    winner,
  });
  const withComparison: ModelMetrics = {
    ...metrics,
    model_version: "criticality-rf-1.0-ab12cd34",
    comparacion: {
      protocol: { repeats: 3, folds: 5, target_recall: 0.85 },
      baseline_flag_everyone: { recall: 1, precision: 0.661, f1: 0.796 },
      models: [
        compared("lr", "Regresión logística", 0.76, false),
        compared("rf", "Random forest (bagging)", 0.78, true),
      ],
      winner: "rf",
      rule: "Gana el modelo con mayor F1 fuera de muestra.",
      decision: "mejor F1",
      importance: {
        method: "permutación",
        reading: "La variable que más pesa es «Edad».",
        shown_model: "rf",
        shown_model_name: "Random forest (bagging)",
        by_model: {
          rf: [
            { feature: "age", label: "Edad", importance: 0.08 },
            { feature: "o2sat", label: "Saturación de oxígeno", importance: 0.04 },
          ],
        },
      },
      odds_ratios: {
        model_name: "Regresión logística",
        note: "nota",
        items: [
          {
            feature: "acuity",
            label: "Nivel de triage ESI",
            coefficient: -0.9,
            odds_ratio: 0.41,
            reading: "Por cada desviación estándar más, los momios disminuyen 59%.",
          },
        ],
      },
    },
  };
  it("shows the comparison, marks the winner and draws the confusion matrix", async () => {
    sessionToken.set("test-session");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/auth/me")
          ? response({ ...user, role: "EPS_COORDINATOR" })
          : response({ seed: 2026, criticality: withComparison }),
      ),
    );
    renderUi(<ModelsSection />);
    const table = await screen.findByRole("region", {
      name: "Comparación de modelos",
    });
    const winnerRow = within(table).getByText("Random forest (bagging)").closest("tr")!;
    expect(within(winnerRow).getByText("Ganador")).toBeInTheDocument();
    expect(within(winnerRow).getByText("0,780")).toBeInTheDocument();
    const other = within(table).getByText("Regresión logística").closest("tr")!;
    expect(within(other).queryByText("Ganador")).toBeNull();
    expect(screen.getByText("Modelo en servicio:")).toBeInTheDocument();
    // The baseline of flagging everyone is stated, and no model beats its F1 here.
    expect(screen.getByText(/Ningún modelo la supera en F1/)).toBeInTheDocument();
    // Confusion matrix of the winner with plain labels and counts.
    const matrix = screen.getByRole("table", {
      name: "Matriz de confusión de Random forest (bagging)",
    });
    expect(within(matrix).getByText("Críticos detectados")).toBeInTheDocument();
    expect(within(matrix).getByText("40,5")).toBeInTheDocument();
    const lost = within(matrix).getByText("Críticos perdidos").closest("td")!;
    expect(within(lost).getByText("7,5")).toBeInTheDocument();
    expect(lost.className).toContain("critical");
    const alarms = within(matrix).getByText("Falsas alarmas").closest("td")!;
    expect(alarms.className).not.toContain("critical");
    // Importance and odds ratios with their readings.
    expect(screen.getByText("La variable que más pesa es «Edad».")).toBeInTheDocument();
    expect(screen.getByText("Saturación de oxígeno")).toBeInTheDocument();
    expect(screen.getByText("0,41")).toBeInTheDocument();
    expect(
      screen.getByText(/los momios disminuyen 59%/),
    ).toBeInTheDocument();
    expect(screen.getByText(/La razón de momios compara/)).toBeInTheDocument();
  });
});
