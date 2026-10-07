export type ServiceState = 'checking' | 'available' | 'unavailable';
export type Health = { api: ServiceState; database: ServiceState };

async function check(path: string, expected: string, signal: AbortSignal): Promise<ServiceState> {
  try {
    const response = await fetch(path, { signal, cache: 'no-store' });
    const body: unknown = await response.json();
    return response.ok && typeof body === 'object' && body !== null &&
      'status' in body && body.status === expected ? 'available' : 'unavailable';
  } catch {
    return 'unavailable';
  }
}

export async function loadHealth(signal: AbortSignal): Promise<Health> {
  const [api, database] = await Promise.all([
    check('/api/v1/health/live', 'alive', signal),
    check('/api/v1/health/ready', 'ready', signal),
  ]);
  return { api, database };
}
