"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

type Pref = "dark" | "light" | "system";
const KEY = "db-theme";

const ThemeCtx = createContext<{ pref: Pref; resolved: "dark" | "light"; setPref: (p: Pref) => void }>({
  pref: "dark",
  resolved: "dark",
  setPref: () => {},
});

/** Inline script (runs before paint) so the chosen theme never flashes. Dark is the default. */
export const themeInitScript = `(function(){try{var p=localStorage.getItem('${KEY}')||'dark';var r=p==='system'?(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):p;document.documentElement.dataset.theme=r;var m=localStorage.getItem('db-motion');if(m)document.documentElement.dataset.motion=m;}catch(e){}})();`;

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<Pref>("dark");
  const [resolved, setResolved] = useState<"dark" | "light">("dark");

  const apply = useCallback((p: Pref) => {
    const r = p === "system" ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : p;
    document.documentElement.dataset.theme = r;
    setResolved(r);
  }, []);

  useEffect(() => {
    let stored: Pref = "dark";
    try {
      stored = (localStorage.getItem(KEY) as Pref) || "dark";
    } catch {
      /* ignore */
    }
    setPrefState(stored);
    apply(stored);
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      if ((localStorage.getItem(KEY) || "dark") === "system") apply("system");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [apply]);

  const setPref = useCallback(
    (p: Pref) => {
      try {
        localStorage.setItem(KEY, p);
      } catch {
        /* ignore */
      }
      setPrefState(p);
      apply(p);
    },
    [apply],
  );

  return <ThemeCtx.Provider value={{ pref, resolved, setPref }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);

export function setReducedMotion(on: boolean) {
  try {
    if (on) localStorage.setItem("db-motion", "reduced");
    else localStorage.removeItem("db-motion");
  } catch {
    /* ignore */
  }
  if (on) document.documentElement.dataset.motion = "reduced";
  else delete document.documentElement.dataset.motion;
}
