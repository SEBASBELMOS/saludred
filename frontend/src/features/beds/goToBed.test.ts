import { expect, it } from "vitest";
import { findBedByCode } from "./goToBed";
import { bed } from "../../test/fixtures";

const beds = ["N-HOS-01", "N-HOS-02", "S-HOS-01", "N-UCI-01"].map((code) => ({
  ...bed,
  id: code,
  code,
}));
it("opens a bed by its full code in any case", () => {
  expect(findBedByCode(beds, " n-hos-01 ")).toEqual({
    kind: "found",
    bed: beds[0],
  });
});
it("accepts a partial code only when one bed matches", () => {
  expect(findBedByCode(beds, "uci")).toMatchObject({ kind: "found" });
  expect(findBedByCode(beds, "hos-01")).toEqual({
    kind: "ambiguous",
    count: 2,
  });
});
it("says when nothing matches", () => {
  expect(findBedByCode(beds, "X-99")).toEqual({ kind: "missing" });
  expect(findBedByCode(beds, "  ")).toEqual({ kind: "missing" });
});
