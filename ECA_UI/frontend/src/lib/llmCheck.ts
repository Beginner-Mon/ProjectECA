/**
 * Browser-direct provider check — the health check runs HERE, not via the agent.
 *
 * The browser calls the provider straight (list models, then at most two probes)
 * so a bad key fails fast on the settings screen instead of mid-chat. The key
 * only ever travels to the provider's own `baseUrl` — never to models.dev,
 * never to ECA. This file must not import lib/api.ts or apiBase.ts: those pin
 * the ECA origin, and every request here goes elsewhere by design.
 */

import { catalogIdForBaseUrl, lookupModel, suggestModel } from './modelCatalog'
import type { LlmCapability, LlmOmit } from './llmProvider'

export interface CheckConn {
  baseUrl: string
  apiKey: string
  checkHeaders?: Record<string, string>
  catalogId?: string
}

export type CheckErrorKind =
  | 'auth'
  | 'quota'
  | 'model_not_found'
  | 'bad_request'
  | 'server'
  | 'network'
  | 'no_text'

export interface CheckError {
  kind: CheckErrorKind
  status?: number
}

export interface CheckResult {
  ok: boolean
  error?: CheckError
  models: string[]
  modelsListed: boolean
  suggested?: string
  capabilities: LlmCapability[]
  omit: LlmOmit[]
  /** The model's context window per the catalog; absent when the catalog does not say. */
  contextWindow?: number
  /**
   * Set when the models were listed but the trial message could not be sent:
   * this provider lets a web page read its model list and nothing more. Not a
   * verdict on the key — ECA's server makes the real calls and no such limit
   * applies to it. `capabilities` and `omit` then hold what the catalog says
   * about the model, since no trial could.
   */
  untested?: {
    /** The provider refuses a wrong key on the listing, and did not refuse this one. */
    keyAccepted: boolean
  }
}

const TIMEOUT_MS = 20_000

function stripBase(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '')
}

function authHeaders(apiKey: string, checkHeaders?: Record<string, string>): Record<string, string> {
  return {
    ...(checkHeaders ?? {}),
    ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
  }
}

function mapStatus(status: number): CheckErrorKind {
  if (status === 401 || status === 403) return 'auth'
  if (status === 402 || status === 429) return 'quota'
  if (status === 404) return 'model_not_found'
  if (status === 400 || status === 422 || status === 405) return 'bad_request'
  if (status >= 500) return 'server'
  return status >= 400 && status < 500 ? 'bad_request' : 'server'
}

async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

function toNetworkError(): CheckError {
  return { kind: 'network' }
}

/** GET {base}/models — which ids may this key call. 404/405 means "unlisted". */
export async function listModels(conn: CheckConn): Promise<{ models: string[]; listed: boolean }> {
  const url = `${stripBase(conn.baseUrl)}/models`
  let resp: Response
  try {
    resp = await fetchWithTimeout(url, { method: 'GET', headers: authHeaders(conn.apiKey, conn.checkHeaders) })
  } catch {
    throw toNetworkError()
  }
  if (resp.ok) {
    try {
      const json = (await resp.json()) as { data?: Array<{ id?: unknown }> }
      const data = Array.isArray(json?.data) ? json.data : []
      const ids = data
        .map((d) => (typeof d?.id === 'string' ? (d.id as string) : ''))
        .filter((id) => id !== '')
      return { models: ids, listed: true }
    } catch {
      return { models: [], listed: true }
    }
  }
  if (resp.status === 404 || resp.status === 405) return { models: [], listed: false }
  if (resp.status === 401 || resp.status === 403) throw { kind: 'auth', status: resp.status } satisfies CheckError
  // Anything else (quota, server, …) is not a verdict on the key — the ping
  // below says that. Treat as "unlisted" and carry on.
  return { models: [], listed: false }
}

/** Not a key any provider issued. Sent once, to see whether a key is looked at. */
const WRONG_KEY = 'eca-key-check-not-a-real-key'

/**
 * Did the listing look at the key at all? Some providers list their models to
 * anyone, so a list that came back proves nothing by itself. Ask once more
 * with a key that cannot be right: if THAT is refused, the provider checks
 * keys on this address — and it did not refuse the user's.
 */
async function listingChecksKey(conn: CheckConn): Promise<boolean> {
  try {
    await listModels({ ...conn, apiKey: WRONG_KEY })
    return false
  } catch (e) {
    return (e as CheckError)?.kind === 'auth'
  }
}

// ── Ping stream reading ──────────────────────────────────────────────

function contentFromPayload(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return null
  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) return null
  const first = choices[0] as Record<string, unknown>
  const delta = first.delta as Record<string, unknown> | undefined
  if (delta && typeof delta.content === 'string') return delta.content as string
  const message = first.message as Record<string, unknown> | undefined
  if (message && typeof message.content === 'string') return message.content as string
  return null
}

