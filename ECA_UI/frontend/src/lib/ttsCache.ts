/**
 * Per-user IndexedDB cache of synthesised speech, so replaying a message does
 * not synthesise it again.
 *
 * IndexedDB rather than localStorage: localStorage holds strings only, so audio
 * would have to go in as base64 (a third bigger), and its ~5 MB cap would fit a
 * few answers at best. IndexedDB stores the ArrayBuffers as they are and its
 * quota is a share of the disk.
 *
 * ── Isolation ────────────────────────────────────────────────────────────────
 * Every row is stamped with the Cognito `sub` of the user it was made for — not
 * the email, which can change hands; a sub cannot. Three things keep one
 * account from hearing another's audio on a shared machine:
 *
 *   1. reads and writes are addressed by sub, so a lookup for B never returns
 *      A's rows;
 *   2. sign-out clears everything, awaited, BEFORE Amplify is told (AuthGuard);
 *   3. whenever the guard learns who is signed in, every row that is not
 *      theirs is deleted (`sweepTtsCache`).
 *
 * 3 is the one that matters. 2 can be cut off — Amplify's signOut may navigate
 * away mid-call, a tab can simply be closed — and isolation must not depend on
 * the previous user's sign-out having finished.
 *
 * Demo mode (no Cognito session) has no sub and does not cache at all. The
 * per-browser demo UUID is not an identity, and caching under it would recreate
 * the shared namespace that 3 exists to prevent.
 *
 * ── Failure ──────────────────────────────────────────────────────────────────
 * Every exported function resolves; none rejects, and none can hang (each is
 * time-boxed). Private browsing, a full disk, an old WebView without
 * IndexedDB, an insecure origin without SubtleCrypto — each degrades to "no
 * cache", which costs one synthesis. Never a broken speaker button, and never a
 * sign-out stuck waiting on storage.
 */

/** How long a clip may be replayed from this browser. ONE place to change it. */
export const TTS_CACHE_TTL_MS = 24 * 60 * 60 * 1000

/**
 * Most rows kept; the oldest go first.
 *
 * Rows hold whatever codec the server sent, unexamined. Opus speech is a few kB
 * per second, so a long answer is a few hundred kB and 100 rows stays in the
 * tens of MB. Lossless (FLAC, WAV) is roughly 10-30x that per row; then the
 * browser quota is the real limit, and hitting it is a QuotaExceededError
 * caught like any other failure.
 */
export const TTS_CACHE_MAX_ENTRIES = 100

/** The small half of a row: everything but the audio. Sweeps read only these. */
export interface CacheMeta {
  /** `${sub}:${key}` */
  id: string
  sub: string
  persona: string
  /** What voice_version is compared within — see `voiceScope`. */
  scope: string
  voiceVersion: string
  lang?: string
  codec: string
  sampleRate: number
  createdAt: number
  bytes: number
}

export interface CachedClip {
  meta: CacheMeta
  chunks: ArrayBuffer[]
}

// ── Pure decisions (unit-tested) ─────────────────────────────────────────────

/**
 * SHA-256 hex of the text and the character that speaks it.
 *
 * NOT the language. The backend picks it (`resolve_voice` → `detect_lang`)
 * from the reply, the user's question when there is one, and the character's
 * own default — and /chat and POST /tts feed it different inputs, so for a
 * short reply the two can disagree. A key built on a language the browser only
 * guesses would miss, or worse, match the wrong voice. Two of the three inputs
 * are in the key; the language rides inside the row when the server names it.
 *
 * JSON rather than `text|persona`: a text containing `|` could otherwise
 * produce the same string as a different text/persona pair.
 *
 * @returns null where SubtleCrypto does not exist — it is secure-context only,
 *          so plain http:// on a LAN address has none. No key, no cache.
 */
export async function cacheKeyFor(text: string, persona: string): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) return null
  try {
    const input = new TextEncoder().encode(JSON.stringify([text, persona]))
    const digest = await subtle.digest('SHA-256', input)
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
  } catch {
    return null
  }
}

/**
 * The set of rows a voice_version is compared within.
 *
 * voice_version is a hash of the reference recording SpeechLLm actually cloned,
 * and each character has one recording PER LANGUAGE (anne_vi.wav, anne_en.wav).
 * Two versions for one character are therefore either a re-recording (the old
 * rows are stale) or simply the other language (they are not) — and only the
 * language tells those apart. `speech_start` does not carry it today, so the
 * scope falls back to the character alone and a bilingual conversation
 * evicts the other language's rows. Costly (a re-synthesis), never wrong (no
 * row is ever played in a voice it was not made with). If the server starts
 * sending `lang`, the scope narrows with no other change.
 */
