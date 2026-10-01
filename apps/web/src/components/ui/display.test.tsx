import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { DemoBanner, PlanLimitNotice } from "./display";

describe("plan limit notice", () => {
  it("explains the limit with real numbers", () => {
    const error = new ApiError(403, "plan_limit_exceeded", "Monthly audit runs reached", "req-1", { metric: "audit_run", used: 25, limit: 25, plan: "free", next_plan: "pro" });
    render(<PlanLimitNotice error={error} />);
    expect(screen.getByText("Monthly audit runs reached")).toBeInTheDocument();
    expect(screen.getByText(/25 of 25 used on the Free plan/)).toBeInTheDocument();
  });
  it("renders nothing for other errors", () => {
    const { container } = render(<PlanLimitNotice error={new ApiError(500, "error", "boom")} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("demo banner", () => {
  it("is explicitly marked DEMO", () => {
    render(<DemoBanner sandbox={false} />);
    expect(screen.getByText("DEMO")).toBeInTheDocument();
    expect(screen.getByText(/Nothing here describes a real organization/)).toBeInTheDocument();
  });
});
