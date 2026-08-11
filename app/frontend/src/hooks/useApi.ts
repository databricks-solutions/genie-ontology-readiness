// Lightweight fetch helpers and an SSE stream parser for the Genie Ontology
// Readiness API. The selected AI model id is sent as the X-AI-Model header on
// AI calls (plan + chat).

const API_BASE = '/api';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data?.error || data?.detail || res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new ApiError(await parseError(res), res.status);
  return res.json() as Promise<T>;
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  model?: string
): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (model) headers['X-AI-Model'] = model;
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(await parseError(res), res.status);
  return res.json() as Promise<T>;
}

/**
 * POST to an SSE endpoint and yield each parsed JSON `data:` object as it
 * streams in. Terminates on `data: [DONE]`. Generic over the event shape.
 */
export async function* streamPostEvents<T = unknown>(
  path: string,
  body: unknown,
  model?: string,
  signal?: AbortSignal
): AsyncGenerator<T, void, unknown> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (model) headers['X-AI-Model'] = model;

  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) throw new ApiError(await parseError(res), res.status);
  if (!res.body) throw new ApiError('No response body', 500);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE events are separated by newlines; process complete lines.
    let newlineIndex: number;
    while ((newlineIndex = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (!line.startsWith('data:')) continue;

      const data = line.slice(5).trim();
      if (data === '[DONE]') return;
      if (!data) continue;

      try {
        yield JSON.parse(data) as T;
      } catch {
        // Ignore non-JSON keepalive lines.
      }
    }
  }
}

/**
 * POST to an SSE endpoint and yield text content chunks (`data: {"content":...}`).
 * Built on streamPostEvents; used by the chat + plan endpoints.
 */
export async function* streamPost(
  path: string,
  body: unknown,
  model?: string,
  signal?: AbortSignal
): AsyncGenerator<string, void, unknown> {
  for await (const ev of streamPostEvents<{ content?: string }>(path, body, model, signal)) {
    if (typeof ev.content === 'string' && ev.content.length > 0) {
      yield ev.content;
    }
  }
}