export function voiceScope(persona: string, lang?: string): string {
  return lang ? `${persona}|${lang}` : persona
}

/**
 * True once a row is at least `ttlMs` old.
 *
 * Also true for a row dated in the future — the clock was moved back since it
 * was written — and for a corrupt timestamp. Otherwise such a row would outlive
 * its TTL by however far the clock jumped.
 */
export function isExpired(createdAt: number, now: number, ttlMs = TTS_CACHE_TTL_MS): boolean {
  const age = now - createdAt
  return !(age >= 0 && age < ttlMs)
}

/** This user's rows for this voice scope that were made with a different voice. */
export function staleIds(
  metas: readonly CacheMeta[],
  sub: string,
  scope: string,
  voiceVersion: string,
): string[] {
  // No version, no comparison: deleting on an empty string would wipe the
  // character's rows over a server that simply left the field out.
  if (!voiceVersion) return []
  return metas
    .filter((m) => m.sub === sub && m.scope === scope && m.voiceVersion !== voiceVersion)
    .map((m) => m.id)
}

/** Rows that are not `sub`'s, plus `sub`'s expired ones. A null sub owns nothing. */
export function sweepIds(metas: readonly CacheMeta[], sub: string | null, now: number): string[] {
  return metas.filter((m) => m.sub !== sub || isExpired(m.createdAt, now)).map((m) => m.id)
}

/** The oldest rows beyond `max`. */
export function evictionIds(metas: readonly CacheMeta[], max = TTS_CACHE_MAX_ENTRIES): string[] {
  if (metas.length <= max) return []
  return [...metas]
    .sort((a, b) => a.createdAt - b.createdAt)
    .slice(0, metas.length - max)
    .map((m) => m.id)
}

// ── IndexedDB ────────────────────────────────────────────────────────────────
//
// Two stores sharing one key, written in one transaction: `meta` (tiny) and
// `audio` (the chunks). Every sweep reads only `meta`, so deciding what to
// delete never pulls tens of MB of audio into memory just to look at dates.

const DB_NAME = 'vva-tts-cache'
const DB_VERSION = 1
const META = 'meta'
const AUDIO = 'audio'

const OPEN_TIMEOUT_MS = 3000
/** A replay waits on this before it can fall back to the network. */
const OP_TIMEOUT_MS = 2000
/** Sign-out waits on this. Short on purpose: step 3 above is the guarantee. */
const CLEAR_TIMEOUT_MS = 1500

let dbPromise: Promise<IDBDatabase | null> | null = null

/** Resolve with `fallback` if `p` rejects or takes longer than `ms`. */
function bounded<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<T>((resolve) => {
    timer = setTimeout(() => resolve(fallback), ms)
  })
  return Promise.race([p.catch(() => fallback), timeout]).finally(() => clearTimeout(timer))
}

function openDb(): Promise<IDBDatabase | null> {
  dbPromise ??= bounded(
    new Promise<IDBDatabase | null>((resolve) => {
      try {
        if (typeof indexedDB === 'undefined') return resolve(null)
        const req = indexedDB.open(DB_NAME, DB_VERSION)
        req.onupgradeneeded = () => {
          const db = req.result
          if (!db.objectStoreNames.contains(META)) db.createObjectStore(META, { keyPath: 'id' })
          if (!db.objectStoreNames.contains(AUDIO)) db.createObjectStore(AUDIO, { keyPath: 'id' })
        }
        req.onsuccess = () => {
          const db = req.result
          // Another tab upgrading the schema, or the browser clearing site
          // data: let go so that can proceed, and reopen on next use.
          db.onversionchange = () => {
            db.close()
            dbPromise = null
          }
          db.onclose = () => {
            dbPromise = null
          }
          resolve(db)
        }
        // Firefox private windows land here (InvalidStateError), among others.
        req.onerror = () => resolve(null)
      } catch {
        resolve(null)
      }
    }),
    OPEN_TIMEOUT_MS,
    null,
  )
  return dbPromise
}

function txDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error ?? new DOMException('transaction aborted', 'AbortError'))
  })
}

/**
 * Delete whichever rows `pick` names, deciding from `meta` alone, in one
 * read-write transaction — so nothing can be written between the look and the
 * delete.
 */
