import type { components } from "./openapi";

export type Project = {
  id: string;
  pdf_path: string;
  county: string;
  created_at: string | null;
  total_lines: number;
  approved: number;
  review: number;
  rejected: number;
  duplicates: number;
};

export type SignatureList = {
  total: number;
  page: number;
  page_size: number;
  items: SignatureRow[];
};

export type SignatureRow = {
  line_number: number;
  page: number;
  raw_name: string;
  raw_address: string;
  raw_date: string;
  signature_present: boolean;
  first_name: string;
  last_name: string;
  street: string;
  city: string;
  state: string;
  zip_code: string;
  voter_id: string | null;
  voter_name: string | null;
  voter_address: string | null;
  match_confidence: number | null;
  name_score: number | null;
  address_score: number | null;
  status: string;
  auto_status: string;
  duplicate_of_line: number | null;
  staff_notes: string;
};

export type ReviewPayload = components["schemas"]["ReviewPayload"];

const AUTH_METADATA_KEYS = ["pv_role", "pv_user_id", "pv_full_name", "pv_name"];
const LEGACY_TOKEN_KEY = ["pv", "token"].join("_");

function clearAuthMetadata(): void {
  [...AUTH_METADATA_KEYS, LEGACY_TOKEN_KEY].forEach((key) => localStorage.removeItem(key));
}

async function refreshSession(): Promise<boolean> {
  const response = await fetch("/auth/refresh", {
    method: "POST",
    credentials: "same-origin",
  }).catch(() => undefined);
  return !!response?.ok;
}

async function logout(): Promise<void> {
  await fetch("/auth/logout", {
    method: "POST",
    credentials: "same-origin",
  }).catch(() => undefined);
  clearAuthMetadata();
  window.location.href = "/static/login.html";
}

async function request<T>(path: string, init?: RequestInit, didRefresh = false): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });

  if (response.status === 401 && !didRefresh) {
    if (await refreshSession()) {
      return request<T>(path, init, true);
    }
    await logout();
    throw new Error("Unauthorized");
  }
  if (response.status === 401) {
    await logout();
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    const detail = await response.json().catch(() => undefined);
    throw new Error(detail?.detail ?? response.statusText);
  }

  return response.json() as Promise<T>;
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
}

export function listSignatures(params: {
  projectId: string;
  status?: string;
  page: number;
  pageSize: number;
}): Promise<SignatureList> {
  const search = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.status && params.status !== "all") search.set("status", params.status);
  return request<SignatureList>(`/projects/${params.projectId}/signatures?${search.toString()}`);
}

export function reviewSignature(args: {
  projectId: string;
  lineNumber: number;
  payload: ReviewPayload;
}): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(
    `/projects/${args.projectId}/signatures/${args.lineNumber}/review`,
    {
      method: "POST",
      body: JSON.stringify(args.payload),
    },
  );
}

export function exportProjectCsv(projectId: string): void {
  window.location.href = `/projects/${projectId}/export`;
}
