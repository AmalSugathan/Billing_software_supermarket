import { z } from 'zod';
import { ApiError } from './identity-api';
const evidence = z.object({ provider_model: z.string(), document_class: z.literal('unclassified'), classification_confidence: z.null(), review_required: z.literal(true), pages: z.array(z.object({ page_number: z.number().int(), markdown: z.string(), blocks: z.array(z.object({ label: z.string(), text: z.string(), bbox: z.array(z.number()).nullable(), confidence: z.null(), confidence_method: z.literal('not_supplied'), requires_review: z.literal(true) })) })) });
const document = z.object({ id: z.string().uuid(), store_id: z.string().uuid(), filename: z.string(), mime_type: z.string(), data_origin: z.string(), sha256: z.string(), byte_size: z.number().int(), page_count: z.number().int(), created_at: z.string(), status: z.string(), attempt_id: z.string().nullable(), error_code: z.string().nullable(), evidence: evidence.nullable() });
const provider = z.object({ upload_enabled: z.boolean(), provider_configured: z.boolean(), provider_model: z.string(), health_verified: z.boolean(), semantic_matching_available: z.boolean(), message: z.string() });
const candidate = z.object({ product_id: z.string().uuid(), name: z.string(), sku: z.string(), unit: z.string(), match_score: z.number().int(), method: z.string(), confidence: z.null(), requires_review: z.literal(true) });
const proposedField = z.object({ value: z.string().nullable(), source: z.string(), confidence: z.null(), requires_review: z.literal(true) });
const draft = z.object({ document_id: z.string(), attempt_id: z.string(), source_sha256: z.string(), supplier_candidates: z.array(z.string()), fields: z.record(z.string(), proposedField), rows: z.array(z.object({ page: z.number().int(), block: z.number().int(), row: z.number().int(), headers: z.array(z.string()), cells: z.array(z.string()), fields: z.record(z.string(), proposedField) })), warnings: z.array(z.string()), line_total_sum: z.string().nullable(), posting_allowed: z.literal(false), review_required: z.literal(true) });
export type InvoiceDraft = z.infer<typeof draft>;
export type PurchaseSource = { draft: InvoiceDraft; storeId: string; filename: string };
export const ocr = { draft, document, documents: z.array(document), provider, candidates: z.array(candidate), mapping: z.object({ id: z.string().uuid() }) };
export type InvoiceDocument = z.infer<typeof document>;
export type OcrProvider = z.infer<typeof provider>;
export type OcrCandidate = z.infer<typeof candidate>;
const uploadRecovery = z.object({ fingerprint: z.string(), key: z.string().uuid() });
export async function uploadInvoice(route: string, file: File, origin: string, csrf: string, storageKey: string): Promise<InvoiceDocument> {
  if (!file.size || file.size > 10 * 1024 * 1024) throw new Error('Choose a non-empty invoice up to 10 MiB.');
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const fingerprint = JSON.stringify([route, file.name, file.type, origin, digest]);
  const raw = localStorage.getItem(storageKey);
  const previous = raw === null ? null : uploadRecovery.parse(JSON.parse(raw));
  if (previous && previous.fingerprint !== fingerprint) throw new Error('Reselect the previous invoice and origin to resolve its pending upload first.');
  const command = previous ?? { fingerprint, key: crypto.randomUUID() };
  localStorage.setItem(storageKey, JSON.stringify(command));
  if (localStorage.getItem(storageKey) !== JSON.stringify(command)) throw new Error('Upload recovery storage is unavailable.');
  let response: Response;
  try {
    response = await fetch('/api/v1' + route + '?filename=' + encodeURIComponent(file.name) + '&data_origin=' + encodeURIComponent(origin), {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(30000),
      headers: { 'Content-Type': file.type || 'application/octet-stream', 'X-CSRF-Token': csrf, 'Idempotency-Key': command.key }, body: file,
    });
  } catch { throw new ApiError(0, 'Upload status is uncertain. Reselect this same file to retry safely.'); }
  let body: unknown;
  try { body = await response.json(); } catch { throw new ApiError(0, 'Upload response is uncertain. Retry the same file.'); }
  if (!response.ok) {
    if (response.status >= 400 && response.status < 500) localStorage.removeItem(storageKey);
    const error = z.object({ detail: z.string() }).safeParse(body);
    throw new ApiError(response.status, error.success ? error.data.detail : 'Invoice upload failed. Retry the same file.');
  }
  const saved = document.parse(body);
  localStorage.removeItem(storageKey);
  return saved;
}