async function prune(pick: (metas: CacheMeta[]) => string[]): Promise<void> {
  const db = await openDb()
  if (!db) return
  const tx = db.transaction([META, AUDIO], 'readwrite')
  const metaStore = tx.objectStore(META)
  const audioStore = tx.objectStore(AUDIO)
  const all = metaStore.getAll()
  all.onsuccess = () => {
    for (const id of pick(all.result as CacheMeta[])) {
      metaStore.delete(id)
      audioStore.delete(id)
    }
  }
  await txDone(tx)
}

/**
 * The cached clip for `key`, or null for a miss.
 *
 * An expired row is a miss, and is deleted on the way out rather than left for
 * the next sweep.
 */
export function readCachedClip(sub: string, key: string): Promise<CachedClip | null> {
  return bounded(
    (async () => {
      const db = await openDb()
      if (!db) return null
      const id = `${sub}:${key}`
      const tx = db.transaction([META, AUDIO], 'readonly')
      const metaReq = tx.objectStore(META).get(id)
      const audioReq = tx.objectStore(AUDIO).get(id)
      await txDone(tx)
      const meta = metaReq.result as CacheMeta | undefined
      const audio = audioReq.result as { id: string; chunks: ArrayBuffer[] } | undefined
      if (!meta || !audio || !Array.isArray(audio.chunks)) return null
      if (isExpired(meta.createdAt, Date.now())) {
        void prune(() => [id]).catch(() => {})
        return null
      }
      return { meta, chunks: audio.chunks }
    })(),
    OP_TIMEOUT_MS,
    null,
  )
}

export interface CacheEntry {
  persona: string
  voiceVersion: string
  lang?: string
  codec: string
  sampleRate: number
  chunks: ArrayBuffer[]
}

/** Store a complete clip, then trim the cache back under its cap. */
export function writeCachedClip(sub: string, key: string, entry: CacheEntry): Promise<void> {
  return bounded(
    (async () => {
      const db = await openDb()
      if (!db) return
      const id = `${sub}:${key}`
      const meta: CacheMeta = {
        id,
        sub,
        persona: entry.persona,
        scope: voiceScope(entry.persona, entry.lang),
        voiceVersion: entry.voiceVersion,
        lang: entry.lang,
        codec: entry.codec,
        sampleRate: entry.sampleRate,
        createdAt: Date.now(),
        bytes: entry.chunks.reduce((n, c) => n + c.byteLength, 0),
      }
      const tx = db.transaction([META, AUDIO], 'readwrite')
      const metaStore = tx.objectStore(META)
      const audioStore = tx.objectStore(AUDIO)
      // IndexedDB copies the buffers (structured clone), so the clip in memory
      // keeps its own and can go on playing.
      metaStore.put(meta)
      audioStore.put({ id, chunks: entry.chunks })
      const all = metaStore.getAll()
      all.onsuccess = () => {
        for (const old of evictionIds(all.result as CacheMeta[])) {
          metaStore.delete(old)
          audioStore.delete(old)
        }
      }
      await txDone(tx)
    })(),
    OP_TIMEOUT_MS,
    undefined,
  )
}

/**
 * A synthesis just reported which voice it used: delete this user's rows for
 * the same voice scope that were made with a different one. Without this, a
 * re-recorded voice would keep playing the old recording until the TTL ran out.
 */
export function dropStaleVoice(
  sub: string,
  persona: string,
  lang: string | undefined,
  voiceVersion: string,
): Promise<void> {
  return bounded(
    prune((metas) => staleIds(metas, sub, voiceScope(persona, lang), voiceVersion)),
    OP_TIMEOUT_MS,
    undefined,
  )
}

/**
 * Delete every row that is not `sub`'s, and `sub`'s expired ones; then trim to
 * the cap. Run whenever the signed-in user becomes known — see Isolation above.
 * A null sub means nobody is signed in, so nothing here belongs to anyone.
 */
export function sweepTtsCache(sub: string | null): Promise<void> {
  if (sub === null) return clearTtsCache()
  return bounded(
    prune((metas) => {
      const doomed = new Set(sweepIds(metas, sub, Date.now()))
      const kept = metas.filter((m) => !doomed.has(m.id))
      for (const id of evictionIds(kept)) doomed.add(id)
      return [...doomed]
    }),
    OP_TIMEOUT_MS,
    undefined,
  )
}

/** Delete everything. For sign-out: await it BEFORE anything that may navigate. */
export function clearTtsCache(): Promise<void> {
  return bounded(
    (async () => {
      const db = await openDb()
      if (!db) return
      const tx = db.transaction([META, AUDIO], 'readwrite')
      tx.objectStore(META).clear()
      tx.objectStore(AUDIO).clear()
      await txDone(tx)
    })(),
    CLEAR_TIMEOUT_MS,
    undefined,
  )
}
