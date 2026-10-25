import * as SecureStore from 'expo-secure-store';
import * as Crypto from 'expo-crypto';

export type CivicReport = { id: string; category: string; description: string; location_text: string; latitude: number | null; longitude: number | null; location_confirmed: boolean; public: boolean; status: string; status_label: string; created_at: string; updated_at: string; support_count: number; supported: boolean; photo_url: string | null; is_owner: boolean; history: { status: string; created_at: string }[] };
export type CommunityPost = { id: string; text: string; location_text: string; public: boolean; created_at: string; photo_url: string | null; report: CivicReport | null };
export type Page<T> = { items: T[]; next_offset: number | null };
export type SelectedPhoto = { uri: string; name: string; type: string };
export type ReportDraft = { category?: string; description?: string; location_text?: string };
export type ResolvedLocation = { status: string; place: { id: string; name: string; address: string; location: { latitude: number; longitude: number }; attributions?: { provider: string; provider_uri?: string }[] } | null; candidates?: { id: string; name: string; address: string }[] };

let tokenPromise: Promise<string> | null = null;
export function installationToken(): Promise<string> {
  if (!tokenPromise) tokenPromise = (async () => {
    const saved = await SecureStore.getItemAsync('smartnagrik.installation.v1');
    if (saved && /^[A-Za-z0-9_-]{32,128}$/.test(saved)) return saved;
    const value = Crypto.randomUUID().replace(/-/g, '') + Crypto.randomUUID().replace(/-/g, '');
    await SecureStore.setItemAsync('smartnagrik.installation.v1', value);
    return value;
  })().catch((error) => { tokenPromise = null; throw error; });
  return tokenPromise;
}
export async function identityHeaders() { return { Authorization: `Bearer ${await installationToken()}` }; }
export function apiBase() {
  const base = process.env.EXPO_PUBLIC_API_URL?.trim().replace(/\/$/, '');
  if (!base) throw Error('The backend address is not configured.');
  return base;
}

export async function civicRequest<T>(path: string, payload?: unknown, photo?: SelectedPhoto | null): Promise<T> {
  const headers: Record<string, string> = await identityHeaders();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    let body: string | FormData | undefined;
    if (photo) {
      const data = new FormData();
      data.append('payload', JSON.stringify(payload));
      data.append('photo', photo as unknown as Blob);
      body = data;
    } else if (payload !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(payload); }
    const response = await fetch(apiBase() + path, { method: payload === undefined ? 'GET' : 'POST', headers, body, signal: controller.signal });
    const envelope = await response.json();
    if (!response.ok || !envelope.success || !envelope.data) throw Error(envelope.error?.message || 'The request could not be saved. Please try again.');
    return envelope.data as T;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw Error('The request timed out. Retry with the same form to safely check the save.');
    throw error;
  } finally { clearTimeout(timer); }
}

// Store only retry metadata in protected storage, never form text or photos.
export async function submissionKey(kind: 'report' | 'post', payload: unknown): Promise<string> {
  const digest = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, JSON.stringify(payload));
  const key = `smartnagrik.pending.${kind}`;
  const raw = await SecureStore.getItemAsync(key);
  if (raw) { try { const saved = JSON.parse(raw); if (saved.digest === digest && typeof saved.id === 'string') return saved.id; } catch { /* replace corrupt retry metadata */ } }
  const id = Crypto.randomUUID();
  await SecureStore.setItemAsync(key, JSON.stringify({ digest, id }));
  return id;
}
export async function clearSubmission(kind: 'report' | 'post') { await SecureStore.deleteItemAsync(`smartnagrik.pending.${kind}`); }

export function mediaPath(value: unknown): string | null {
  return typeof value === 'string' && /^\/v1\/civic\/media\/[A-Za-z0-9_-]+$/.test(value) ? value : null;
}
export async function mediaSource(path: string) {
  if (!mediaPath(path)) throw Error('Invalid media reference.');
  return { uri: apiBase() + path, headers: await identityHeaders() };
}

const listeners = new Set<(report: CivicReport) => void>();
export function onReportChange(listener: (report: CivicReport) => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export function publishReport(report: CivicReport) { listeners.forEach((listener) => listener(report)); }
export async function setSupport(id: string, supported: boolean) {
  const report = await civicRequest<CivicReport>(`/v1/civic/reports/${encodeURIComponent(id)}/support`, { supported });
  publishReport(report); return report;
}
export function publicShare(report: CivicReport): string | null {
  return report.public ? `Community-reported ${report.category.replace(/_/g, ' ')}: ${report.description}\n${report.location_text}\nSmart Nagrik reference ${report.id}. ${report.status_label}. Community information, not official verification.` : null;
}
