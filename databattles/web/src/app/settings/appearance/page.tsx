"use client";

import { Check, Monitor, Moon, Palette, Sun, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { setReducedMotion, useTheme } from "@/components/shell/theme";
import { Switch } from "@/components/ui/form";
import { cn } from "@/lib/cn";
import { SettingsPageHeading, SettingsSection } from "../_components/settings-ui";

const THEMES: { value: "dark" | "light" | "system"; label: string; description: string; icon: LucideIcon }[] = [
  { value: "dark", label: "Dark", description: "The default DataBattles look.", icon: Moon },
  { value: "light", label: "Light", description: "Bright surfaces for daylight.", icon: Sun },
  { value: "system", label: "System", description: "Follow your device setting.", icon: Monitor },
];

/**
 * Illustrative miniature of the app chrome (no content). The light miniature is scoped with
 * `data-theme="light"` so it uses the real light tokens; the dark one is mixed from black and the brand
 * hues so it reads as dark even while the light theme is active.
 */
function Miniature({ tone }: { tone: "dark" | "light" }) {
  const dark = tone === "dark";
  return (
    <div
      data-theme={dark ? undefined : "light"}
      className={cn(
        "absolute inset-0 flex flex-col gap-1.5 p-2.5",
        dark ? "bg-[color-mix(in_oklab,black_88%,var(--blue))]" : "bg-bg",
      )}
    >
      <div className={cn("flex h-4 items-center gap-1 rounded-[5px] border px-1.5", dark ? "border-white/10 bg-white/[0.04]" : "border-border bg-surface")}>
        <span className="h-1.5 w-1.5 rounded-full bg-brand" />
        <span className={cn("h-1 w-6 rounded-full", dark ? "bg-white/20" : "bg-surface-3")} />
        <span className={cn("ml-auto h-1.5 w-4 rounded-full", dark ? "bg-white/15" : "bg-surface-3")} />
      </div>
      <div className="flex min-h-0 flex-1 gap-1.5">
        <div className={cn("flex w-1/4 flex-col gap-1 rounded-[5px] border p-1.5", dark ? "border-white/10 bg-white/[0.03]" : "border-border bg-surface")}>
          <span className="h-1 w-full rounded-full bg-brand opacity-80" />
          <span className={cn("h-1 w-3/4 rounded-full", dark ? "bg-white/15" : "bg-surface-3")} />
          <span className={cn("h-1 w-2/3 rounded-full", dark ? "bg-white/15" : "bg-surface-3")} />
        </div>
        <div className={cn("flex flex-1 flex-col gap-1 rounded-[5px] border p-1.5", dark ? "border-white/10 bg-white/[0.05]" : "border-border bg-surface shadow-card")}>
          <span className={cn("h-1.5 w-1/2 rounded-full", dark ? "bg-white/40" : "bg-fg/70")} />
          <span className={cn("h-1 w-5/6 rounded-full", dark ? "bg-white/15" : "bg-surface-3")} />
          <span className={cn("h-1 w-2/3 rounded-full", dark ? "bg-white/15" : "bg-surface-3")} />
          <div className="mt-auto flex items-end gap-0.5">
            {[40, 65, 50, 80, 60].map((h, i) => (
              <span key={i} className="w-1.5 rounded-sm bg-brand" style={{ height: `${h / 8}px`, opacity: 0.45 + i * 0.1 }} />
            ))}
            <span className="ml-auto h-2.5 w-7 rounded-[3px] bg-brand" />
          </div>
        </div>
      </div>
    </div>
  );
}

function ThemePreview({ value }: { value: "dark" | "light" | "system" }) {
  if (value === "system") {
    return (
      <>
        <Miniature tone="dark" />
        <div className="absolute inset-0 [clip-path:polygon(58%_0,100%_0,100%_100%,42%_100%)]">
          <Miniature tone="light" />
        </div>
        <span aria-hidden className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 rotate-[14deg] scale-y-125 bg-[linear-gradient(180deg,transparent,var(--accent-strong),transparent)] opacity-70" />
      </>
    );
  }
  return <Miniature tone={value} />;
}

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

  const motionOff = reduced || osReduced;

  return (
    <div className="space-y-10">
      <SettingsPageHeading icon={<Palette />} title="Appearance" description="Choose how DataBattles looks and moves on this device." />

      <SettingsSection title="Theme" description="Saved on this device.">
        <div role="radiogroup" aria-label="Theme" className="grid gap-3 sm:grid-cols-3">
          {THEMES.map(({ value, label, description, icon: Icon }) => {
            const active = pref === value;
            return (
              <label
                key={value}
                className={cn(
                  "group relative flex cursor-pointer flex-col overflow-hidden rounded-[var(--radius-lg)] border bg-bg-elevated transition-[border-color,box-shadow,transform] duration-200 ease-out-expo",
                  "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--ring)] has-[:focus-visible]:ring-offset-2 has-[:focus-visible]:ring-offset-surface",
                  active
                    ? "border-[color-mix(in_oklab,var(--accent)_60%,var(--border-strong))] shadow-glow"
                    : "border-border hover:-translate-y-0.5 hover:border-border-strong hover:shadow-card",
                )}
              >
                <input type="radio" name="theme" value={value} checked={active} onChange={() => setPref(value)} className="sr-only" />
                <div aria-hidden className="relative h-28 overflow-hidden border-b border-border">
                  <ThemePreview value={value} />
                </div>
                <div className="flex items-start gap-3 p-3.5">
                  <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", active ? "text-accent-strong" : "text-muted")} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-fg">{label}</span>
                    <span className="mt-0.5 block text-xs text-muted">{description}</span>
                  </span>
                  <span
                    aria-hidden
                    className={cn(
                      "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors duration-200",
                      active ? "border-transparent bg-accent text-accent-fg" : "border-border-strong bg-surface",
                    )}
                  >
                    {active ? <Check className="h-3 w-3 animate-pop" strokeWidth={3} /> : null}
                  </span>
                </div>
              </label>
            );
          })}
        </div>
        {pref === "system" ? <p className="text-xs text-subtle">Currently using the {resolved} theme from your device.</p> : null}
      </SettingsSection>

      <SettingsSection title="Motion" description="Saved on this device.">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1 space-y-3">
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
          </div>
          <div aria-hidden className="flex shrink-0 items-center gap-3 rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3.5 py-3 sm:w-44 sm:flex-col sm:items-center sm:gap-2 sm:py-4">
            <span className="relative flex h-11 w-11 items-center justify-center">
              <span className="absolute inset-0 rounded-full border border-dashed border-border-strong" />
              <span className="absolute inset-0 animate-[spin_5s_linear_infinite]">
                <span className="absolute -top-[3px] left-1/2 h-1.5 w-1.5 -translate-x-1/2 rounded-full bg-cyan shadow-[0_0_8px_var(--cyan)]" />
              </span>
              <span className="h-3 w-3 rounded-full bg-brand" />
            </span>
            <span className="text-left sm:text-center">
              <span className="block text-eyebrow text-subtle">Preview</span>
              <span className="mt-0.5 block text-xs text-muted">{motionOff ? "Motion reduced" : "Full motion"}</span>
            </span>
          </div>
        </div>
      </SettingsSection>
    </div>
  );
}