/** One SSE line → text, '[DONE]', or null (heartbeat / unparsable). */
function processSseLine(line: string): string | null {
  const trimmed = line.trim()
  if (!trimmed || trimmed.startsWith(':')) return null
  if (!trimmed.startsWith('data:')) return null
  const payload = trimmed.slice(5).trim()
  if (payload === '[DONE]') return '[DONE]'
  try {
    return contentFromPayload(JSON.parse(payload))
  } catch {
    return null
  }
}

function extractFromSseText(text: string): string {
  let out = ''
  for (const line of text.split('\n')) {
    const t = processSseLine(line)
    if (t && t !== '[DONE]') out += t
  }
  return out
}

async function readPingText(resp: Response): Promise<string> {
  const contentType = resp.headers.get('content-type') ?? ''
  if (!contentType.includes('text/event-stream') && !contentType.includes('event-stream')) {
    // Some providers ignore stream:true and answer JSON.
    try {
      const json: unknown = await resp.json()
      const single =
        (json as { choices?: Array<{ message?: { content?: unknown }; delta?: { content?: unknown } }> })
          .choices?.[0]
      const c = single?.message?.content ?? single?.delta?.content
      if (typeof c === 'string') return c
    } catch {
      // fall through to SSE-text parse below
    }
    try {
      return extractFromSseText(await resp.text())
    } catch {
      return ''
    }
  }
  const reader = resp.body?.getReader?.() ?? null
  if (!reader) {
    try {
      return extractFromSseText(await resp.text())
    } catch {
      return ''
    }
  }
  const decoder = new TextDecoder()
  let buffer = ''
  let out = ''
  let done = false
  while (!done) {
    let read: ReadableStreamReadResult<Uint8Array>
    try {
      read = await reader.read()
    } catch {
      break
    }
    if (read.value) buffer += decoder.decode(read.value, { stream: true })
    done = !!read.done
    let nl = buffer.indexOf('\n')
    while (nl !== -1) {
      const line = buffer.slice(0, nl)
      buffer = buffer.slice(nl + 1)
      const t = processSseLine(line)
      if (t === '[DONE]') {
        done = true
        break
      }
      if (t) out += t
      nl = buffer.indexOf('\n')
    }
  }
  const tail = processSseLine(buffer)
  if (tail && tail !== '[DONE]') out += tail
  try {
    reader.releaseLock()
  } catch {
    // ignore
  }
  return out
}

// ── The two probes ───────────────────────────────────────────────────

const PING_SYSTEM_FIRST = 'You are a helpful assistant.'
const PING_USER = 'Reply with the single word: pong'
const PING_SYSTEM_LAST = 'Answer in one word.'

interface PingVariant {
  includeTemperature: boolean
  includeMaxTokens: boolean
  includeFinalSystem: boolean
}

