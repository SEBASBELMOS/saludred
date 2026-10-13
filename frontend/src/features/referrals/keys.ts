// Every referral list (the screen and the patient record) shares this prefix,
// so one invalidation refreshes them all when a remision_* event arrives.
export const REFERRALS_KEY = ["referrals"] as const;
