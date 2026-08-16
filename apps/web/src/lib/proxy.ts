const UUID_PATTERN =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const ALLOWED_PATHS = [
  /^health$/,
  /^v1\/investigations\/runs$/,
  new RegExp(`^v1/investigations/runs/${UUID_PATTERN}$`),
  new RegExp(`^v1/investigations/runs/${UUID_PATTERN}/review$`),
  new RegExp(`^v1/investigations/runs/${UUID_PATTERN}/feedback$`),
];

export function isAllowedApiPath(path: string): boolean {
  return ALLOWED_PATHS.some((pattern) => pattern.test(path));
}

export function resolveBackendUrl(path: string): URL {
  if (!isAllowedApiPath(path)) {
    throw new Error("API path is not allowlisted");
  }
  const baseUrl = process.env.FINSIGHT_API_BASE_URL ?? "http://127.0.0.1:8000";
  const base = new URL(baseUrl);
  if (!["http:", "https:"].includes(base.protocol)) {
    throw new Error("FINSIGHT_API_BASE_URL must use HTTP or HTTPS");
  }
  if (base.username || base.password || base.search || base.hash) {
    throw new Error("FINSIGHT_API_BASE_URL cannot contain credentials, a query, or a fragment");
  }
  return new URL(path, `${base.toString().replace(/\/$/, "")}/`);
}
