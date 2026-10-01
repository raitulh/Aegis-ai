"use client";
import dynamic from "next/dynamic";

/** Client-only, lazily loaded Trust Core (three.js is never part of the server render or first paint). */
export const TrustCoreLazy = dynamic(() => import("./trust-core"), {
  ssr: false,
  loading: () => <div aria-hidden className="h-full w-full" />,
});
