/**
 * Providers and model metadata from the open catalog at
 * https://models.dev/api.json.
 *
 * One API key carries no metadata: GET /models answers "which models may this
 * key call" with bare ids, never whether a model takes images or calls tools.
 * The catalog supplies that, and the list of providers itself — fetched
 * WITHOUT the key and without any user data, once per page load, only when the
 * Providers screen opens.
 */

export interface ModelMeta {
  textInput: boolean
  textOutput: boolean
  image: boolean
  tools: boolean
  /** False when the model rejects the temperature param (e.g. some Claude tiers). */
  temperature: boolean
  /**
   * False when this provider serves the model over a different API than chat
   * completions — the only one ECA speaks. A gateway such as OpenCode Zen
   * lists every model under one key, but serves Claude over the Anthropic
   * format and GPT over the Responses format; calling those on
   * /chat/completions fails every time.
   */
  chat: boolean
  cost?: number
  releaseDate?: string
}

/** The adapter name the catalog uses for "plain chat completions". */
const CHAT_ADAPTER = '@ai-sdk/openai-compatible'

export const MODELS_DEV_URL = 'https://models.dev/api.json'

let catalogPromise: Promise<unknown | null> | null = null

/** Fetch once per session. Never throws — a failure is null (unknown metadata). */
export function loadCatalog(): Promise<unknown | null> {
  if (!catalogPromise) {
    catalogPromise = fetch(MODELS_DEV_URL)
      .then((resp) => {
        if (!resp.ok) return null
        return resp.json() as Promise<unknown>
      })
      .catch(() => null)
  }
  return catalogPromise
}

/** Test seam: drop the cached promise so each test fetches fresh. */
export function resetCatalogCache(): void {
  catalogPromise = null
}

