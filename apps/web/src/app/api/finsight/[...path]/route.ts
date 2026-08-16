import { NextRequest, NextResponse } from "next/server";

import { isAllowedApiPath, resolveBackendUrl } from "@/lib/proxy";

const ALLOWED_METHODS = new Set(["GET", "POST"]);
const MAX_REQUEST_BYTES = 32_768;

async function forward(request: NextRequest, pathParts: string[]): Promise<NextResponse> {
  const path = pathParts.join("/");
  if (!ALLOWED_METHODS.has(request.method) || !isAllowedApiPath(path)) {
    return NextResponse.json({ error: "Endpoint is not allowed." }, { status: 404 });
  }

  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BYTES) {
    return NextResponse.json({ error: "Request body is too large." }, { status: 413 });
  }

  const requestBody = request.method === "POST" ? await request.text() : undefined;
  if (requestBody && new TextEncoder().encode(requestBody).byteLength > MAX_REQUEST_BYTES) {
    return NextResponse.json({ error: "Request body is too large." }, { status: 413 });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60_000);
  try {
    const response = await fetch(resolveBackendUrl(path), {
      method: request.method,
      body: requestBody,
      headers: request.method === "POST" ? { "Content-Type": "application/json" } : undefined,
      cache: "no-store",
      signal: controller.signal,
    });
    const body = await response.text();
    return new NextResponse(body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
    });
  } catch (error) {
    const message = error instanceof Error && error.name === "AbortError"
      ? "The FinSight API timed out."
      : "The FinSight API is unavailable.";
    return NextResponse.json({ error: message }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return forward(request, (await context.params).path);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  return forward(request, (await context.params).path);
}
