"use client";

import { ArrowLeft } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button, type ButtonProps } from "@/components/ui/button";

/**
 * "Go back" for the not-found and error pages: history back when there is somewhere to go, otherwise home.
 * (Mirrors the private BackButton inside `ui/states`, which isn't exported.)
 */
export function BackButton({ variant = "secondary" }: { variant?: ButtonProps["variant"] }) {
  const router = useRouter();
  return (
    <Button
      variant={variant}
      icon={<ArrowLeft className="h-4 w-4" />}
      onClick={() => {
        if (typeof window !== "undefined" && window.history.length > 1) router.back();
        else router.push("/");
      }}
    >
      Go back
    </Button>
  );
}
