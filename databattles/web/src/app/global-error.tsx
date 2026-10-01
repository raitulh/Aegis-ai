"use client";

import Link from "next/link";
import { useEffect } from "react";

/*
 * Last-resort boundary. It replaces the root layout, so no providers, global CSS, fonts or design tokens are
 * available: everything below is self-contained — a small inline stylesheet (theme, focus, reduced motion)
 * and an inline SVG orbit. The palette mirrors the app tokens because `globals.css` is not loaded here.
 * Dark is the default (as in the app shell); the effect below switches to light for a stored preference.
 */
const CSS = `
:root{--bg:#06070c;--surface:rgb(13 16 24/.78);--border:rgb(150 165 255/.14);--grid:rgb(150 165 255/.05);--fg:#eef0f8;--muted:#a5adc4;--subtle:#7b84a1;--accent:#8b6dff;--accent-strong:#a998ff;--cyan:#2dd4ef;--danger:#f87171;--glow:rgb(139 109 255/.2);--shadow:0 30px 70px -24px rgb(0 0 0/.6);color-scheme:dark}
:root[data-theme="light"]{--bg:#f6f7fb;--surface:rgb(255 255 255/.86);--border:rgb(20 30 70/.12);--grid:rgb(20 30 70/.05);--fg:#0b1020;--muted:#4a5468;--subtle:#646d84;--accent:#5b3df5;--accent-strong:#4a2de0;--cyan:#0891b2;--danger:#dc2626;--glow:rgb(91 61 245/.1);--shadow:0 2px 6px rgb(15 23 42/.05),0 30px 60px -24px rgb(15 23 42/.22);color-scheme:light}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:var(--bg);color:var(--fg);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.ge-bg{position:fixed;inset:0;pointer-events:none;background:radial-gradient(60% 45% at 50% 0%,var(--glow),transparent 70%)}
.ge-grid{position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);background-size:56px 56px;-webkit-mask-image:radial-gradient(ellipse 70% 60% at 50% 40%,#000 20%,transparent 75%);mask-image:radial-gradient(ellipse 70% 60% at 50% 40%,#000 20%,transparent 75%)}
.ge-card{position:relative;width:100%;max-width:440px;padding:40px 28px 32px;text-align:center;border:1px solid var(--border);border-radius:28px;background:var(--surface);-webkit-backdrop-filter:blur(16px);backdrop-filter:blur(16px);box-shadow:var(--shadow)}
.ge-orbit{transform-origin:60px 60px;animation:ge-spin 40s linear infinite}
@keyframes ge-spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion: reduce){.ge-orbit{animation:none}}
[data-motion="reduced"] .ge-orbit{animation:none}
.ge-eyebrow{margin:28px 0 0;font:500 11px/16px ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--danger)}
h1{margin:10px 0 0;font-size:26px;line-height:1.15;letter-spacing:-.03em;font-weight:620;text-wrap:balance}
.ge-text{margin:12px auto 0;max-width:340px;font-size:15px;line-height:1.6;color:var(--muted)}
.ge-ref{margin:16px 0 0;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--subtle);overflow-wrap:anywhere}
.ge-actions{margin-top:28px;display:flex;flex-wrap:wrap;gap:8px;justify-content:center}
.ge-btn{display:inline-flex;align-items:center;justify-content:center;height:40px;padding:0 18px;border-radius:10px;font-family:inherit;font-size:14px;font-weight:500;line-height:1;text-decoration:none;cursor:pointer;transition:filter .2s,background-color .2s,border-color .2s}
.ge-primary{border:0;color:#fff;background:linear-gradient(180deg,color-mix(in oklab,var(--accent) 88%,#fff),var(--accent))}
.ge-primary:hover{filter:brightness(1.1)}
.ge-secondary{border:1px solid var(--border);color:var(--fg);background:transparent}
.ge-secondary:hover{border-color:var(--accent-strong)}
.ge-btn:focus-visible{outline:2px solid var(--accent-strong);outline-offset:2px}
`;

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  // Mirror the app shell's `themeInitScript`: dark by default, "system" follows the OS, and the in-app
  // reduced-motion setting (`db-motion`) is honoured alongside `prefers-reduced-motion`.
  useEffect(() => {
    const root = document.documentElement;
    try {
      const pref = localStorage.getItem("db-theme") || "dark";
      root.dataset.theme =
        pref === "system" ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : pref;
      const motion = localStorage.getItem("db-motion");
      if (motion) root.dataset.motion = motion;
    } catch {
      root.dataset.theme = "dark";
    }
  }, []);

  return (
    <html lang="en">
      <head>
        <title>DataBattles is having trouble</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style dangerouslySetInnerHTML={{ __html: CSS }} />
      </head>
      <body>
        <div className="ge-bg" aria-hidden />
        <div className="ge-grid" aria-hidden />
        <main className="ge-card">
          <svg width="120" height="120" viewBox="0 0 120 120" fill="none" aria-hidden style={{ display: "block", margin: "0 auto" }}>
            <circle cx="60" cy="60" r="56" stroke="var(--border)" strokeDasharray="3 6" />
            <g className="ge-orbit">
              <circle cx="60" cy="4" r="3.5" fill="var(--danger)" />
            </g>
            <ellipse cx="60" cy="60" rx="58" ry="20" transform="rotate(-16 60 60)" stroke="var(--danger)" strokeOpacity="0.3" />
            <circle cx="60" cy="60" r="38" stroke="var(--border)" />
            <circle cx="60" cy="60" r="26" fill="var(--bg)" stroke="var(--border)" />
            <path d="M60 50v12" stroke="var(--danger)" strokeWidth="2.4" strokeLinecap="round" />
            <circle cx="60" cy="69" r="1.6" fill="var(--danger)" />
          </svg>
          <p className="ge-eyebrow">Unexpected error</p>
          <h1>DataBattles is having trouble</h1>
          <p className="ge-text">Please refresh the page. If this keeps happening, try again later.</p>
          {error?.digest ? <p className="ge-ref">Reference: {error.digest}</p> : null}
          <div className="ge-actions">
            <button type="button" onClick={reset} className="ge-btn ge-primary">
              Retry
            </button>
            {/* A full document load rebuilds the root layout; a client-side transition could land back in the
                broken tree. Modified clicks (new tab/window) keep the browser default. */}
            <Link
              href="/"
              prefetch={false}
              onNavigate={(e) => {
                e.preventDefault();
                // A full document load is the point here: a client-side push could re-enter the failed root layout.
                window.location.assign(window.location.origin);
              }}
              className="ge-btn ge-secondary"
            >
              Go to homepage
            </Link>
          </div>
        </main>
      </body>
    </html>
  );
}
