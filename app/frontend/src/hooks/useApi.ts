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
  const status = `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ''}`;
  let raw = '';
  try {
    raw = await res.text();
  } catch {
    return status;
  }
  try {
    const data = JSON.parse(raw);
    const msg = data?.error || data?.detail;
    if (typeof msg === 'string' && msg) return msg;
  } catch {
    // Not JSON. The Databricks Apps proxy answers timeouts with an HTML or
    // plain-text page, which never reaches the app's own logs.
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return `${status}: the Databricks Apps proxy did not get a reply from the app in time.`;
  }
  const text = raw.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  return text && text.length <= 200 ? `${status}: ${text}` : status;
}

/** True when the browser dropped the connection (no HTTP response to read).
 * Each browser words this differently. */
export function isNetworkError(e: unknown): boolean {
  if (!(e instanceof TypeError)) return false;
  return /network|failed to fetch|load failed|fetch/i.test(e.message);
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

/** Read an SSE response and yield each parsed JSON `data:` object. Stops on
 * `data: [DONE]`. Comment lines (`: keepalive`) are skipped. */
async function* readSseEvents<T>(res: Response): AsyncGenerator<T, void, unknown> {
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
  yield* readSseEvents<T>(res);
}

/** GET an SSE endpoint and yield each parsed JSON `data:` object. */
export async function* streamGetEvents<T = unknown>(
  path: string,
  signal?: AbortSignal
): AsyncGenerator<T, void, unknown> {
  const res = await fetch(`${API_BASE}${path}`, { signal, headers: { Accept: 'text/event-stream' } });
  yield* readSseEvents<T>(res);
}

/** Best-effort DELETE (used to cancel a background run). Errors are ignored. */
export async function apiDeleteQuiet(path: string): Promise<void> {
  try {
    await fetch(`${API_BASE}${path}`, { method: 'DELETE', keepalive: true });
  } catch {
    // The run expires on the server anyway.
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
