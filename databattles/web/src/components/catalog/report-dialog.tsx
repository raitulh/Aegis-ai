"use client";

import { Flag } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, FormError, Select, Textarea } from "@/components/ui/form";
import { ApiError, post } from "@/lib/api";
import { useApiMutation } from "@/lib/hooks";

const REASONS = [
  { value: "spam", label: "Spam or advertising" },
  { value: "abuse", label: "Harassment or abuse" },
  { value: "misleading", label: "Misleading or fraudulent" },
  { value: "copyright", label: "Copyright or license violation" },
  { value: "privacy", label: "Exposes private information" },
  { value: "other", label: "Something else" },
] as const;

/** POST /reports — lets a signed-in user flag content for moderator review. */
export function ReportDialog({
  targetType,
  targetId,
  targetLabel,
}: {
  targetType: "thread" | "comment" | "project" | "dataset" | "user" | "competition";
  targetId: string;
  targetLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string>("spam");
  const [details, setDetails] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const report = useApiMutation(
    () => post<{ message: string }>("/reports", { target_type: targetType, target_id: targetId, reason, details: details.trim() || null }),
    {
      success: (d) => d.message,
      onSuccess: () => {
        setOpen(false);
        setDetails("");
        setReason("spam");
        setError(null);
      },
      onError: (e) => setError(e),
    },
  );
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setError(null);
      }}
      title={`Report ${targetLabel}`}
      description="Moderators review every report. Reports are confidential."
      size="sm"
      trigger={
        <Button variant="ghost" size="sm" className="max-sm:h-9" icon={<Flag className="h-4 w-4" aria-hidden />}>
          Report
        </Button>
      }
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="danger" loading={report.isPending} onClick={() => report.mutate(undefined)}>
            Submit report
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Reason" error={error?.fields.reason}>
          {(p) => (
            <Select {...p} value={reason} onChange={(e) => setReason(e.target.value)}>
              {REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Details (optional)" hint="Add links or context that help a moderator decide." error={error?.fields.details}>
          {(p) => <Textarea {...p} rows={4} maxLength={1000} value={details} onChange={(e) => setDetails(e.target.value)} />}
        </Field>
        {error && !Object.keys(error.fields).length ? <FormError message={error.message} /> : null}
      </div>
    </Dialog>
  );
}
