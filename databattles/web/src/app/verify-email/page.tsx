"use client";

import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { Spinner } from "@/components/ui/states";
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
    <AuthCard title="Email verification">
      {state === "working" ? <Spinner label="Verifying" /> : state === "ok" ? (
        <div className="flex flex-col items-center text-center"><CheckCircle2 className="h-10 w-10 text-success" /><p className="mt-3 text-sm text-muted">Email verified — signing you in…</p></div>
      ) : (
        <div className="flex flex-col items-center text-center">
          <XCircle className="h-10 w-10 text-danger" />
          <p className="mt-3 text-sm text-muted">{message}</p>
          <p className="mt-4 text-sm">Links expire after 24 hours and work once. <Link href="/login" className="text-accent-strong underline">Sign in</Link> to request a new one.</p>
        </div>
      )}
    </AuthCard>
  );
}

export default function VerifyEmailPage() {
  return <Suspense><Verify /></Suspense>;
}
