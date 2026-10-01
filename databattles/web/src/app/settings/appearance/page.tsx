"use client";

import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { setReducedMotion, useTheme } from "@/components/shell/theme";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Switch } from "@/components/ui/form";
import { cn } from "@/lib/cn";

const THEMES: { value: "dark" | "light" | "system"; label: string; description: string; icon: LucideIcon }[] = [
  { value: "dark", label: "Dark", description: "The default DataBattles look.", icon: Moon },
  { value: "light", label: "Light", description: "Bright surfaces for daylight.", icon: Sun },
  { value: "system", label: "System", description: "Follow your device setting.", icon: Monitor },
];

export default function AppearanceSettingsPage() {
  const { pref, resolved, setPref } = useTheme();
  const [reduced, setReduced] = useState(false);
  const [osReduced, setOsReduced] = useState(false);

  useEffect(() => {
    try {
      setReduced(localStorage.getItem("db-motion") === "reduced");
    } catch {
      setReduced(document.documentElement.dataset.motion === "reduced");
    }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setOsReduced(mq.matches);
    const onChange = () => setOsReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Theme" description="Saved on this device." />
        <CardBody>
          <div role="radiogroup" aria-label="Theme" className="grid gap-3 sm:grid-cols-3">
            {THEMES.map(({ value, label, description, icon: Icon }) => {
              const active = pref === value;
              return (
                <label
                  key={value}
                  className={cn(
                    "relative flex cursor-pointer flex-col gap-2 rounded-[var(--radius-lg)] border p-4 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--ring)]",
                    active ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong hover:bg-surface-2",
                  )}
                >
                  <input type="radio" name="theme" value={value} checked={active} onChange={() => setPref(value)} className="sr-only" />
                  <Icon className={cn("h-5 w-5", active ? "text-accent-strong" : "text-muted")} aria-hidden />
                  <span className="text-sm font-medium text-fg">{label}</span>
                  <span className="text-xs text-muted">{description}</span>
                </label>
              );
            })}
          </div>
          {pref === "system" ? <p className="mt-3 text-xs text-subtle">Currently using the {resolved} theme from your device.</p> : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Motion" description="Saved on this device." />
        <CardBody className="space-y-3">
          <Switch
            checked={reduced || osReduced}
            disabled={osReduced}
            onChange={(v) => {
              setReducedMotion(v);
              setReduced(v);
            }}
            label="Reduce motion"
            description="Turns off non-essential animations and transitions such as hover lifts, shimmer and progress animations."
          />
          {osReduced ? <p className="text-xs text-subtle">Your operating system already asks for reduced motion, so animations are off everywhere.</p> : null}
        </CardBody>
      </Card>
    </div>
  );
}
