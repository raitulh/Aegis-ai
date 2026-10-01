import { Suspense } from "react";
import { AcceptInvitation } from "./accept";

export const metadata = { title: "Accept invitation", robots: { index: false } };

export default function InvitePage() {
  return (
    <Suspense>
      <AcceptInvitation />
    </Suspense>
  );
}
