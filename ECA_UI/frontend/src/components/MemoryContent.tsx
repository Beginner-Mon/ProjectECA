import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Check, FileText, Loader2, Pencil, Plus, Trash2 } from 'lucide-react'
import { isAxiosError } from 'axios'
import TextareaAutosize from 'react-textarea-autosize'
import { useTranslation } from 'react-i18next'
import { createUserMemory, deleteAllUserMemory, deleteUserMemory, listUserMemory, updateUserMemory, MEMORY_TYPES, type MemoryType, type UserMemoryItem } from '@/lib/api'
import { MAX_FACT_LENGTH, groupByType, normaliseFact, normaliseFacts, sortFacts, validateNewFact, validateTopic } from '@/lib/userMemory'

const ACTION = 'inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40'
const NEW_FACT = 'new'

function MemoryEditor({ fact, onSave, onDelete, onCancel, onReload }: {
  fact?: UserMemoryItem
  onSave: (text: string) => Promise<void>
  onDelete: () => Promise<void>
  onCancel: () => void
  onReload: () => void
}) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(fact?.fact_text ?? '')
  const [pending, setPending] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<'save' | 'delete' | 'conflict' | null>(null)
  const inFlight = useRef(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const validText = validateNewFact(draft)
  const length = Array.from(draft.trim()).length

  useEffect(() => { input.current?.focus() }, [])

  const perform = async (action: 'save' | 'delete') => {
    if (inFlight.current || (action === 'save' && (!validText || error === 'conflict'))) return
    inFlight.current = true
    setPending(true)
    setError(null)
    try {
      if (action === 'delete') await onDelete()
      else await onSave(validText!)
    } catch (err) {
      const status = isAxiosError(err) ? err.response?.status : undefined
      setError(status === 409 || status === 404 ? 'conflict' : action)
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!confirmDelete && validText !== fact?.fact_text) void perform('save')
  }

  return (
    <form onSubmit={submit} className="min-w-0 flex-1" onKeyDown={(event) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        if (!pending) onCancel()
      }
    }}>
      <TextareaAutosize
        ref={input}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') submit(event)
        }}
        disabled={pending}
        minRows={2}
        maxRows={10}
        aria-label={t(fact ? 'memory.edit' : 'memory.add')}
        aria-describedby="memory-edit-hint"
        aria-invalid={length > MAX_FACT_LENGTH}
        placeholder={t('memory.add_placeholder')}
        className="block w-full resize-none rounded-lg border border-border/60 bg-background/30 px-3 py-2 text-sm leading-7 text-foreground placeholder:text-muted-foreground/50 focus:border-ring focus:outline-none disabled:opacity-60"
      />
      <div id="memory-edit-hint" className="mt-2 flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
        <span>{t('memory.editor_hint')}</span>
        <span className={length > MAX_FACT_LENGTH ? 'text-destructive' : ''}>{length} / {MAX_FACT_LENGTH}</span>
      </div>
      {error && <div role="alert" className="mt-2 text-sm text-destructive">
        <p>{t(error === 'conflict' ? 'memory.conflict' : error === 'delete' ? 'memory.delete_failed' : 'memory.save_failed')}</p>
        {error === 'conflict' && <button type="button" onClick={onReload} className={`${ACTION} underline underline-offset-4`}>{t('memory.reload_document')}</button>}
      </div>}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        {fact ? (
          confirmDelete ? <div className="flex flex-wrap items-center gap-1">
            <span className="text-xs text-muted-foreground">{t('memory.delete_confirm')}</span>
            <button type="button" disabled={pending} onClick={() => void perform('delete')} className={`${ACTION} text-destructive hover:bg-destructive/10`}>{t('common.delete')}</button>
            <button type="button" disabled={pending} onClick={() => setConfirmDelete(false)} className={`${ACTION} text-muted-foreground hover:bg-secondary/60`}>{t('common.cancel')}</button>
          </div> : <button type="button" disabled={pending} onClick={() => setConfirmDelete(true)} title={t('common.delete')} aria-label={t('common.delete')} className={`${ACTION} text-muted-foreground hover:bg-destructive/10 hover:text-destructive`}><Trash2 className="h-4 w-4" /></button>
        ) : <span />}
        {!confirmDelete && <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={onCancel} disabled={pending} className={`${ACTION} text-muted-foreground hover:bg-secondary/60`}>{t('common.cancel')}</button>
          <button type="submit" disabled={pending || !validText || validText === fact?.fact_text || error === 'conflict'} className={`${ACTION} bg-primary text-primary-foreground hover:bg-primary/90`}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {t(pending ? 'memory.saving' : 'memory.save')}
          </button>
        </div>}
      </div>
    </form>
  )
}

