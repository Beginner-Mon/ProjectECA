/**
 * Migration of legacy VVA browser-storage keys to their ECA names.
 *
 * Four cases: copies old to new and deletes the old key; never overwrites
 * an existing new key (but still deletes the old one); does nothing when
 * there is no old key; never throws when storage itself throws.
 */

import { describe, expect, it } from 'vitest'
import { migrateStorageKeys } from './storageKeyMigration'

/** A Storage that records writes, so "wrote nothing" is assertable. */
function fakeStorage(initial: Record<string, string> = {}): Storage & { writes: number } {
  const map = new Map(Object.entries(initial))
  return {
    writes: 0,
    get length() {
      return map.size
    },
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    getItem: (k: string) => map.get(k) ?? null,
    setItem(this: { writes: number }, k: string, v: string) {
      this.writes++
      map.set(k, v)
    },
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
  } as Storage & { writes: number }
}

function throwingStorage(): Storage {
  const boom = () => {
    throw new DOMException('denied', 'SecurityError')
  }
  return {
    length: 0,
    key: boom,
    getItem: boom,
    setItem: boom,
    removeItem: boom,
    clear: boom,
  } as unknown as Storage
}

describe('migrateStorageKeys', () => {
  it('copies the old key to the new one and deletes the old key', () => {
    const storage = fakeStorage({ vva_avatar_bg: 'slate' })
    migrateStorageKeys(storage)
    expect(storage.getItem('eca-avatar-bg')).toBe('slate')
    expect(storage.getItem('vva_avatar_bg')).toBeNull()
  })

  it('does not overwrite the new key when it already exists', () => {
    const storage = fakeStorage({ vva_avatar_bg: 'slate', 'eca-avatar-bg': 'ocean' })
    migrateStorageKeys(storage)
    expect(storage.getItem('eca-avatar-bg')).toBe('ocean')
    expect(storage.getItem('vva_avatar_bg')).toBeNull()
  })

  it('writes nothing when there is no old key', () => {
    const storage = fakeStorage()
    migrateStorageKeys(storage)
    expect(storage.writes).toBe(0)
  })

  it('does not throw when storage throws', () => {
    expect(() => migrateStorageKeys(throwingStorage())).not.toThrow()
  })
})
