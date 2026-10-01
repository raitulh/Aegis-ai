"use client";

import { Eye, EyeOff } from "lucide-react";
import { forwardRef, useState, type InputHTMLAttributes } from "react";

import { Input } from "@/components/ui/form";
import { cn } from "@/lib/cn";

/**
 * Password field with a show/hide toggle. Pure UI: the value, name and autocomplete pass straight through to
 * the underlying `Input`, so `<Field>` wiring (id, aria-invalid, aria-describedby) is unchanged. The toggle is a
 * separately labelled button ("Show password", aria-pressed) so the input keeps its own label.
 */
export const PasswordInput = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, "type">>(function PasswordInput(
  { className, ...props },
  ref,
) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input ref={ref} {...props} type={visible ? "text" : "password"} className={cn("pr-11", className)} />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label="Show password"
        aria-pressed={visible}
        aria-controls={props.id}
        disabled={props.disabled}
        className={cn(
          "absolute right-0.5 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-[8px] text-subtle",
          "transition-colors duration-200 hover:bg-surface-2 hover:text-fg disabled:opacity-50",
          "focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--ring)]",
        )}
      >
        {visible ? <EyeOff className="h-4 w-4 animate-pop" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
      </button>
    </div>
  );
});
