import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "Learn",
  description: "Short AI and data-science courses with server-graded quizzes, hands-on challenges and verifiable certificates.",
};

export default function LearnLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
