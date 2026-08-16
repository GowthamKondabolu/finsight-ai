import { afterEach, describe, expect, it } from "vitest";

import {
  backendRequestHeaders,
  isAllowedApiPath,
  resolveBackendUrl,
} from "@/lib/proxy";

afterEach(() => {
  delete process.env.FINSIGHT_API_BASE_URL;
  delete process.env.FINSIGHT_API_AUTH_TOKEN;
});

describe("analyst API proxy policy", () => {
  it("allows only bounded investigation and health routes", () => {
    expect(isAllowedApiPath("health")).toBe(true);
    expect(isAllowedApiPath("v1/investigations/runs")).toBe(true);
    expect(
      isAllowedApiPath(
        "v1/investigations/runs/11111111-1111-4111-8111-111111111111/review",
      ),
    ).toBe(true);
    expect(isAllowedApiPath("v1/experiments/private/analysis")).toBe(false);
    expect(isAllowedApiPath("../../metadata")).toBe(false);
    expect(isAllowedApiPath("v1/investigations/runs/------------------------------------")).toBe(
      false,
    );
  });

  it("resolves an allowlisted path against the configured backend only", () => {
    process.env.FINSIGHT_API_BASE_URL = "https://api.internal.example/base/";
    expect(resolveBackendUrl("health").toString()).toBe(
      "https://api.internal.example/base/health",
    );
  });

  it("keeps the deployment token on the server-side backend request", () => {
    process.env.FINSIGHT_API_AUTH_TOKEN = "opaque-deployment-token";

    expect(backendRequestHeaders("GET")).toEqual({
      Authorization: "Bearer opaque-deployment-token",
    });
    expect(backendRequestHeaders("POST")).toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer opaque-deployment-token",
    });
  });

  it("rejects non-http backends and paths", () => {
    process.env.FINSIGHT_API_BASE_URL = "file:///tmp/unsafe";
    expect(() => resolveBackendUrl("health")).toThrow(/HTTP or HTTPS/);
    expect(() => resolveBackendUrl("v1/admin/secrets")).toThrow(/allowlisted/);

    process.env.FINSIGHT_API_BASE_URL = "https://user:secret@api.internal.example";
    expect(() => resolveBackendUrl("health")).toThrow(/cannot contain credentials/);
  });
});
