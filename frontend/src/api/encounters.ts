import { api } from "./client";
import type { Encounter, EncounterInput } from "./types";
export const createEncounter = (body: EncounterInput) =>
  api<Encounter>("/encounters", { method: "POST", body });
