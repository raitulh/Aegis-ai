"use client";

import dynamic from "next/dynamic";
import { Component, useEffect, useRef, useState, type ReactNode } from "react";

import { useTheme } from "@/components/shell/theme";
import { cn } from "@/lib/cn";
import { useInView, useReducedMotion } from "@/lib/motion";
import { DataOrbitFallback } from "./data-orbit-fallback";
import type { OrbitQuality } from "./data-orbit-scene";
import { ORBIT_CONCEPTS } from "./orbit-concepts";

// three.js + R3F live in their own chunk and are only requested once WebGL is confirmed and the browser is idle.
const DataOrbitScene = dynamic(() => import("./data-orbit-scene"), { ssr: false, loading: () => null });

type Mode = "fallback" | "loading" | "ready" | "failed";

function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(window.WebGLRenderingContext && (canvas.getContext("webgl2") || canvas.getContext("webgl")));
  } catch {
    return false;
  }
}

class SceneBoundary extends Component<{ children: ReactNode; onError: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onError();
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/**
 * The hero visualisation. Renders the SVG orbit immediately (server-rendered, zero JS cost), then
 * cross-fades to the WebGL scene when it is supported, motion is allowed and the page is idle.
 * The canvas pauses whenever the hero is off screen, and complexity drops on small or low-power devices.
 */
export function DataOrbit({ className }: { className?: string }) {
  const { resolved } = useTheme();
  const reduced = useReducedMotion();
  const [mode, setMode] = useState<Mode>("fallback");
  const [quality, setQuality] = useState<OrbitQuality>("high");
  const [active, setActive] = useState<number | null>(null);
  const activeRef = useRef<number | null>(null);
  const labelsRef = useRef<(HTMLDivElement | null)[]>([]);
  const [ref, inView] = useInView<HTMLDivElement>({ once: false, rootMargin: "160px 0px" });

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    if (reduced) return;
    if (!hasWebGL()) {
      setMode("failed");
      return;
    }
    const nav = navigator as Navigator & { deviceMemory?: number };
    const low = window.innerWidth < 768 || (nav.hardwareConcurrency ?? 8) <= 4 || (nav.deviceMemory ?? 8) <= 4;
    setQuality(low ? "low" : "high");
    const w = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    const start = () => setMode((m) => (m === "fallback" ? "loading" : m));
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(start, { timeout: 1800 });
      return () => w.cancelIdleCallback?.(id);
    }
    const t = window.setTimeout(start, 700);
    return () => window.clearTimeout(t);
  }, [reduced]);

  const showScene = !reduced && (mode === "loading" || mode === "ready");
  const sceneVisible = showScene && mode === "ready";
  const current = active === null ? null : ORBIT_CONCEPTS[active];

  return (
    <div ref={ref} aria-hidden className={cn("relative isolate select-none", className)}>
      <DataOrbitFallback
        className={cn("absolute inset-0 transition-opacity duration-700", sceneVisible && "opacity-0")}
        active={sceneVisible ? null : active}
        onActive={sceneVisible ? undefined : setActive}
      />
      {showScene ? (
        <SceneBoundary onError={() => setMode("failed")}>
          <div className={cn("absolute inset-0 transition-opacity duration-1000 ease-out-expo", sceneVisible ? "opacity-100" : "opacity-0")}>
            <div className="absolute inset-0 [mask-image:radial-gradient(ellipse_72%_68%_at_50%_50%,black_55%,transparent_100%)]">
            <DataOrbitScene
              concepts={ORBIT_CONCEPTS}
              quality={quality}
              theme={resolved}
              running={inView}
              activeRef={activeRef}
              labelsRef={labelsRef}
              onReady={() => setMode("ready")}
              onLost={() => setMode("failed")}
            />
            </div>
            <div className="pointer-events-none absolute inset-0 overflow-hidden">
              {ORBIT_CONCEPTS.map((c, i) => (
                <div
                  key={c.key}
                  ref={(el) => {
                    labelsRef.current[i] = el;
                  }}
                  onPointerEnter={() => setActive(i)}
                  onPointerLeave={() => setActive(null)}
                  className={cn(
                    "pointer-events-auto absolute left-0 top-0 whitespace-nowrap rounded-full border px-2.5 py-1 font-mono text-[10.5px] tracking-[0.14em] transition-[background-color,border-color,color] duration-200 will-change-transform",
                    active === i ? "border-border-strong bg-surface-2/95 text-fg shadow-glow" : "border-border bg-[var(--glass-strong)] text-fg/90 backdrop-blur-md",
                  )}
                >
                  <span className="text-subtle">{String(i + 1).padStart(2, "0")}</span> {c.label.toUpperCase()}
                </div>
              ))}
            </div>
          </div>
        </SceneBoundary>
      ) : null}

      <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center px-4">
        <div className="max-w-md rounded-full border border-border bg-glass px-3.5 py-1.5 text-center text-[11px] text-muted backdrop-blur-md">
          {current ? (
            <span>
              <span className="font-mono tracking-[0.12em] text-fg">{current.label.toUpperCase()}</span> · {current.caption}
            </span>
          ) : (
            <span className="font-mono tracking-[0.12em]">
              {ORBIT_CONCEPTS.map((c, i) => (
                <span key={c.key}>
                  {i ? <span className="mx-1 text-subtle">→</span> : null}
                  {c.label.toUpperCase()}
                </span>
              ))}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