async function pingOnce(
  conn: CheckConn,
  model: string,
  variant: PingVariant,
): Promise<{ ok: true; text: string } | { ok: false; error: CheckError }> {
  const messages: Array<{ role: string; content: string }> = [
    { role: 'system', content: PING_SYSTEM_FIRST },
    { role: 'user', content: PING_USER },
  ]
  if (variant.includeFinalSystem) messages.push({ role: 'system', content: PING_SYSTEM_LAST })
  const body: Record<string, unknown> = {
    model,
    stream: true,
    messages,
    ...(variant.includeTemperature ? { temperature: 0.7 } : {}),
    ...(variant.includeMaxTokens ? { max_completion_tokens: 1024 } : {}),
  }
  let resp: Response
  try {
    resp = await fetchWithTimeout(`${stripBase(conn.baseUrl)}/chat/completions`, {
      method: 'POST',
      headers: { ...authHeaders(conn.apiKey, conn.checkHeaders), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    return { ok: false, error: toNetworkError() }
  }
  if (!resp.ok) {
    const kind = mapStatus(resp.status)
    return { ok: false, error: { kind, status: resp.status } }
  }
  let text: string
  try {
    text = await readPingText(resp)
  } catch {
    return { ok: false, error: toNetworkError() }
  }
  if (!text) return { ok: false, error: { kind: 'no_text', status: 200 } }
  return { ok: true, text }
}

async function probeTools(conn: CheckConn, model: string): Promise<boolean> {
  const body = {
    model,
    messages: [{ role: 'user', content: 'What is the current time? Call get_time to find out.' }],
    tools: [
      {
        type: 'function',
        function: {
          name: 'get_time',
          description: 'Get the current time.',
          parameters: { type: 'object', properties: {}, required: [] },
        },
      },
    ],
  }
  let resp: Response
  try {
    resp = await fetchWithTimeout(`${stripBase(conn.baseUrl)}/chat/completions`, {
      method: 'POST',
      headers: { ...authHeaders(conn.apiKey, conn.checkHeaders), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    return false
  }
  if (!resp.ok) return false
  try {
    const json: unknown = await resp.json()
    const choice = (json as { choices?: Array<Record<string, unknown>> }).choices?.[0]
    if (!choice || typeof choice !== 'object') return false
    const message = (choice as Record<string, unknown>).message as Record<string, unknown> | undefined
    const delta = (choice as Record<string, unknown>).delta as Record<string, unknown> | undefined
    const calls = (message?.tool_calls ?? delta?.tool_calls) as unknown
    return Array.isArray(calls) && calls.length > 0
  } catch {
    return false
  }
}

// ── Main entry ───────────────────────────────────────────────────────

/**
 * Metadata first: only what the catalog cannot answer gets a live probe.
 * A model the catalog knows costs exactly one chat/completions call (ping);
 * an unknown one costs two (ping + tools); listing alone costs none.
 */
export async function checkConnection(
  conn: CheckConn,
  model?: string,
  catalog?: unknown | null,
): Promise<CheckResult> {
  let models: string[]
  let listed: boolean
  try {
    const list = await listModels(conn)
    models = list.models
    listed = list.listed
  } catch (e) {
    const error = e as CheckError
    if (error?.kind === 'auth' || error?.kind === 'network') {
      return { ok: false, error, models: [], modelsListed: false, capabilities: [], omit: [] }
    }
    // Unreachable: listModels only throws auth/network. Kept so a future
    // thrower cannot turn into an unhandled rejection here.
    return { ok: false, error: { kind: 'network' }, models: [], modelsListed: false, capabilities: [], omit: [] }
  }

  // A preset names its catalog provider. A typed endpoint does not, so it is
  // recognised by its address — otherwise a model id is matched across every
  // provider and may be described the way some other provider serves it.
  const catalogId = conn.catalogId ?? catalogIdForBaseUrl(catalog ?? null, conn.baseUrl)

  if (!model) {
    return {
      ok: true,
      models,
      modelsListed: listed,
      suggested: suggestModel(catalog ?? null, models, catalogId),
      capabilities: [],
      omit: [],
    }
  }

  const meta = lookupModel(catalog ?? null, model, catalogId)

  const variants: PingVariant[] = [
    { includeTemperature: meta?.temperature !== false, includeMaxTokens: true, includeFinalSystem: true },
    { includeTemperature: false, includeMaxTokens: true, includeFinalSystem: true },
    { includeTemperature: false, includeMaxTokens: false, includeFinalSystem: true },
    { includeTemperature: false, includeMaxTokens: false, includeFinalSystem: false },
  ]
  const seen = new Set<string>()
  let won: PingVariant | null = null
  let lastError: CheckError = { kind: 'bad_request' }
  for (const variant of variants) {
    const key = `${variant.includeTemperature}-${variant.includeMaxTokens}-${variant.includeFinalSystem}`
    if (seen.has(key)) continue
    seen.add(key)
    const res = await pingOnce(conn, model, variant)
    if (res.ok) {
      won = variant
      break
    }
    lastError = res.error
    if (res.error.kind === 'network' && listed) {
      // The listing came back, so the address is right and the network is up:
      // it is the trial message this provider will not take from a browser.
      // Report what can be known without one — whether the key was looked at,
      // and what the catalog says the model can do (unknown: assume it can
      // call tools, as before; the first chat turn settles it).
      const capabilities: LlmCapability[] = ['text', 'answer']
      if (meta ? meta.tools : true) capabilities.push('tools')
      if (meta?.image) capabilities.push('image')
      return {
        ok: false,
        error: res.error,
        models,
        modelsListed: listed,
        capabilities,
        omit: meta?.temperature === false ? ['temperature'] : [],
        ...(meta?.contextWindow !== undefined ? { contextWindow: meta.contextWindow } : {}),
        untested: { keyAccepted: await listingChecksKey(conn) },
      }
    }
    if (res.error.kind !== 'bad_request') {
      return { ok: false, error: res.error, models, modelsListed: listed, capabilities: [], omit: [] }
    }
  }
  if (!won) {
    return { ok: false, error: lastError, models, modelsListed: listed, capabilities: [], omit: [] }
  }

  const omit: LlmOmit[] = []
  if (!won.includeTemperature) omit.push('temperature')
  if (!won.includeMaxTokens) omit.push('max_tokens')

  const capabilities: LlmCapability[] = ['text']
  if (won.includeFinalSystem) capabilities.push('answer')

  if (meta) {
    if (meta.tools) capabilities.push('tools')
    if (meta.image) capabilities.push('image')
  } else {
    const hasTools = await probeTools(conn, model)
    if (hasTools) capabilities.push('tools')
  }

  return {
    ok: true,
    models,
    modelsListed: listed,
    capabilities,
    omit,
    ...(meta?.contextWindow !== undefined ? { contextWindow: meta.contextWindow } : {}),
  }
}
