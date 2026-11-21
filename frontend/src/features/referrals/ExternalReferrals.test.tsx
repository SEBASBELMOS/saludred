import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ExternalReferralState } from "../../api/referrals";
import { ExternalChip, externalStates } from "./ExternalReferrals";

const everyState: ExternalReferralState[] = [
  "enviando",
  "enviada",
  "error_envio",
  "aceptada",
  "rechazada",
  "rechazada_por_validacion",
];

describe("external referral states", () => {
  it("has a label for every state the backend can persist", () => {
    expect(Object.keys(externalStates).sort()).toEqual([...everyState].sort());
  });

  it.each(everyState)("renders a readable chip for %s", (estado) => {
    render(<ExternalChip estado={estado} />);
    const chip = screen.getByText(externalStates[estado]);
    expect(chip).toBeInTheDocument();
    expect(chip.className).toMatch(/text-/);
  });

  it("labels the in-flight and failed states in Spanish, with a critical tone for the failure", () => {
    render(
      <>
        <ExternalChip estado="enviando" />
        <ExternalChip estado="error_envio" />
      </>,
    );
    expect(screen.getByText("Enviando")).toBeInTheDocument();
    expect(screen.getByText("Error de envío").className).toContain("critical");
  });
});
