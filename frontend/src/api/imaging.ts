import { api, request } from "./client";
import type { Page, Study, StudyImages } from "./types";
export const imagingApi = {
  studies: (id: string, signal?: AbortSignal) =>
    api<Page<Study>>(`/patients/${id}/imaging-studies?page_size=100`, {
      signal,
    }),
  images: (id: string, signal?: AbortSignal) =>
    api<StudyImages>(`/imaging-studies/${id}/images`, { signal }),
  preview: async (id: string, signal?: AbortSignal) =>
    (
      await request(`/imaging/instances/${encodeURIComponent(id)}/preview`, {
        signal,
      })
    ).blob(),
  upload: (id: string, file: File) =>
    api<{ images: number; instance_id: string; pacs_study_id: string }>(
      `/imaging-studies/${id}/images`,
      {
        method: "POST",
        raw: true,
        body: file,
        headers: { "Content-Type": file.type || "application/dicom" },
      },
    ),
};
