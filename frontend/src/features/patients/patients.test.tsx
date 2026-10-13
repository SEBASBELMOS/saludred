import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { searchCriterion, validateVitals, vitalCatalog } from "./validation";
import { PatientResults, PatientsPage } from "./PatientsPage";
import { PatientFormDialog } from "./PatientFormDialog";
import { AuthProvider } from "../auth/AuthProvider";
import { renderUi } from "../../test/render";
import { patient, response, user } from "../../test/fixtures";
import { sessionToken } from "../../api/client";
import { dateOnly } from "../../components/format";
describe("patient search", () => {
  it("requires a criterion with six document characters or three letters", () => {
    expect(searchCriterion("")).toBeNull();
    expect(searchCriterion("12345")?.error).toBeTruthy();
    expect(searchCriterion("123456")).toEqual({
      key: "document_number",
      value: "123456",
    });
    expect(searchCriterion("Ma")?.error).toBeTruthy();
    expect(searchCriterion("Mar")).toEqual({ key: "name", value: "Mar" });
    expect(searchCriterion("AB1234")).toEqual({
      key: "document_number",
      value: "AB1234",
    });
    expect(searchCriterion("ABCDEF", "document_number")).toEqual({
      key: "document_number",
      value: "ABCDEF",
    });
    expect(searchCriterion("---", "name")?.error).toBeTruthy();
  });
  it("shows the total and never renders more than ten results", () => {
    renderUi(
      <PatientResults
        patients={Array.from({ length: 12 }, (_, i) => ({
          ...patient,
          id: String(i),
        }))}
        total={42}
        onOpen={vi.fn()}
      />,
    );
    expect(
      screen.getByText("42 coincidencias. Se muestran las 10 primeras."),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: "Ver paciente" }),
    ).toHaveLength(10);
  });
  it("does not request patient lists without a valid criterion", async () => {
    sessionToken.set("test-session");
    const fetch = vi
      .fn()
      .mockImplementation(async (url: string) =>
        url.endsWith("/auth/me")
          ? response(user)
          : response({ items: [], total: 0, page: 1, page_size: 10 }),
      );
    vi.stubGlobal("fetch", fetch);
    renderUi(
      <AuthProvider>
        <PatientsPage />
      </AuthProvider>,
    );
    const search = await screen.findByLabelText("Documento completo o nombre");
    await userEvent.type(search, "12");
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(
      fetch.mock.calls.filter(([url]) => String(url).includes("/patients")),
    ).toHaveLength(0);
    await userEvent.clear(search);
    await userEvent.type(search, "Mar");
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url]) =>
            String(url).includes("name=Mar") &&
            String(url).includes("page_size=10"),
        ),
      ).toBe(true),
    );
  });
});
it.each(vitalCatalog)("validates both boundaries of $name", (v) => {
  expect(validateVitals({ [v.code]: String(v.min) })).toHaveLength(1);
  expect(validateVitals({ [v.code]: String(v.max) })).toHaveLength(1);
  expect(() => validateVitals({ [v.code]: String(v.min - 1) })).toThrow(
    "fuera del rango",
  );
  expect(() => validateVitals({ [v.code]: String(v.max + 1) })).toThrow(
    "fuera del rango",
  );
});
it("requires systolic pressure to be greater than diastolic pressure", () => {
  expect(() => validateVitals({ "8480-6": "80", "8462-4": "80" })).toThrow(
    "La presión sistólica debe ser mayor",
  );
  expect(() => validateVitals({ "8480-6": "70", "8462-4": "80" })).toThrow();
  expect(validateVitals({ "8480-6": "120", "8462-4": "80" })).toHaveLength(2);
  expect(validateVitals({ "8310-5": "36,5" })).toHaveLength(1);
  expect(() => validateVitals({ "8310-5": "NaN" })).toThrow();
});
it("keeps the document read-only while editing", () => {
  renderUi(
    <PatientFormDialog patient={patient} onClose={vi.fn()} onSaved={vi.fn()} />,
  );
  expect(screen.getByLabelText("Documento")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("Documento")).toHaveValue("CC 12345678");
});
it("does not shift calendar birth dates across time zones", () =>
  expect(dateOnly("1976-05-13")).toBe("13 may 1976"));
