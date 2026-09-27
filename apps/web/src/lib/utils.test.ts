import { describe, expect, it } from "vitest";
import { cn, initials, pct, timeAgo, titleCase } from "./utils";

describe("utils", () => {
  it("merges classes", () => {
    expect(cn("a", false && "b", "c")).toBe("a c");
    expect(cn("px-2", "px-4")).toBe("px-4");
  });
  it("titleCases identifiers", () => {
    expect(titleCase("prompt_injection")).toBe("Prompt Injection");
    expect(titleCase("agent-action")).toBe("Agent Action");
  });
  it("formats percentages", () => {
    expect(pct(94.2, 1)).toBe("94.2%");
  });
  it("derives initials", () => {
    expect(initials("Alex Rivera")).toBe("AR");
    expect(initials(null, "dana@example.com")).toBe("DE");
  });
  it("humanizes recent times", () => {
    expect(timeAgo(new Date().toISOString())).toBe("just now");
  });
});