function SaidOn({ saidAt }: { saidAt: string }) {
  const { i18n } = useTranslation()
  let text = saidAt.slice(0, 10)
  const parsed = new Date(saidAt)
  if (!Number.isNaN(parsed.getTime())) {
    try {
      text = parsed.toLocaleDateString(i18n.language === 'vi' ? 'vi-VN' : 'en-US', {
        year: 'numeric', month: 'short', day: 'numeric',
      })
    } catch {
      /* keep the ISO date */
    }
  }
  return <>{text}</>
}

/** Markdown-like presentation; each editor retains a single fact's identity. */
export default function MemoryContent() {
  const { t } = useTranslation()
  const [facts, setFacts] = useState<UserMemoryItem[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [newType, setNewType] = useState<MemoryType>('profile')
  const [newTopic, setNewTopic] = useState('')
  const [topicInvalid, setTopicInvalid] = useState(false)
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false)
  const [saved, setSaved] = useState(false)
  const lastTrigger = useRef(NEW_FACT)
  const editButtons = useRef(new Map<string, HTMLButtonElement>())

  const rememberButton = (id: string, button: HTMLButtonElement | null) => {
    if (button) editButtons.current.set(id, button)
    else editButtons.current.delete(id)
  }

  useEffect(() => {
    let alive = true
    listUserMemory()
      .then((answer) => { if (alive) setFacts(sortFacts(normaliseFacts(answer))) })
      .catch(() => { if (alive) setLoadFailed(true) })
    return () => { alive = false }
  }, [attempt])

  const finish = () => {
    setEditingId(null)
    requestAnimationFrame(() => (editButtons.current.get(lastTrigger.current) ?? editButtons.current.get(NEW_FACT))?.focus())
  }
  const reload = () => {
    finish()
    setFacts(null)
    setSaved(false)
    setLoadFailed(false)
    setConfirmDeleteAll(false)
    setAttempt((value) => value + 1)
  }
  const saveFact = async (fact: UserMemoryItem | undefined, text: string) => {
    let topic: string | null = null
    if (!fact) {
      const checked = validateTopic(newTopic)
      if (checked === 'invalid') {
        setTopicInvalid(true)
        return
      }
      topic = checked
    }
    const updated = normaliseFact(await (fact
      ? updateUserMemory(fact, text)
      : createUserMemory(text, {
        kind: newType === 'instruction' ? 'instruction' : 'fact',
        memory_type: newType,
        ...(topic !== null && { project: topic }),
      })))
    setFacts((prev) => fact ? (prev ?? []).map((item) => item.id === fact.id ? updated : item) : [...(prev ?? []), updated])
    setSaved(true)
    if (!fact) {
      setNewTopic('')
      setTopicInvalid(false)
    }
    finish()
  }
  const deleteAll = async () => {
    await deleteAllUserMemory()
    setFacts([])
    setSaved(true)
    setConfirmDeleteAll(false)
    finish()
  }

  const grouped = facts === null ? null : groupByType(facts)

  const renderRow = (fact: UserMemoryItem) => (
    <li key={fact.id} className="group flex items-start gap-3 py-1 text-sm leading-7 sm:text-[15px]">
      <span className="select-none pt-1 font-mono text-muted-foreground/50" aria-hidden="true">-</span>
      {editingId === fact.id ? <MemoryEditor fact={fact} onSave={(text) => saveFact(fact, text)} onCancel={finish} onReload={reload} onDelete={async () => {
        await deleteUserMemory(fact.id)
        setFacts((prev) => (prev ?? []).filter((item) => item.id !== fact.id))
        setSaved(true)
        finish()
      }} /> : <>
        <div className="min-w-0 flex-1 py-1">
          <p className="whitespace-pre-wrap break-words text-foreground/90">{fact.fact_text}</p>
          {fact.memory_type === 'instruction' && fact.tier === 2 && fact.project && <p className="text-xs text-muted-foreground">{t('memory.only_for', { topic: fact.project })}</p>}
          <p className="text-xs text-muted-foreground/70"><SaidOn saidAt={fact.said_at ?? fact.created_at} /> · {t(fact.source === 'extracted' ? 'memory.source_extracted' : 'memory.source_user')}</p>
        </div>
        <button ref={(button) => rememberButton(fact.id, button)} type="button" disabled={editingId !== null} title={t('memory.edit')} aria-label={t('memory.edit_fact', { fact: fact.fact_text })} onClick={() => {
          lastTrigger.current = fact.id
          setSaved(false)
          setEditingId(fact.id)
        }} className={`${ACTION} shrink-0 px-2 text-muted-foreground/60 hover:bg-secondary/60 hover:text-foreground`}><Pencil className="h-4 w-4" aria-hidden="true" /></button>
      </>}
    </li>
  )

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label={t('memory.title')}>
      <div className="flex min-h-16 shrink-0 items-center justify-between gap-3 border-b border-border/40 px-5 sm:px-8">
        <div className="flex items-center gap-2.5 text-muted-foreground">
          <FileText className="h-4 w-4" aria-hidden="true" />
          <span className="font-mono text-sm text-foreground">{t('memory.filename')}</span>
        </div>
        <button ref={(button) => rememberButton(NEW_FACT, button)} type="button" disabled={facts === null || loadFailed || editingId !== null} title={t('memory.add')} aria-label={t('memory.add')} onClick={() => {
          lastTrigger.current = NEW_FACT
          setSaved(false)
          setEditingId(NEW_FACT)
        }} className={`${ACTION} text-muted-foreground hover:bg-secondary/60 hover:text-foreground`}><Plus className="h-4 w-4" /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-7 sm:px-10 sm:py-8">
        <h3 className="mb-6 flex items-baseline gap-3 text-xl font-semibold tracking-tight text-foreground">
          <span className="select-none font-mono font-normal text-muted-foreground/40" aria-hidden="true">#</span>
          {t('memory.document_title')}
        </h3>
        {loadFailed ? <div role="alert" className="text-sm">
          <p className="text-destructive">{t('memory.load_failed')}</p>
          <button type="button" onClick={reload} className="mt-2 rounded-sm text-sm text-foreground underline underline-offset-4 transition-colors hover:text-foreground/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{t('common.refresh')}</button>
        </div> : facts === null || grouped === null ? <p role="status" className="text-sm text-muted-foreground">{t('common.loading')}</p> : <>
          {facts.length === 0 && editingId !== NEW_FACT && <p className="text-sm leading-7 text-muted-foreground">{t('memory.empty_document')}</p>}
          {grouped.map((section, index) => (
            <div key={section.type}>
              <h4 className={`mb-2 text-sm font-semibold text-foreground ${index === 0 ? 'mt-2' : 'mt-6'}`}>{t(`memory.type_${section.type}`)}</h4>
              {section.topics.map((group) => (
                <div key={group.topic ?? ''}>
                  {group.topic !== null && <h5 className="mb-1 mt-4 font-mono text-xs text-muted-foreground">{group.topic}</h5>}
                  <ul className="space-y-2">
                    {group.items.map(renderRow)}
                  </ul>
                </div>
              ))}
            </div>
          ))}
          {editingId === NEW_FACT && <div className="mt-4">
            <label className="mb-2 block text-xs text-muted-foreground" htmlFor="memory-new-kind">{t('memory.kind_label')}</label>
            <select id="memory-new-kind" value={newType} onChange={(event) => setNewType(event.target.value as MemoryType)} className="mb-2 block rounded-lg border border-border/60 bg-background/30 px-3 py-2 text-sm text-foreground focus:border-ring focus:outline-none">
              {MEMORY_TYPES.map((type) => <option key={type} value={type}>{t(`memory.type_${type}`)}</option>)}
            </select>
            <input
              id="memory-new-topic"
              type="text"
              value={newTopic}
              onChange={(event) => { setNewTopic(event.target.value); setTopicInvalid(false) }}
              aria-label={t('memory.topic_label')}
              aria-invalid={topicInvalid}
              aria-describedby={topicInvalid ? 'memory-topic-error' : undefined}
              placeholder={t('memory.topic_placeholder')}
              className="mb-2 block w-full rounded-lg border border-border/60 bg-background/30 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 focus:border-ring focus:outline-none"
            />
            {topicInvalid && <p id="memory-topic-error" role="alert" className="mb-2 text-sm text-destructive">{t('memory.topic_invalid')}</p>}
            <div className="flex items-start gap-3 py-1">
              <span className="pt-2 font-mono text-muted-foreground/50" aria-hidden="true">-</span>
              <MemoryEditor onSave={(text) => saveFact(undefined, text)} onCancel={finish} onReload={reload} onDelete={async () => {}} />
            </div>
          </div>}
        </>}
      </div>
      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border/30 px-5 py-3 text-xs text-muted-foreground sm:px-8">
        <span>{facts !== null && t('memory.fact_count', { count: facts.length })}</span>
        <span className="flex items-center gap-1.5">
          {confirmDeleteAll ? <>
            <span>{t('memory.delete_all_confirm')}</span>
            <button type="button" onClick={() => void deleteAll()} className={`${ACTION} text-destructive hover:bg-destructive/10`}>{t('common.delete')}</button>
            <button type="button" onClick={() => setConfirmDeleteAll(false)} className={`${ACTION} hover:bg-secondary/60`}>{t('common.cancel')}</button>
          </> : <>
            {facts !== null && facts.length > 0 && <button type="button" onClick={() => setConfirmDeleteAll(true)} className={`${ACTION} hover:bg-secondary/60`}>{t('memory.delete_all')}</button>}
            <span role="status" className="flex items-center gap-1.5">{saved && <><Check className="h-3.5 w-3.5" aria-hidden="true" />{t('memory.saved')}</>}</span>
          </>}
        </span>
      </div>
    </section>
  )
}
