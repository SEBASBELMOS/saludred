import { expect, it } from "vitest";
import type { Role } from "../../api/types";
import {
  canAssign,
  canChangeBed,
  canCreate,
  canRegisterCleaning,
  canRegisterEncounter,
  canReviewCleaning,
  homeFor,
  isIpsScoped,
  menuFor,
} from "./permissions";

const paths = (role: Role) => menuFor(role).map((item) => item.path);

it("gives the especialista the referrals plus the patients, queue and beds of their IPS", () => {
  expect(paths("SPECIALIST")).toEqual([
    "/queue",
    "/beds",
    "/patients",
    "/referrals",
  ]);
  expect(homeFor("SPECIALIST")).toBe("/referrals");
});
it("keeps the especialista read-only on queue and beds, as the API does", () => {
  // POST /beds/{id}/status, assign and release answer 403 to the especialista.
  expect(canChangeBed("SPECIALIST")).toBe(false);
  expect(canAssign("SPECIALIST")).toBe(false);
  expect(canCreate("SPECIALIST")).toBe(false);
  // They do register encounters, measurements and images within their IPS.
  expect(canRegisterEncounter("SPECIALIST")).toBe(true);
  expect(isIpsScoped("SPECIALIST")).toBe(true);
});
it("lets the contable see only billing and the paciente only their portal", () => {
  expect(paths("ACCOUNTANT")).toEqual(["/billing"]);
  expect(paths("PATIENT")).toEqual(["/portal"]);
  expect(canRegisterEncounter("ACCOUNTANT")).toBe(false);
  expect(canRegisterEncounter("PATIENT")).toBe(false);
});
it("shows the care network screen to the admin only", () => {
  for (const role of [
    "EPS_COORDINATOR",
    "IPS_CLINICAL_OPERATOR",
    "SPECIALIST",
    "ACCOUNTANT",
    "PATIENT",
  ] as Role[])
    expect(paths(role)).not.toContain("/organizations");
  expect(paths("ADMIN")).toContain("/organizations");
});
it("splits cleaning evidence between who registers and who approves", () => {
  expect(canRegisterCleaning("IPS_CLINICAL_OPERATOR")).toBe(true);
  expect(canReviewCleaning("IPS_CLINICAL_OPERATOR")).toBe(false);
  expect(canRegisterCleaning("EPS_COORDINATOR")).toBe(false);
  expect(canReviewCleaning("EPS_COORDINATOR")).toBe(true);
  // The admin may do both; the API still refuses approving their own record.
  expect(canRegisterCleaning("ADMIN")).toBe(true);
  expect(canReviewCleaning("ADMIN")).toBe(true);
  for (const role of ["SPECIALIST", "ACCOUNTANT", "PATIENT"] as Role[]) {
    expect(canRegisterCleaning(role)).toBe(false);
    expect(canReviewCleaning(role)).toBe(false);
  }
});
