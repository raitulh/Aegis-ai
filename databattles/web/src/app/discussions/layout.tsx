import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Discussions",
  description: "Ask questions, share approaches and help other students in the DataBattles community.",
};

export default function DiscussionsLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
