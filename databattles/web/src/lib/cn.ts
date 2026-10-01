import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge taught about the design system's custom utilities (see globals.css): the type-scale
 * utilities are font sizes (not text colours), and `text-gradient` is its own thing — otherwise
 * `cn("text-eyebrow text-accent-strong")` would silently drop the eyebrow style.
 */
const twMerge = extendTailwindMerge<"text-gradient">({
  extend: {
    classGroups: {
      "font-size": [{ text: ["eyebrow", "title", "headline", "display"] }],
      "text-gradient": ["text-gradient"],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
