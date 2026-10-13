import { api, request } from "./client";

export type CleaningStatus = "PENDING_REVIEW" | "APPROVED" | "REJECTED";
export type CleaningIsolation = "NONE" | "CONTACT" | "DROPLET" | "SPORES";
export type CleaningDisinfectant =
  | "CHLORINE_5000PPM"
  | "QUATERNARY_AMMONIUM"
  | "SPORICIDAL"
  | "OTHER";

export interface CleaningInput {
  started_at: string;
  finished_at: string;
  isolation: CleaningIsolation;
  disinfectant: CleaningDisinfectant;
  curtains_changed: boolean;
  checklist: Record<string, boolean>;
  notes: string | null;
}
export interface CleaningRecord extends CleaningInput {
  id: string;
  location_id: string;
  has_photo: boolean;
  photo_content_type: string | null;
  photo_size: number | null;
  status: CleaningStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  created_by: string;
  created_at: string;
  // AI guess made when the bed entered cleaning; null when the service was down.
  predicted_delay_probability: number | null;
  predicted_delay: boolean | null;
  delay_model_version: string | null;
}

export const cleaningApi = {
  forBed: (bedId: string, signal?: AbortSignal) =>
    api<CleaningRecord[]>(`/beds/${bedId}/cleaning`, { signal }),
  create: (bedId: string, body: CleaningInput) =>
    api<CleaningRecord>(`/beds/${bedId}/cleaning`, { method: "POST", body }),
  // The body is the image itself, like the imaging uploads.
  uploadPhoto: (recordId: string, file: Blob) =>
    api<CleaningRecord>(`/cleaning/${recordId}/photo`, {
      method: "PUT",
      raw: true,
      body: file,
      headers: { "Content-Type": file.type || "image/jpeg" },
    }),
  photo: async (recordId: string, signal?: AbortSignal) =>
    (await request(`/cleaning/${recordId}/photo`, { signal })).blob(),
  review: (recordId: string, approved: boolean, review_notes: string | null) =>
    api<CleaningRecord>(`/cleaning/${recordId}/review`, {
      method: "POST",
      body: { approved, review_notes },
    }),
};
