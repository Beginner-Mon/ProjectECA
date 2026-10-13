import { useEffect, useState, type FormEvent } from 'react'
import { Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useLocale } from '@/hooks/useLocale'
import { createUserMemory, deleteUserMemory, listUserMemory, type UserMemoryItem } from '@/lib/api'
import { MAX_FACT_LENGTH, normaliseFact, normaliseFacts, sortFacts, validateNewFact } from '@/lib/userMemory'

const FIELD =
  'px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors'
const BUTTON =
  'px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors'
const LINK =
  'text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground transition-colors'

/**
 * Settings → Memory: what ECA remembers about the user. The user can read every
 * fact, delete any of them, and add their own. A failed call shows a short
 * message and leaves the list as it was.
 */
export default function MemoryContent() {
  const { t } = useTranslation()
  const { locale } = useLocale()
  const [facts, setFacts] = useState<UserMemoryItem[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [draft, setDraft] = useState('')
  const [adding, setAdding] = useState(false)
  // The fact the user is being asked to confirm deleting, and the one in flight.
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<'save' | 'delete' | null>(null)

  // `attempt` is bumped by the retry link; the effect re-runs on it.
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    listUserMemory()
      .then((answer) => {
        if (alive) setFacts(sortFacts(normaliseFacts(answer)))
      })
      .catch(() => {
        if (alive) setLoadFailed(true)
      })
    return () => {
      alive = false
    }
  }, [attempt])

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault()
    const text = validateNewFact(draft)
    if (!text || adding) return
    setAdding(true)
    setActionError(null)
    try {
      const created = normaliseFact(await createUserMemory(text))
      setFacts((prev) => sortFacts([created, ...(prev ?? [])]))
      setDraft('')
    } catch {
      setActionError('save')
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (id: string) => {
    setDeletingId(id)
    setActionError(null)
    try {
      await deleteUserMemory(id)
      setFacts((prev) => (prev ?? []).filter((f) => f.id !== id))
      setConfirmId(null)
    } catch {
      setActionError('delete')
    } finally {
      setDeletingId(null)
    }
  }

  const formatDate = (iso: string) => {
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(locale)
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <p className="text-sm text-foreground/90">{t('memory.intro')}</p>
      <p className="text-sm text-muted-foreground mt-2 mb-6">{t('memory.cascade_note')}</p>

      <form onSubmit={handleAdd} className="flex gap-2 mb-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={MAX_FACT_LENGTH}
          placeholder={t('memory.add_placeholder')}
          aria-label={t('memory.add_placeholder')}
          className={`flex-1 min-w-0 ${FIELD}`}
        />
        <button type="submit" disabled={adding || !validateNewFact(draft)} className={BUTTON}>
          {t('common.add')}
        </button>
      </form>
      <div className="min-h-5 mb-4">
        {actionError && (
          <p className="text-sm text-destructive">
            {actionError === 'save' ? t('memory.save_failed') : t('memory.delete_failed')}
          </p>
        )}
      </div>

      {loadFailed && (
        <div className="flex items-center gap-3">
          <p className="text-sm text-destructive">{t('memory.load_failed')}</p>
          <button
            type="button"
            onClick={() => {
              setLoadFailed(false)
              setAttempt((n) => n + 1)
            }}
            className={LINK}
          >
            {t('common.refresh')}
          </button>
        </div>
      )}

      {!loadFailed && facts === null && (
        <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
      )}

      {facts !== null && facts.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('memory.empty')}</p>
      )}

      {facts !== null && facts.length > 0 && (
        <ul className="space-y-4">
          {facts.map((fact) => (
            <li
              key={fact.id}
              className="flex justify-between items-center gap-4 rounded-xl border border-border/40 bg-card py-4 px-6"
            >
              <div className="min-w-0">
                <p className="text-base text-foreground break-words">{fact.fact_text}</p>
                <p className="text-sm text-muted-foreground mt-1">
                  {fact.source === 'extracted' ? t('memory.source_extracted') : t('memory.source_user')}
                  {formatDate(fact.updated_at) && ` · ${formatDate(fact.updated_at)}`}
                </p>
              </div>
              {confirmId === fact.id ? (
                <div className="flex items-center gap-3 shrink-0">
                  <span className="text-sm text-foreground">{t('memory.delete_confirm')}</span>
                  <button
                    type="button"
                    onClick={() => void handleDelete(fact.id)}
                    disabled={deletingId === fact.id}
                    className="text-sm font-medium text-destructive hover:underline disabled:opacity-50"
                  >
                    {t('common.delete')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmId(null)}
                    disabled={deletingId === fact.id}
                    className={LINK}
                  >
                    {t('common.cancel')}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setActionError(null)
                    setConfirmId(fact.id)
                  }}
                  title={t('common.delete')}
                  aria-label={t('common.delete')}
                  className="shrink-0 p-2 rounded-lg text-muted-foreground hover:text-destructive transition-colors"
                >
                  <Trash2 className="w-[18px] h-[18px]" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
