"use client";

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

const control =
  "w-full rounded-[var(--radius-md)] border border-border bg-bg-elevated px-3 text-sm text-fg placeholder:text-subtle " +
  "transition-colors hover:border-border-strong focus:border-accent focus:outline-none focus:ring-2 focus:ring-[var(--ring)] " +
  "disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-danger";

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
    <select ref={ref} className={cn(control, "h-10 pr-8", className)} {...props}>
      {children}
    </select>
  );
});

export function Label({ htmlFor, children, className }: { htmlFor?: string; children: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn("text-sm font-medium text-fg", className)}>
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
      {hint && !error ? <p id={hintId} className="text-xs text-subtle">{hint}</p> : null}
      {error ? <p id={errId} role="alert" className="text-xs font-medium text-danger">{error}</p> : null}
    </div>
  );
}

export function Checkbox({ label, description, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; description?: ReactNode }) {
  const id = useId();
  return (
    <div className={cn("flex items-start gap-3", className)}>
      <input id={id} type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--accent)]" {...props} />
      <label htmlFor={id} className="text-sm">
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
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-border transition-colors disabled:opacity-50",
          checked ? "bg-accent" : "bg-surface-3",
        )}
      >
        <span className={cn("inline-block h-4 w-4 rounded-full bg-white shadow transition-transform", checked ? "translate-x-6" : "translate-x-1")} />
      </button>
    </div>
  );
}

export function FormError({ message }: { message?: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded-[var(--radius-md)] border border-danger/40 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </div>
  );
}

/** Comma/Enter-separated tag input. */
export function TagInput({ value, onChange, placeholder, max = 12, id }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; max?: number; id?: string }) {
  return (
    <div className={cn(control, "flex min-h-10 flex-wrap items-center gap-1.5 py-1.5")}>
      {value.map((t) => (
        <span key={t} className="inline-flex items-center gap-1 rounded-md bg-surface-3 px-2 py-0.5 text-xs">
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
