import { useState } from "react";
import { patientsApi } from "../../api/patients";
import { Action, useToast } from "../../components/ui";
import { useAuth } from "../auth/AuthProvider";
import { canDownloadRecord } from "../auth/permissions";

/** Downloads the patient's record as a FHIR R4 Bundle (JSON), next to the PDF. */
export function FhirExport({
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
      label="Exportar (FHIR)"
      loading={pending}
      disabled={pending}
      onClick={async () => {
        setPending(true);
        try {
          await patientsApi.downloadFhirBundle(patientId, fileName);
        } catch (error) {
          toast(
            `No se pudo exportar el paciente. ${error instanceof Error ? error.message : ""} Intenta de nuevo.`.replace(
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
