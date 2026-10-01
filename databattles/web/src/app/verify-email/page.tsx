"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Mail, MailX } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { AuthStatus } from "@/components/auth/auth-status";
import { errorMessage, post } from "@/lib/api";
import { qk } from "@/lib/query";
import type { Me } from "@/lib/types";

function Verify() {
  const token = useSearchParams().get("token");
  const router = useRouter();
  const qc = useQueryClient();
  const [state, setState] = useState<"working" | "ok" | "error">("working");
  const [message, setMessage] = useState("");
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    if (!token) {
      setState("error");
      setMessage("This link is missing its token.");
      return;
    }
    post<{ user: Me }>("/auth/verify-email", { token })
      .then((res) => {
        qc.setQueryData(qk.me, res.user);
        setState("ok");
        let next = "/dashboard";
        try {
          next = localStorage.getItem("db-post-auth-next") || "/dashboard";
          localStorage.removeItem("db-post-auth-next");
        } catch {
          /* ignore */
        }
        setTimeout(() => router.push(res.user.onboarding_completed ? next : `/onboarding?next=${encodeURIComponent(next)}`), 1200);
      })
      .catch((e) => {
        setState("error");
        setMessage(errorMessage(e));
      });
  }, [token, router, qc]);

  return (
    <AuthCard eyebrow="Verification" title="Email verification">
      {state === "working" ? (
        <AuthStatus key="working" tone="accent" busy icon={<Mail />} title="Verifying your email…">
          This only takes a moment.
        </AuthStatus>
      ) : state === "ok" ? (
        <AuthStatus key="ok" tone="success" celebrate title="Email verified — signing you in…" />
      ) : (
        <AuthStatus key="error" tone="danger" icon={<MailX />} title="This link didn't work">
          <p>{message}</p>
          <p className="mt-3 text-fg/80">Links expire after 24 hours and work once. <Link href="/login" className="whitespace-nowrap font-medium text-accent-strong underline">Sign in</Link> to request a new one.</p>
        </AuthStatus>
      )}
    </AuthCard>
  );
}

export default function VerifyEmailPage() {
  return <Suspense><Verify /></Suspense>;
}
