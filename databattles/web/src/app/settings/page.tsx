"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Spinner } from "@/components/ui/states";

export default function SettingsIndexPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/settings/profile");
  }, [router]);
  return <Spinner label="Opening profile settings" />;
}
