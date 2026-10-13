import { useState } from "react";
import { aiApi } from "../../api/ai";
import { Action, useToast } from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canDownloadRecord } from "../auth/permissions";
/** Downloads the clinical record PDF. Hidden for roles that get no clinical data. */
export function RecordDownload({
  patientId,
  fileName,
}: {
  patientId: string;
  fileName: string;
}) {
  const { user } = useAuth();
  const [pending, setPending] = useState(false);
  const toast = useToast();
  if (!user || !canDownloadRecord(user.role)) return null;
  return (
    <Action
      secondary
      label="Descargar historia clínica (PDF)"
      loading={pending}
      disabled={pending}
      onClick={async () => {
        setPending(true);
        try {
          await aiApi.downloadRecord(patientId, fileName);
        } catch (error) {
          toast(
            `No se pudo descargar la historia clínica. ${error instanceof Error ? error.message : ""} Intenta de nuevo.`.replace(
              /\s+/g,
              " ",
            ),
            "error",
          );
        } finally {
          setPending(false);
        }
      }}
    />
  );
}
