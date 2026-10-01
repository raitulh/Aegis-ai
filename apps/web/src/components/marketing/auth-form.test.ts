import { describe, expect, it } from "vitest";
import { safeNext } from "./auth-form";

describe("post-login redirect", () => {
  it("allows same-site paths", () => {
    expect(safeNext("/dashboard/findings?severity=critical")).toBe("/dashboard/findings?severity=critical");
    expect(safeNext("/invite?token=abc")).toBe("/invite?token=abc");
  });
  it("rejects anything that could leave the site", () => {
    for (const bad of ["https://evil.example", "//evil.example", "/\\\\evil.example", "javascript:alert(1)", "", null, undefined]) {
      expect(safeNext(bad)).toBe("/dashboard");
    }
  });
});
