import { useQuery } from "@tanstack/react-query";
import { referralsApi, type Referral } from "../../api/referrals";
import { REFERRALS_KEY } from "./keys";

// One list for the Remisiones screen and the patient record: the API already
// picks the side by role (recibidas, enviadas, or all for the admin).
export const useReferrals = (enabled = true) =>
  useQuery({
    queryKey: [...REFERRALS_KEY, "list"],
    queryFn: async ({ signal }): Promise<Referral[]> => {
      const list = await referralsApi.list(signal);
      // A degraded response must read as an empty list, not crash the screen.
      return Array.isArray(list) ? list : [];
    },
    enabled,
  });
