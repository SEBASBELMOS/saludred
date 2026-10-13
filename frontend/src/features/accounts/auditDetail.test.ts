import { expect, it } from "vitest";
import { auditDetail } from "./auditDetail";
import { dateOnly, dateTime } from "../../components/format";

it("translates known keys and values to plain Spanish", () => {
  expect(auditDetail({ username: "admin", outcome: "success" })).toBe(
    "Usuario: admin; Resultado: correcto",
  );
  expect(auditDetail({ role: "IPS_CLINICAL_OPERATOR" })).toBe(
    "Rol: Operación IPS",
  );
});
it("falls back to a readable key and hides identifiers", () => {
  expect(
    auditDetail({ bed_request_id: "x", custom_field: "valor", empty: null }),
  ).toBe("Custom field: valor");
  expect(auditDetail(null)).toBe("");
});
it("writes dates and date-times in one family", () => {
  expect(dateOnly("1976-05-13")).toBe("13 may 1976");
  expect(dateOnly(null)).toBe("—");
  const year = new Date().getFullYear();
  expect(dateTime(new Date(year, 9, 2, 20, 36).toISOString())).toBe(
    "2 oct, 20:36",
  );
  expect(dateTime(new Date(2020, 0, 5, 8, 5).toISOString())).toBe(
    "5 ene 2020, 08:05",
  );
});