export function normalizeModelId(id: string): string {
  return id.replace(/^models\//, '')
}

interface CollectedEntry {
  providerId: string
  modelId: string
  entry: Record<string, unknown>
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function collectEntries(catalog: unknown): CollectedEntry[] {
  if (!isRecord(catalog)) return []
  const out: CollectedEntry[] = []
  for (const [providerId, provider] of Object.entries(catalog)) {
    if (!isRecord(provider)) continue
    const models = (provider as Record<string, unknown>).models
    if (isRecord(models)) {
      for (const [modelId, entry] of Object.entries(models)) {
        if (isRecord(entry)) out.push({ providerId, modelId, entry })
      }
    } else if (Array.isArray(models)) {
      for (const entry of models) {
        if (!isRecord(entry)) continue
        const id = typeof entry.id === 'string' ? entry.id : ''
        if (id) out.push({ providerId, modelId: id, entry })
      }
    } else if ('modalities' in provider || 'tool_call' in provider) {
      // Flat shape: top-level key is itself a model id.
      out.push({ providerId: '', modelId: providerId, entry: provider })
    }
  }
  return out
}

function toMeta(entry: Record<string, unknown>): ModelMeta | null {
  const modalities = isRecord(entry.modalities) ? (entry.modalities as Record<string, unknown>) : null
  if (!modalities) return null
  const input = Array.isArray(modalities.input) ? (modalities.input as unknown[]) : []
  const output = Array.isArray(modalities.output) ? (modalities.output as unknown[]) : []
  const cost = isRecord(entry.cost) ? (entry.cost as Record<string, unknown>) : null
  const costInput = typeof cost?.input === 'number' ? (cost.input as number) : undefined
  const releaseDate = typeof entry.release_date === 'string' ? (entry.release_date as string) : undefined
  // Only a per-MODEL adapter override says anything here: it means "this one
  // model is not served the way the rest of this provider is". A provider's
  // own default adapter is irrelevant — ECA calls each provider's
  // chat-completions endpoint whatever SDK the catalog would have used.
  const override = isRecord(entry.provider) ? (entry.provider as Record<string, unknown>).npm : undefined
  return {
    textInput: input.includes('text'),
    textOutput: output.includes('text'),
    image: input.includes('image'),
    tools: entry.tool_call === true,
    temperature: entry.temperature !== false,
    chat: typeof override !== 'string' || override === CHAT_ADAPTER,
    ...(costInput !== undefined ? { cost: costInput } : {}),
    ...(releaseDate !== undefined ? { releaseDate } : {}),
  }
}

/**
 * The floor for any model ECA talks to: it takes text and answers in text,
 * over chat completions. Taking images as well is fine; an image, audio or
 * video generator, or a model that only listens, is not a chat model.
 */
function canChat(meta: ModelMeta): boolean {
  return meta.chat && meta.textInput && meta.textOutput
}

/**
 * Look a model id up: the `catalogId` provider first, then an exact id match
 * across the whole catalog. Null when the catalog is missing or knows nothing
 * about the id — the caller then probes tools live instead of guessing.
 */
export function lookupModel(
  catalog: unknown | null,
  modelId: string,
  catalogId?: string,
): ModelMeta | null {
  if (!catalog) return null
  const want = normalizeModelId(modelId)
  const entries = collectEntries(catalog)
  if (catalogId) {
    const hit = entries.find(
      (e) => e.providerId === catalogId && normalizeModelId(e.modelId) === want,
    )
    if (hit) return toMeta(hit.entry)
    const hitByEntryId = entries.find((e) => {
      if (e.providerId !== catalogId) return false
      const entryId = typeof e.entry.id === 'string' ? normalizeModelId(e.entry.id as string) : ''
      return entryId !== '' && entryId === want
    })
    if (hitByEntryId) return toMeta(hitByEntryId.entry)
  }
  const global = entries.find((e) => normalizeModelId(e.modelId) === want)
  if (global) return toMeta(global.entry)
  const globalByEntryId = entries.find((e) => {
    const entryId = typeof e.entry.id === 'string' ? normalizeModelId(e.entry.id as string) : ''
    return entryId !== '' && entryId === want
  })
  if (globalByEntryId) return toMeta(globalByEntryId.entry)
  return null
}

function normalizeBaseUrl(url: string): string {
  return url.trim().toLowerCase().replace(/\/+$/, '')
}

/**
 * Which catalog provider an endpoint belongs to, by its address.
 *
 * A preset names its provider outright. A typed endpoint (the "OpenAI
 * Compatible" card) names nothing — but the catalog records each provider's
 * API address, so the address itself identifies it. Without this, a model id
 * is looked up across every provider at once, and the first match may describe
 * how some OTHER provider serves that model.
 */
export function catalogIdForBaseUrl(catalog: unknown | null, baseUrl: string): string | undefined {
  const want = normalizeBaseUrl(baseUrl)
  if (!want || !isRecord(catalog)) return undefined
  for (const [providerId, provider] of Object.entries(catalog)) {
    if (!isRecord(provider) || typeof provider.api !== 'string') continue
    if (normalizeBaseUrl(provider.api) === want) return providerId
  }
  return undefined
}

export interface CatalogProvider {
  id: string
  name: string
  baseUrl: string
}

/**
 * A flat-rate subscription says so in its name ("Alibaba Coding Plan", "Kimi
 * For Coding", "Alibaba Token Plan"). Its key is not a general API key: these
 * plans are sold for use inside coding tools. Alibaba's terms, for one, forbid
 * using the key from "application backends" and answer with a suspended
 * subscription — and a backend is what ECA is. The same company's pay-per-use
 * entry is listed under its plain name.
 * https://www.alibabacloud.com/help/en/model-studio/coding-plan
 */
const PLAN_NAME = /\b(coding|plan)\b/i

/**
 * The providers ECA can be pointed at exactly as the catalog describes them.
 *
 * Three rules, none of them a list of names:
 *  - the catalog says the provider speaks chat completions at one fixed https
 *    address (not its own SDK, not an address with a `${...}` blank to fill in,
 *    not a program on the user's own machine);
 *  - it is not a coding-tool subscription (see PLAN_NAME);
 *  - an address listed twice is shown once, under the entry with more models.
 *
 * The catalog has no field for "refuses callers it has not approved", so an
 * entry that does this without saying so in its name still appears. It fails
 * on its own: the provider turns the key away and the screen reports that.
 */
export function catalogProviders(catalog: unknown | null): CatalogProvider[] {
  if (!isRecord(catalog)) return []
  const byAddress = new Map<string, CatalogProvider & { models: number }>()
  for (const [id, provider] of Object.entries(catalog)) {
    if (!isRecord(provider)) continue
    const { name, api, npm } = provider
    if (npm !== CHAT_ADAPTER || typeof name !== 'string' || typeof api !== 'string') continue
    if (!api.startsWith('https://') || api.includes('${')) continue
    if (PLAN_NAME.test(name)) continue
    const models = isRecord(provider.models) ? Object.keys(provider.models).length : 0
    const key = normalizeBaseUrl(api)
    const seen = byAddress.get(key)
    if (!seen || models > seen.models) byAddress.set(key, { id, name, baseUrl: api, models })
  }
  return [...byAddress.values()]
    .map(({ id, name, baseUrl }) => ({ id, name, baseUrl }))
    .sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }))
}

