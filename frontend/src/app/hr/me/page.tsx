"use client";

import { HrProfileView } from "@/components/hr-profile";

/** "Hồ sơ của tôi" (0096): one's own whole profile; contact changes go to HCNS. */
export default function MyHrProfilePage() {
  return <HrProfileView userId="me" me />;
}
