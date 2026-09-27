import Link from "next/link";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/primitives";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 aegis-radial">
      <Logo />
      <p className="font-mono text-6xl font-semibold text-[var(--color-text-subtle)]">404</p>
      <p className="text-[var(--color-text-muted)]">This page could not be found.</p>
      <Link href="/dashboard"><Button variant="secondary">Back to dashboard</Button></Link>
    </div>
  );
}
