import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "../../components/ui";
import { isConflict } from "./labels";

export const INVOICES_KEY = ["invoices"] as const;
export const SUMMARY_KEY = ["invoice-summary"] as const;

export function useRefreshBilling() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: INVOICES_KEY }),
      client.invalidateQueries({ queryKey: SUMMARY_KEY }),
    ]);
}

/**
 * A 409 means somebody else already changed the invoice: reload the list and
 * say so. Returns true when the error was handled that way.
 */
export function useConflictHandler() {
  const toast = useToast();
  const refresh = useRefreshBilling();
  return (error: Error) => {
    if (!isConflict(error)) return false;
    toast(
      "La factura cambió de estado mientras la veías. Se actualizó la lista.",
      "warn",
    );
    void refresh();
    return true;
  };
}
