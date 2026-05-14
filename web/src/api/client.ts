import type { paths } from './schema';

type JsonResponse<
  TPath extends keyof paths,
  TMethod extends keyof paths[TPath],
> = paths[TPath][TMethod] extends {
  responses: { 200: { content: { 'application/json': infer TResponse } } };
}
  ? TResponse
  : never;

type JsonRequest<
  TPath extends keyof paths,
  TMethod extends keyof paths[TPath],
> = paths[TPath][TMethod] extends {
  requestBody: { content: { 'application/json': infer TBody } };
}
  ? TBody
  : never;

export type PacketList = JsonResponse<'/review/packets', 'get'>;
export type PacketListItem = PacketList[number];
export type PacketDetail = JsonResponse<'/review/packets/{packet_id}', 'get'>;
export type PacketLine = NonNullable<PacketDetail['lines']>[number];
export type CountyBody = JsonRequest<'/review/packets/{packet_id}/county', 'post'>;
export type VoterRollBody = JsonRequest<'/review/packets/{packet_id}/voter-roll', 'post'>;
type OkResponse = JsonResponse<'/review/packets/{packet_id}/lines/{line_no}/action', 'post'>;
type CountyResponse = JsonResponse<'/review/packets/{packet_id}/county', 'post'>;
type ApproveAllResponse = JsonResponse<'/review/packets/{packet_id}/approve-all', 'post'>;
type VoterRollResponse = JsonResponse<'/review/packets/{packet_id}/voter-roll', 'post'>;
type VoterMatchResponse = JsonResponse<'/review/packets/{packet_id}/voter-match', 'post'>;
type FraudAnalysisResponse = JsonResponse<'/review/packets/{packet_id}/fraud-analysis', 'post'>;

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

function token(): string {
  return window.localStorage.getItem('pv_token') ?? '';
}

function cookie(name: string): string {
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`))
    ?.split('=')
    .slice(1)
    .join('=') ?? '';
}

function jsonHeaders(): HeadersInit {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  const bearer = token();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  const csrf = cookie('pv_csrf');
  if (csrf) headers['X-CSRF-Token'] = decodeURIComponent(csrf);
  return headers;
}

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(jsonHeaders());
  new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

async function apiBlob(path: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: jsonHeaders(),
    credentials: 'same-origin',
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.blob();
}

export const api = {
  hasSession: () => Boolean(token() || cookie('pv_csrf')),
  listPackets: () => apiJson<PacketList>('/review/packets'),
  getPacket: (packetId: number) => apiJson<PacketDetail>(`/review/packets/${packetId}`),
  counties: () => apiJson<string[]>('/review/counties'),
  setCounty: (packetId: number, body: CountyBody) =>
    apiJson<CountyResponse>(`/review/packets/${packetId}/county`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  setLineAction: (packetId: number, lineNo: number, action: 'approved' | 'rejected' | 'escalated') =>
    apiJson<OkResponse>(`/review/packets/${packetId}/lines/${lineNo}/action`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  approveAll: (packetId: number) =>
    apiJson<ApproveAllResponse>(`/review/packets/${packetId}/approve-all`, { method: 'POST' }),
  saveVoterRoll: (packetId: number, body: VoterRollBody) =>
    apiJson<VoterRollResponse>(`/review/packets/${packetId}/voter-roll`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  runVoterMatch: (packetId: number) =>
    apiJson<VoterMatchResponse>(`/review/packets/${packetId}/voter-match`, { method: 'POST' }),
  runFraudAnalysis: (packetId: number) =>
    apiJson<FraudAnalysisResponse>(`/review/packets/${packetId}/fraud-analysis`, { method: 'POST' }),
  setDecision: (packetId: number, lineNo: number, decision: 'confirmed_fraud' | 'cleared') =>
    apiJson<OkResponse>(`/review/packets/${packetId}/lines/${lineNo}/decision`, {
      method: 'PATCH',
      body: JSON.stringify({ decision }),
    }),
  imageBlob: (packetId: number, imageType: 'cleaned' | 'raw') =>
    apiBlob(`/review/packets/${packetId}/image?type=${imageType}`),
  exportBlob: (packetId: number, filter: 'all' | 'valid' | 'flagged') =>
    apiBlob(`/review/packets/${packetId}/export?filter=${filter}`),
};
