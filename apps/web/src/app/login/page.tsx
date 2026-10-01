import { Suspense } from "react";
import { AuthForm } from "@/components/marketing/auth-form";

export const metadata = { title: "Sign in" };

export default function Page() {
  return (
    <Suspense>
      <AuthForm mode="login" />
    </Suspense>
  );
}
