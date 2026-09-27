import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskBadge, SeverityBadge, StatusBadge } from "./primitives";

describe("badges convey meaning beyond color", () => {
  it("severity badge shows a text label", () => {
    render(<SeverityBadge severity="high" />);
    expect(screen.getByText("High")).toBeInTheDocument();
  });
  it("risk badge shows a text label", () => {
    render(<RiskBadge level="critical" />);
    expect(screen.getByText("Critical")).toBeInTheDocument();
  });
  it("status badge shows a text label", () => {
    render(<StatusBadge status="resolved" />);
    expect(screen.getByText("Resolved")).toBeInTheDocument();
  });
});
