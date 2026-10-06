/**
 * One-time migration of browser storage keys from the VVA naming to ECA.
 *
 * Added 06/10/2026. Safe to remove after 06/11/2026: anyone who has not
 * opened the app in that window falls back to defaults and starts a new
 * chat session. No server-side data is affected.
 */

const KEY_PAIRS: Array<[oldKey: string, newKey: string]> = [
  ['vva_session_id', 'eca-session-id'],
  ['vva_avatar_bg', 'eca-avatar-bg'],
  ['vva-graphics-settings', 'eca-graphics-settings'],
  ['vva_demo_user', 'eca-demo-user'],
]

const OLD_TTS_CACHE_DB = 'vva-tts-cache'

/** Copy each legacy localStorage key to its new name (never overwriting),
 * then delete the old key. Idempotent; per-pair try/catch because storage
 * can throw in private mode. */
export function migrateStorageKeys(storage: Storage): void {
  for (const [oldKey, newKey] of KEY_PAIRS) {
    try {
      const oldValue = storage.getItem(oldKey)
      if (oldValue === null) continue
      if (storage.getItem(newKey) === null) {
        storage.setItem(newKey, oldValue)
      }
      storage.removeItem(oldKey)
    } catch {
      // Unavailable or denied — leave both keys untouched.
    }
  }
}

/** Drop the legacy TTS audio cache. Not copied: it is a TTL cache, a miss
 * just re-downloads. Fire-and-forget; errors are ignored. */
export function dropOldTtsCache(): void {
  try {
    indexedDB.deleteDatabase(OLD_TTS_CACHE_DB)
  } catch {
    // No IndexedDB or denied — nothing to clean up.
  }
}

// GraphicsContext reads localStorage at module load (`let state = load()`),
// so this module must be imported before any other app import (see main.tsx:
// it is the first import there), not called from inside a component.
if (typeof localStorage !== 'undefined') {
  migrateStorageKeys(localStorage)
}
if (typeof indexedDB !== 'undefined') {
  dropOldTtsCache()
}
