import { useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import type { Study } from "../../api/types";
import { imagingApi } from "../../api/imaging";
import { useInvalidateClinical } from "../../api/query";
import { Action, DataTable, Notice, useToast } from "../../components/ui";
import { EmptyState } from "../../components/EmptyState";
import { dateOnly } from "../../components/format";
const modalities = {
  CR: "Radiografía",
  DX: "Radiografía digital",
  CT: "Tomografía",
  MR: "Resonancia",
  US: "Ecografía",
  XA: "Angiografía",
  NM: "Medicina nuclear",
};
export function UploadImage({ studyId }: { studyId: string }) {
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const invalidate = useInvalidateClinical();
  const mutation = useMutation({
    mutationFn: async (file: File) => {
      if (file.size > 15 * 1024 * 1024)
        throw new Error("El archivo supera el máximo de 15 MB.");
      return imagingApi.upload(studyId, file);
    },
    onSuccess: async (result) => {
      toast(
        `Imagen guardada en el PACS. El estudio tiene ${result.images} ${result.images === 1 ? "imagen" : "imágenes"}`,
      );
      await invalidate();
    },
  });
  return (
    <>
      <input
        ref={input}
        type="file"
        accept=".dcm,.dicom,application/dicom,image/png,image/jpeg"
        className="sr-only"
        tabIndex={-1}
        aria-label="Archivo de imagen"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) mutation.mutate(file);
          event.target.value = "";
        }}
      />
      <Action
        secondary
        label={mutation.isPending ? "Subiendo…" : "Subir imagen"}
        disabled={mutation.isPending}
        onClick={() => input.current?.click()}
      />
      {mutation.error && <Notice>{mutation.error.message}</Notice>}
    </>
  );
}
export function StudyList({
  studies,
  canUpload = false,
  onView,
}: {
  studies: Study[];
  canUpload?: boolean;
  onView: (study: Study) => void;
}) {
  if (!studies.length)
    return <EmptyState title="Sin estudios de imagen registrados" />;
  return (
    <DataTable
      caption="Estudios de imagen"
      headers={["Fecha", "Estudio", "Imágenes"]}
    >
      {[...studies]
        .sort((a, b) => b.started_at.localeCompare(a.started_at))
        .map((study) => (
          <tr key={study.id}>
            <td>{dateOnly(study.started_at)}</td>
            <td>
              <strong>{study.description || modalities[study.modality]}</strong>
              <small className="block text-ink-muted">
                {modalities[study.modality]}, {study.body_site}
              </small>
            </td>
            <td>
              <div className="flex flex-wrap items-start gap-2">
                {study.pacs_study_id ? (
                  <Action
                    secondary
                    label="Ver imágenes"
                    onClick={() => onView(study)}
                  />
                ) : (
                  <span className="text-xs text-ink-muted">Sin imágenes</span>
                )}
                {canUpload && <UploadImage studyId={study.id} />}
              </div>
            </td>
          </tr>
        ))}
    </DataTable>
  );
}
