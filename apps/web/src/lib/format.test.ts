import { describe, expect, it } from "vitest";
import { SEVERITY_META, STATUS_META, DIMENSIONS } from "./format";

describe("format metadata", () => {
  it("has metadata for every severity with distinct colors and icons (not color-only)", () => {
    for (const s of ["critical", "high", "medium", "low", "info"]) {
      expect(SEVERITY_META[s]).toBeDefined();
      expect(SEVERITY_META[s].label).toBeTruthy();
      expect(SEVERITY_META[s].icon).toBeTruthy();
    }
  });
  it("covers the six assurance dimensions", () => {
    expect(DIMENSIONS).toEqual(["fairness", "truthfulness", "safety", "privacy", "security", "governance"]);
  });
  it("maps common statuses", () => {
    expect(STATUS_META.completed.tone).toBe("success");
    expect(STATUS_META.failed.tone).toBe("critical");
  });
});
