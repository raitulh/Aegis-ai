import type { ReactNode } from "react";
import { MarketingNav } from "@/components/marketing/nav";
import { MarketingFooter } from "@/components/marketing/footer";

export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <MarketingNav />
      <main className="mx-auto max-w-4xl px-6 py-16">{children}</main>
      <MarketingFooter />
    </div>
  );
}
