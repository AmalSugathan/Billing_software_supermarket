import { z } from 'zod';

export const sessionSchema = z.object({
  user: z.object({ id: z.string(), email: z.string(), display_name: z.string() }),
  csrf_token: z.string().min(1),
});
export const businessSchema = z.object({
  id: z.string(), name: z.string(), currency: z.string(), timezone: z.string(),
  role: z.string(), capabilities: z.array(z.string()), all_stores: z.boolean(),
});
export const storeSchema = z.object({ id: z.string(), name: z.string(), address: z.string() });
export const terminalSchema = z.object({ id: z.string(), store_id: z.string(), name: z.string() });
export const memberSchema = z.object({ id: z.string(), email: z.string(), display_name: z.string(),
  role: z.string(), all_stores: z.boolean(), store_ids: z.array(z.string()) });
export const auditSchema = z.object({ id: z.string(), action: z.string(), source: z.string(),
  actor_user_id: z.string(), resource_id: z.string(), created_at: z.string(),
  before_value: z.record(z.string(), z.unknown()).nullable(),
  after_value: z.record(z.string(), z.unknown()) });
export type Session = z.infer<typeof sessionSchema>;
export type Business = z.infer<typeof businessSchema>;
export type Store = z.infer<typeof storeSchema>;
export type Terminal = z.infer<typeof terminalSchema>;
export type Member = z.infer<typeof memberSchema>;
export type Audit = z.infer<typeof auditSchema>;

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export async function request<T>(path: string, schema: z.ZodType<T>,
  options: { method?: string; body?: unknown; csrf?: string; signal?: AbortSignal; idempotencyKey?: string; timeoutMs?: number } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/v1' + path, {
      method: options.method ?? 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(options.csrf ? { 'X-CSRF-Token': options.csrf } : {}),
        ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(Math.min(240000, Math.max(1000, options.timeoutMs ?? 8000)))]) : AbortSignal.timeout(Math.min(240000, Math.max(1000, options.timeoutMs ?? 8000))),
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new ApiError(0, 'Could not reach the store service. Please try again.');
  }
  if (response.status === 204 && response.ok) return schema.parse(undefined);
  let body: unknown;
  try { body = await response.json(); }
  catch { throw new ApiError(response.status, 'The service returned an unreadable response.'); }
  if (!response.ok) {
    const error = z.object({ detail: z.string() }).safeParse(body);
    throw new ApiError(response.status, error.success ? error.data.detail : 'Please check the entered details and try again.');
  }
  const result = schema.safeParse(body);
  if (!result.success) throw new ApiError(0, 'The service response could not be verified.');
  return result.data;
}

export const schemas = {
  session: sessionSchema, businesses: z.array(businessSchema), business: businessSchema,
  stores: z.array(storeSchema), store: storeSchema, terminals: z.array(terminalSchema),
  terminal: terminalSchema, members: z.array(memberSchema), member: memberSchema,
  audit: z.array(auditSchema), empty: z.undefined(),
};