export interface ModelGroups {
  /** Known to answer in text and call tools — newest first. */
  recommended: string[]
  /** Everything else that may work, in the provider's own order. */
  other: string[]
  /**
   * Known NOT to work (see canChat): the model does not take text and answer
   * in text, or this provider serves it over an API ECA does not speak. Kept
   * apart so the picker can leave them out and say how many there are.
   */
  unsupported: string[]
}

/**
 * Split a provider's model list for the picker.
 *
 * A provider lists every model the key may call — embedding, image and speech
 * models included — and nobody choosing a chat model can tell them apart by id.
 * The catalog can: what it knows to answer in text and call tools goes first,
 * newest first. What it knows cannot chat is set aside (see canChat); an id
 * the catalog does not know stays selectable in the second group.
 *
 * Indexes the catalog once rather than calling lookupModel per id: a long list
 * (several hundred ids) against several thousand entries is otherwise a
 * visible stall on the settings screen.
 */
export function groupModels(
  catalog: unknown | null,
  modelIds: string[],
  catalogId?: string,
): ModelGroups {
  const preferred = new Map<string, Record<string, unknown>>()
  const anywhere = new Map<string, Record<string, unknown>>()
  for (const e of collectEntries(catalog)) {
    const keys = [normalizeModelId(e.modelId)]
    if (typeof e.entry.id === 'string') keys.push(normalizeModelId(e.entry.id))
    for (const key of keys) {
      if (catalogId && e.providerId === catalogId && !preferred.has(key)) preferred.set(key, e.entry)
      if (!anywhere.has(key)) anywhere.set(key, e.entry)
    }
  }

  const recommended: { id: string; date: string }[] = []
  const other: string[] = []
  const unsupported: string[] = []
  for (const id of modelIds) {
    const key = normalizeModelId(id)
    const entry = preferred.get(key) ?? anywhere.get(key)
    const meta = entry ? toMeta(entry) : null
    if (meta && !canChat(meta)) unsupported.push(id)
    else if (meta && meta.tools) recommended.push({ id, date: meta.releaseDate ?? '' })
    else other.push(id)
  }
  recommended.sort((a, b) => b.date.localeCompare(a.date))
  return { recommended: recommended.map((r) => r.id), other, unsupported }
}

/**
 * Default pick: among ids known to speak chat completions, take text and call
 * tools, the cheapest of the 5 newest by release date (priceless sorts last).
 * Unknown ids never win. When none resolve, the first id not known to be
 * unusable is returned so the user still gets a starting value to probe.
 */
export function suggestModel(
  catalog: unknown | null,
  modelIds: string[],
  catalogId?: string,
): string | undefined {
  if (modelIds.length === 0) return undefined
  const looked = modelIds.map((id) => ({ id, meta: lookupModel(catalog, id, catalogId) }))
  const known = looked.filter(
    (x): x is { id: string; meta: ModelMeta } => x.meta !== null && canChat(x.meta) && x.meta.tools,
  )
  // Nothing recognised as a chat model: offer the first id that is not known
  // to be unusable, rather than hand over a certain failure as the default.
  if (known.length === 0) return looked.find((x) => x.meta === null || canChat(x.meta))?.id
  const newest = [...known].sort((a, b) =>
    (b.meta.releaseDate ?? '').localeCompare(a.meta.releaseDate ?? ''),
  ).slice(0, 5)
  newest.sort((a, b) => (a.meta.cost ?? Number.POSITIVE_INFINITY) - (b.meta.cost ?? Number.POSITIVE_INFINITY))
  return newest[0].id
}
