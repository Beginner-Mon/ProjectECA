/**
 * Model metadata from the open catalog at https://models.dev/api.json.
 *
 * One API key carries no metadata: GET /models answers "which models may this
 * key call" with bare ids, never whether a model takes images or calls tools.
 * The catalog supplies that — fetched WITHOUT the key and without any user
 * data, once per page load, only when the Providers screen opens.
 */

export interface ModelMeta {
  textOutput: boolean
  image: boolean
  tools: boolean
  /** False when the model rejects the temperature param (e.g. some Claude tiers). */
  temperature: boolean
  cost?: number
  releaseDate?: string
}

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
  return {
    textOutput: output.includes('text'),
    image: input.includes('image'),
    tools: entry.tool_call === true,
    temperature: entry.temperature !== false,
    ...(costInput !== undefined ? { cost: costInput } : {}),
    ...(releaseDate !== undefined ? { releaseDate } : {}),
  }
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

/**
 * Default pick: among ids known to take text and call tools, the cheapest of
 * the 5 newest by release date (priceless sorts last). Unknown ids never win —
 * when none resolve, the first listed id is returned so the user still gets a
 * starting value to probe.
 */
export function suggestModel(
  catalog: unknown | null,
  modelIds: string[],
  catalogId?: string,
): string | undefined {
  if (modelIds.length === 0) return undefined
  const known = modelIds
    .map((id) => ({ id, meta: lookupModel(catalog, id, catalogId) }))
    .filter((x): x is { id: string; meta: ModelMeta } => x.meta !== null && x.meta.textOutput && x.meta.tools)
  if (known.length === 0) return modelIds[0]
  const newest = [...known].sort((a, b) =>
    (b.meta.releaseDate ?? '').localeCompare(a.meta.releaseDate ?? ''),
  ).slice(0, 5)
  newest.sort((a, b) => (a.meta.cost ?? Number.POSITIVE_INFINITY) - (b.meta.cost ?? Number.POSITIVE_INFINITY))
  return newest[0].id
}
