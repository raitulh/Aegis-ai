"use client";

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

const control =
  "w-full rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 text-sm text-fg placeholder:text-subtle " +
  "shadow-[inset_0_1px_2px_rgb(0_0_0/0.12)] transition-[border-color,box-shadow,background-color] duration-200 ease-out-expo " +
  "hover:border-border-strong focus:border-accent focus:outline-none " +
  "focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_38%,transparent),inset_0_1px_2px_rgb(0_0_0/0.1)] " +
  "disabled:cursor-not-allowed disabled:opacity-60 " +
  "aria-[invalid=true]:border-danger aria-[invalid=true]:focus:shadow-[0_0_0_3px_color-mix(in_oklab,var(--danger)_38%,transparent)]";

/** Native selects get a theme-neutral chevron (see `.select-chevron` in globals.css). */
const selectChevron = "select-chevron";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(control, "h-10", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(control, "min-h-24 py-2 leading-relaxed", className)} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <select ref={ref} className={cn(control, selectChevron, "h-10 pr-9", className)} {...props}>
      {children}
    </select>
  );
});

export function Label({ htmlFor, children, className }: { htmlFor?: string; children: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn("text-[13px] font-medium tracking-[-0.005em] text-fg", className)}>
      {children}
    </label>
  );
}

/**
 * Accessible field wrapper: wires label, hint and error to the control via ids (aria-describedby / aria-invalid).
 * Usage: <Field label="Title" error={errors.title}>{(p) => <Input {...p} />}</Field>
 */
export function Field({
  label,
  hint,
  error,
  required,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  className?: string;
  children: (props: { id: string; "aria-invalid"?: boolean; "aria-describedby"?: string; required?: boolean }) => ReactNode;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errId = error ? `${id}-err` : undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id}>
        {label}
        {required ? <span className="ml-0.5 text-danger" aria-hidden>*</span> : null}
      </Label>
      {children({ id, "aria-invalid": error ? true : undefined, "aria-describedby": [hintId, errId].filter(Boolean).join(" ") || undefined, required })}
      {hint && !error ? <p id={hintId} className="text-xs leading-relaxed text-subtle">{hint}</p> : null}
      {error ? (
        <p id={errId} role="alert" className="flex items-start gap-1.5 text-xs font-medium text-danger animate-slide-down">
          <svg aria-hidden viewBox="0 0 16 16" className="mt-px h-3.5 w-3.5 shrink-0" fill="currentColor"><path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm-.75 3.25h1.5v4.5h-1.5v-4.5Zm0 5.75h1.5V12h-1.5v-1.5Z" /></svg>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Checkbox({ label, description, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; description?: ReactNode }) {
  const id = useId();
  return (
    <div className={cn("flex items-start gap-3", className)}>
      <input
        id={id}
        type="checkbox"
        className="peer mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded-[5px] accent-[var(--accent)] transition-shadow focus-visible:shadow-[0_0_0_3px_color-mix(in_oklab,var(--accent)_25%,transparent)] disabled:cursor-not-allowed disabled:opacity-50"
        {...props}
      />
      <label htmlFor={id} className="cursor-pointer text-sm peer-disabled:cursor-not-allowed peer-disabled:opacity-60">
        <span className="font-medium text-fg">{label}</span>
        {description ? <span className="mt-0.5 block text-xs text-muted">{description}</span> : null}
      </label>
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <label htmlFor={id} className="text-sm font-medium text-fg">{label}</label>
        {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-[background-color,border-color,box-shadow] duration-200 ease-out-expo disabled:cursor-not-allowed disabled:opacity-50",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
          checked
            ? "border-transparent bg-accent-fill shadow-[inset_0_1px_0_rgb(255_255_255/0.2),0_0_16px_-4px_color-mix(in_oklab,var(--accent)_70%,transparent)]"
            : "border-border-strong bg-surface-3",
        )}
      >
        <span
          className={cn(
            "inline-block h-[18px] w-[18px] rounded-full bg-white shadow-[0_1px_3px_rgb(0_0_0/0.35)] transition-transform duration-300 ease-spring",
            checked ? "translate-x-[22px]" : "translate-x-[2px]",
          )}
        />
      </button>
    </div>
  );
}

export function FormError({ message }: { message?: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-[var(--radius-md)] border border-danger/35 bg-danger-soft px-3 py-2.5 text-sm text-danger animate-slide-down">
      <svg aria-hidden viewBox="0 0 16 16" className="mt-0.5 h-4 w-4 shrink-0" fill="currentColor"><path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm-.75 3.25h1.5v4.5h-1.5v-4.5Zm0 5.75h1.5V12h-1.5v-1.5Z" /></svg>
      <span>{message}</span>
    </div>
  );
}

/** Comma/Enter-separated tag input. */
export function TagInput({ value, onChange, placeholder, max = 12, id }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; max?: number; id?: string }) {
  return (
    <div className={cn(control, "flex min-h-10 flex-wrap items-center gap-1.5 py-1.5")}>
      {value.map((t) => (
        <span key={t} className="inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-2 py-0.5 font-mono text-[11.5px] text-fg animate-pop">
          {t}
          <button type="button" aria-label={`Remove ${t}`} className="text-subtle hover:text-fg" onClick={() => onChange(value.filter((x) => x !== t))}>
            ×
          </button>
        </span>
      ))}
      {value.length < max ? (
        <input
          id={id}
          className="min-w-24 flex-1 bg-transparent text-sm outline-none placeholder:text-subtle"
          placeholder={placeholder}
          onKeyDown={(e) => {
            const target = e.currentTarget;
            if ((e.key === "Enter" || e.key === ",") && target.value.trim()) {
              e.preventDefault();
              const tag = target.value.trim().toLowerCase().slice(0, 48);
              if (!value.includes(tag)) onChange([...value, tag]);
              target.value = "";
            } else if (e.key === "Backspace" && !target.value && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
        />
      ) : null}
    </div>
  );
}
