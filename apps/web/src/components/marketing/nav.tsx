"use client";
import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/logo";
import { Sheet, SheetContent } from "@/components/ui/overlays";
import { Button } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/product", label: "Product" },
  { href: "/solutions", label: "Solutions" },
  { href: "/developers", label: "Developers" },
  { href: "/pricing", label: "Pricing" },
  { href: "/trust", label: "Trust" },
  { href: "/docs", label: "Docs" },
];

export function MarketingNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-40 border-b border-[var(--color-border)]/70 bg-[var(--color-bg)]/80 backdrop-blur-xl">
      <a href="#content" className="skip-link">
        Skip to content
      </a>
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link href="/" aria-label="Aegis home" className="rounded-md focus-ring">
          <Logo />
        </Link>
        <nav aria-label="Main" className="hidden items-center gap-1 md:flex">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              aria-current={pathname === l.href ? "page" : undefined}
              className={cn("rounded-md px-3 py-2 text-sm transition-colors hover:text-[var(--color-text)] focus-ring", pathname === l.href ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]")}
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <Link href="/login" className="hidden sm:block">
            <Button variant="ghost" size="sm">
              Sign in
            </Button>
          </Link>
          <Link href="/demo">
            <Button size="sm">Try the sandbox</Button>
          </Link>
          <button type="button" aria-label="Open menu" onClick={() => setOpen(true)} className="rounded-md p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] focus-ring md:hidden">
            <Menu className="h-5 w-5" />
          </button>
        </div>
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent title="Menu" side="left" className="max-w-xs">
          <nav aria-label="Mobile" className="flex flex-col p-3">
            {[...LINKS, { href: "/contact", label: "Contact" }, { href: "/login", label: "Sign in" }].map((l) => (
              <Link key={l.href} href={l.href} onClick={() => setOpen(false)} className="rounded-md px-3 py-2.5 text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]">
                {l.label}
              </Link>
            ))}
          </nav>
        </SheetContent>
      </Sheet>
    </header>
  );
}
