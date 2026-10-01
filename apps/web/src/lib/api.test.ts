import { describe, expect, it } from "vitest";
import { ApiError, path } from "./api";

describe("api client", () => {
  it("URL-encodes interpolated path segments", () => {
    expect(path`/findings/${"abc"}/comments`).toBe("/findings/abc/comments");
    expect(path`/findings/${"../../admin"}`).toBe("/findings/..%2F..%2Fadmin");
    expect(path`/runtime/traces/${"a b?c#d"}`).toBe("/runtime/traces/a%20b%3Fc%23d");
  });

  it("recognises plan-limit errors", () => {
    expect(new ApiError(403, "plan_limit_exceeded", "limit").isPlanLimit).toBe(true);
    expect(new ApiError(403, "forbidden", "no").isPlanLimit).toBe(false);
    expect(new ApiError(429, "plan_limit_exceeded", "rate").isPlanLimit).toBe(false);
  });
});
