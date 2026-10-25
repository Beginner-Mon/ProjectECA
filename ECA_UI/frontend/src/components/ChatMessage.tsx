import { useState, useRef, useEffect, useSyncExternalStore, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, ThumbsUp, ThumbsDown, Volume2, Check, Pause, Square, Loader2 } from 'lucide-react'
import {
  DEFAULT_PERSONA_ID,
  saveMessageFeedback,
  clearMessageFeedback,
  type FeedbackReason,
  type MessageFeedback,
} from '@/lib/api'
import {
  CLIP_ABORTED,
  CLIP_UNDECODABLE,
  speechPlayer,
  unlockSpeechAudio,
  type ClipSnapshot,
  type SpeechClip,
} from '../lib/speechPlayer'
import { cancelSpeech, liveSpeech, openSpeech } from '../lib/speechSource'
import { useMotion } from '../hooks/useMotion'
import { useChat } from '../hooks/useChat'
import { useAuth } from '../contexts/AuthContext'
import { errorAction, errorCopy, showRetry, type ChatError } from '../lib/chatErrors'
import DislikeFeedbackModal from './feedback/DislikeFeedbackModal'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: Date
  /** Ephemeral divider inserted when character is switched. Rendered as <hr> + label, never persisted. */
  kind?: 'chat' | 'divider'
  /** For divider: slug/label of the character switched to */
  dividerMeta?: { to: string; toLabel?: string }
  /** USER voice notes only: an object URL for the recording, played by a plain
   *  <audio controls>. An assistant's voice is `speech`. */
  audioUrl?: string
  /**
   * Voice mode: this reply's audio, streaming in as it is synthesised. Created
   * empty when the message is, so the speaker shows "audio coming" from the
   * start; ChatContext feeds it `speech_*` events and starts playback at
   * `speech_start`. A mutable handle, not state — chunks land in it without a
   * re-render each, and the speaker button subscribes to it.
   */
  speech?: SpeechClip
  /** The character who wrote this reply, so a replay asks for the same voice
   *  and finds the same cache row. Absent on restored turns; the button then
   *  uses whoever is on screen. */
  personaId?: string
  /** One line about this reply's 3D motion: rendering, or why there is none.
   *  Cleared once the clip plays, because the avatar is then saying it. The
   *  GPU worker is off by default, so "unavailable" is the ordinary case and a
   *  user who asked to SEE a movement needs telling — silence reads as the
   *  request having been misunderstood. */
  motionNotice?: string
  /** One line when the user's own provider failed and ECA answered instead. */
  providerNotice?: string
  /** The user's own provider failed AND ECA could not answer either
   *  (`provider_notice.answered_by` null): the error block then links to the key settings. */
  providerNoticeFailed?: boolean
  /** Why this turn failed (lib/chatErrors.ts). The words are chosen at render,
   *  from the character on screen and the site language, never stored here. */
  error?: ChatError
  /** Restored turns only: the motion this reply rendered, and when it dies.
   *  A live turn plays its motion straight from the SSE event and needs
   *  neither. */
  motionJobId?: string
  motionExpiresAt?: string
  /** What the user asked for, so the replay picker can label it. */
  motionLabel?: string
  /** The message's database id (from SSE `session_persisted.assistant_message_id`,
   *  or from history). Absent for the greeting, the stream-error bubble, and
   *  turns that haven't persisted yet — feedback buttons show only once this
   *  is set. `id` above stays the client/React key and never changes to this. */
  serverId?: string
  /** The caller's saved vote on this message, when one exists. */
  feedback?: MessageFeedback | null
}

interface ChatMessageProps {
  message: Message
  isStreaming?: boolean
}

/** The thin left rule on every line that is not the agent's own answer (errors, notices). */
const NOTICE_BAR_CLASS = 'border-l-2 border-muted-foreground/40 pl-3'

/** How a reply's text looks. A failure line uses it too, so it reads as part of the reply. */
const REPLY_TEXT_CLASS =
  'prose dark:prose-invert prose-p:leading-relaxed prose-strong:text-foreground prose-headings:text-foreground prose-pre:bg-secondary/50 prose-pre:border prose-pre:border-border/40 max-w-none text-sm text-foreground/90'

function useCopy(text: string) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API not available
    }
  }
  return { copied, handleCopy }
}

export default function ChatMessage({ message, isStreaming }: ChatMessageProps) {
  const isUser = message.role === 'user'

  if (!isUser) {
    /* Assistant message được tạo rỗng ngay lúc user gửi (ChatContext) để id của
     * nó bắt token từ stream. Chưa có text thì không render — cái shell rỗng
     * vẫn ăn py-3 + space-y-2 của container, đẩy loading indicator xuống hơn
     * 30px so với user message. Trừ khi lượt này lỗi hoặc có thông báo: những
     * thứ đó người dùng phải thấy dù bubble không có chữ. */
    if (!message.content && !message.error && !message.providerNotice) return null

    const cleaned = message.content.replace(/<\/?evidence_citation>/g, '')
    return (
      <div className="px-3 desktop:px-5 py-2 desktop:py-3 animate-message-in w-full max-w-full">
        {message.content && (
          <div className={REPLY_TEXT_CLASS}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleaned}</ReactMarkdown>
          </div>
        )}
        {message.error && <ErrorBlock message={message} />}
        {message.motionNotice && (
          // Muted and small: an aside about the 3D clip, not part of the
          // answer. It disappears on its own once the avatar starts moving.
          <p className={`mt-1 text-[0.7rem] italic text-muted-foreground ${NOTICE_BAR_CLASS}`}>
            {message.motionNotice}
          </p>
        )}
        {message.providerNotice && (
          // Same aside slot: the user's provider failed (and ECA answered, or
          // — in an error turn — could not either).
          <p className={`mt-1 text-[0.7rem] italic text-muted-foreground ${NOTICE_BAR_CLASS}`}>
            {message.providerNotice}
          </p>
        )}
        {message.content && !message.error && (
          <AssistantActions message={message} isStreaming={isStreaming} />
        )}
      </div>
    )
  }

  return (
    <div className="group flex px-2 desktop:px-4 py-1 animate-message-in flex-row-reverse w-full max-w-full">
      <div className="w-fit max-w-[80%]">
        {(message.content || !message.audioUrl) && (
          <div className="min-w-0 rounded-2xl px-3 py-2 text-sm leading-relaxed bg-primary text-primary-foreground">
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          </div>
        )}
        {message.audioUrl && (
          <div className="mt-1 rounded-xl overflow-hidden bg-secondary/40 p-1">
            <audio controls src={message.audioUrl} className="w-full h-8" preload="metadata" />
          </div>
        )}
        <UserCopyAction content={message.content} />
      </div>
    </div>
  )
}

/**
 * A failure line is just more of the character's reply: the same text style as
 * any answer (REPLY_TEXT_CLASS), no box, no colour, no icon. Under it, small
 * muted text links in the style of the message action row: "Thử lại" when
 * sending the same prompt again makes sense, plus the one action the error asks
 * of the user, if any. `role="alert"` is for screen readers only.
 *
 * Used by a failed turn (ErrorBlock below) and by a conversation that would not
 * load (ChatPanel), so there is one look. Words come from lib/chatErrors.ts;
 * nothing per-code lives here.
 */
const ERROR_LINK_CLASS = 'text-muted-foreground underline underline-offset-2 hover:text-foreground'

export function ErrorNotice({
  text,
  onRetry,
  spaced = false,
  children,
}: {
  text: string
  onRetry?: () => void
  /** There is reply text above: this line starts a new paragraph of it. */
  spaced?: boolean
  children?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <div role="alert" className={`${NOTICE_BAR_CLASS}${spaced ? ' mt-3' : ''}`}>
      <div className={REPLY_TEXT_CLASS}>
        <p>{text}</p>
      </div>
      {(onRetry || !!children) && (
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {onRetry && (
            <button type="button" className={ERROR_LINK_CLASS} onClick={onRetry}>
              {t('chat.retry')}
            </button>
          )}
          {children}
        </div>
      )}
    </div>
  )
}

function ErrorBlock({ message }: { message: Message }) {
  const { t } = useTranslation()
  const { ui, isGenerating, latestAssistantId, retryTurn, requestUi } = useChat()
  const { signOut } = useAuth()
  const error = message.error!
  const action = errorAction(error, !!message.providerNoticeFailed)
  const retry = showRetry(error, latestAssistantId === message.id, isGenerating)
  const linkClass = ERROR_LINK_CLASS

  return (
    <ErrorNotice
      text={errorCopy(error, ui, t)}
      spaced={!!message.content}
      onRetry={retry ? () => retryTurn(message.id) : undefined}
    >
      {action && (
        <>
        {action === 'sign_in' && (
          <button type="button" className={linkClass} onClick={() => void signOut?.()}>
            {t('chat.sign_in_again')}
          </button>
        )}
        {action === 'llm_settings' && (
          <button type="button" className={linkClass} onClick={() => requestUi('llm_settings')}>
            {t('chat.open_llm_settings')}
          </button>
        )}
        </>
      )}
    </ErrorNotice>
  )
}

/**
 * Copy, 👍/👎 and the speaker, under one assistant reply.
 *
 * The thumbs read their color from `message.feedback` (the server's saved
 * vote), not local state — that is what makes the vote survive a reload or a
 * session switch, where a fresh component mounts with the history's answer
 * already in `message`. They render only once `message.serverId` is set: the
 * greeting, the stream-error bubble and a turn that hasn't persisted yet have
 * no row to vote on.
 */
function AssistantActions({ message, isStreaming }: { message: Message; isStreaming?: boolean }) {
  const { t } = useTranslation()
  const { setMessageFeedback } = useChat()
  const { content, speech, personaId, serverId, feedback, motionJobId, motionNotice } = message
  const { copied, handleCopy } = useCopy(content)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  /** The reason-less 👎 save still in flight while the modal is open. */
  const votePromiseRef = useRef<Promise<void> | null>(null)

  if (isStreaming) return null

  const liked = feedback?.rating === 1
  const disliked = feedback?.rating === -1

  const btnClass =
    'p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary/60'

  const iconSize = 'size-4'

  /**
   * Optimistic vote change: apply `next` to context first, fire `call`, and on
   * failure restore whatever was there before and show an inline error. `call`
   * may return the server's own row (a POST) or nothing (a DELETE) — when it
   * returns one, that replaces the optimistic guess, since a rating=+1 save
   * also clears any old reasons/comment server-side.
   */
  const applyVote = async (
    next: MessageFeedback | null,
    call: () => Promise<MessageFeedback | void>,
  ) => {
    if (saving || !serverId) return
    setError(null)
    const previous = feedback ?? null
    setMessageFeedback(serverId, next)
    setSaving(true)
    try {
      const result = await call()
      if (result) setMessageFeedback(serverId, result)
    } catch {
      setMessageFeedback(serverId, previous)
      setError(t('feedback.error_save'))
    } finally {
      setSaving(false)
    }
  }

  const handleLike = () => {
    if (!serverId) return
    if (liked) {
      void applyVote(null, () => clearMessageFeedback(serverId))
    } else {
      // Neutral or disliked before this click: either way a plain +1, with
      // reasons/comment reset — the server does that reset itself, but the
      // optimistic guess mirrors it so the color is right before it answers.
      void applyVote({ rating: 1, reasons: [], comment: null }, () =>
        saveMessageFeedback(serverId, { rating: 1 }),
      )
    }
  }

  const handleDislike = () => {
    if (!serverId) return
    if (disliked) {
      void applyVote(null, () => clearMessageFeedback(serverId))
      return
    }
    // The modal opens at once; a Neon round trip is ~1s and waiting for it
    // made the box appear late. The vote is saved in the background and its
    // promise kept, because the modal's submit must not overtake it.
    setModalOpen(true)
    votePromiseRef.current = applyVote({ rating: -1, reasons: [], comment: null }, () =>
      saveMessageFeedback(serverId, { rating: -1 }),
    )
  }

  const handleModalSubmit = async (reasons: FeedbackReason[], comment: string) => {
    if (!serverId) return
    // Wait for the reason-less save first (applyVote never rejects): if it
    // landed after this POST, its upsert would wipe the reasons just sent.
    await votePromiseRef.current
    const result = await saveMessageFeedback(serverId, {
      rating: -1,
      reasons,
      comment: comment || null,
    })
    // An upsert, so it also creates the row if the first save had failed.
    setMessageFeedback(serverId, result)
    setError(null)
    setModalOpen(false)
  }

  return (
    <>
      <div className="flex items-center gap-1 mt-1.5">
        <button className={btnClass} onClick={handleCopy} title={t('common.copy')}>
          {copied ? <Check className={iconSize} /> : <Copy className={iconSize} />}
        </button>
        {serverId && (
          <>
            <button className={btnClass} onClick={handleLike} disabled={saving} title={t('chat.like')}>
              <ThumbsUp className={`${iconSize} ${liked ? 'text-green-500' : ''}`} />
            </button>
            <button className={btnClass} onClick={handleDislike} disabled={saving} title={t('chat.dislike')}>
              <ThumbsDown className={`${iconSize} ${disliked ? 'text-blue-500' : ''}`} />
            </button>
          </>
        )}
        <AudioButton
          speech={speech}
          personaId={personaId}
          text={content}
          btnClass={btnClass}
          iconSize={iconSize}
        />
      </div>
      {error && <p className="mt-1 text-[0.7rem] text-destructive">{error}</p>}
      {/* Mounted only while open, right after a fresh 👎 save — state always
       *  starts empty, so there is nothing to re-seed from props. */}
      {modalOpen && (
        <DislikeFeedbackModal
          // A motion was requested for this turn: either it already rendered
          // (motionJobId) or is pending/unavailable/failed (motionNotice) —
          // live turns mostly carry the latter, restored ones the former.
          messageHasMotion={!!(motionJobId || motionNotice)}
          onSubmit={handleModalSubmit}
          onCancel={() => setModalOpen(false)}
        />
      )}
    </>
  )
}

const noSubscribe = () => () => {}
const noSnapshot = () => null

/** A clip's state, re-rendering when it changes; null for no clip. */
function useClipSnapshot(clip: SpeechClip | null | undefined): ClipSnapshot | null {
  return useSyncExternalStore<ClipSnapshot | null>(
    clip ? clip.subscribe : noSubscribe,
    clip ? clip.getSnapshot : noSnapshot,
  )
}

/**
 * Speaker control for one assistant message.
 *
 * Where the audio comes from, in order:
 *   1. `speech` — voice mode streamed it into this message as the reply was
 *      written. ChatContext already started it at `speech_start`; this button
 *      only pauses, resumes and replays it.
 *   2. This browser's IndexedDB cache (1 day, per user), keyed by text and
 *      character — a replay after a reload makes no request at all.
 *   3. POST /tts, streamed and played as it arrives, then cached.
 *
 * It holds no <audio> element and no autoplay latch. The shared player knows
 * which clip is speaking; this button reads that and asks it to do things, so
 * two buttons can never both believe they are the one playing.
 */
function AudioButton({
  speech,
  personaId,
  text,
  btnClass,
  iconSize,
}: {
  speech?: SpeechClip
  personaId?: string
  text: string
  btnClass: string
  iconSize: string
}) {
  const { t } = useTranslation()
  const { avatarRef, selectedVrmId } = useMotion()
  const barRef = useRef<HTMLDivElement | null>(null)
  /** A clip this button fetched itself, from the cache or POST /tts. */
  const [ownClip, setOwnClip] = useState<SpeechClip | null>(null)
  const [progress, setProgress] = useState(0)

  const speechSnap = useClipSnapshot(speech)
  const ownSnap = useClipSnapshot(ownClip)
  const player = useSyncExternalStore(speechPlayer.subscribe, speechPlayer.getSnapshot)

  /* Voice mode's clip is used while it is usable. Once it has failed —
   * speech_failed, TTS disabled, the stream cut off — this message counts as
   * having no audio in memory, exactly like a restored one, and a click goes
   * to the cache and then the network. */
  const voice = speech && speechSnap && speechSnap.status !== 'failed' ? speech : null
  const own = ownClip && ownSnap && ownSnap.status !== 'failed' ? ownClip : null
  const clip = voice ?? own
  const snap = voice ? speechSnap : own ? ownSnap : null

  const mine = !!clip && player.clip === clip
  const playing = mine && player.status === 'playing'
  const paused = mine && player.status === 'paused'
  /* Chunks are arriving but the player is still measuring how far ahead
   * generation is before it dares start (speechPlayer.play → computeStartTime).
   * Without this the button fell into a hole between "no audio yet" and
   * "playing": chunks had arrived so `waiting` was false, the run had not
   * started so `playing` was false, and the control looked dead. */
  const buffering = mine && player.status === 'buffering'
  // Audio is on its way and nothing can be heard yet.
  const waiting =
    buffering ||
    (!!snap && (snap.status === 'pending' || (snap.status === 'streaming' && snap.received === 0)))
  // Only this button's own request counts as a failure worth showing: voice
  // mode's failure already falls back silently, and an abort is not a fault.
  const failed =
    (!!ownSnap && ownSnap.status === 'failed' && ownSnap.error !== CLIP_ABORTED) ||
    snap?.error === CLIP_UNDECODABLE

  /* The progress bar reads the player's clock once per frame while playing. */
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      setProgress(speechPlayer.progress() * 100)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  /* Re-attach to synthesis already in flight for this message.
   *
   * Unmounting used to abort it: close the conversation while the speaker
   * spun and ~25 seconds of synthesis were thrown away, the button came back
   * as a plain speaker, and the next click paid for it all again. The request
   * now outlives this component (speechSource keeps it), so coming back finds
   * it — still loading, or ready to play.
   *
   * Done during render when the key changes rather than in an effect: React
   * re-renders straight away instead of committing a frame without the clip. */
  const speechPersona = personaId || selectedVrmId || DEFAULT_PERSONA_ID
  const speechKey = `${speechPersona}\u0000${text}`
  const [attachedKey, setAttachedKey] = useState<string | null>(null)
  if (attachedKey !== speechKey) {
    setAttachedKey(speechKey)
    const found = liveSpeech(text, speechPersona)
    if (found) setOwnClip(found)
  }

  const handleToggle = () => {
    // Before any await: Safari and iOS WebViews start an AudioContext only
    // from inside the gesture's own call stack, and the play() below may be a
    // cache lookup away from this click.
    unlockSpeechAudio()

    if (playing) {
      speechPlayer.pause()
      return
    }
    if (paused) {
      void speechPlayer.resume(avatarRef.current)
      return
    }
    if (clip) {
      // Voice mode's clip that autoplay could not start, or one that already
      // finished: from the top, from memory, no request.
      void speechPlayer.play(clip, avatarRef.current)
      return
    }
    // Nothing in memory — the cache, then the network. The clip comes back
    // already, empty, and fills itself; the player schedules chunks as they
    // land, so the first one plays the moment it decodes.
    const fresh = openSpeech(text, personaId || selectedVrmId || DEFAULT_PERSONA_ID)
    setOwnClip(fresh)
    void speechPlayer.play(fresh, avatarRef.current)
  }

  /** Stop for good: unlike pause, the next click starts from the beginning.
   *  During the wait this is also the cancel — it aborts the synthesis, which
   *  is the only thing there is to stop before any sound exists. */
  const handleStop = () => {
    if (mine) speechPlayer.stop()
    if (ownClip && !ownClip.settled) {
      cancelSpeech(text, personaId || selectedVrmId || DEFAULT_PERSONA_ID)
      setOwnClip(null)
    }
  }

  const handleBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const bar = barRef.current
    if (!mine || !bar) return
    const rect = bar.getBoundingClientRect()
    const fraction = (e.clientX - rect.left) / rect.width
    speechPlayer.seek(fraction)
    setProgress(fraction * 100)
  }

  /* The stop button shows during the wait too. The spinner is not a control
   * anyone would guess is clickable — a square stop sitting next to it is the
   * only affordance that reads as "give up on this". `own` covers the moment
   * before the player has adopted the clip: the request is already running
   * then, so it must already be cancellable. */
  const isActive = playing || paused || buffering || (!!own && !own.settled)
  const shownProgress = mine ? progress : 0

  return (
    <div className="group flex items-center gap-1">
      <button
        className={`${btnClass} ${isActive ? 'text-foreground' : ''} ${failed ? 'text-destructive' : ''}`}
        onClick={handleToggle}
        disabled={waiting}
        title={
          waiting
            ? t('chat.audio_generating')
            : failed
              ? t('chat.audio_failed')
              : playing
                ? t('chat.audio_pause')
                : paused
                  ? t('chat.audio_resume')
                  : clip
                    ? t('chat.audio_listen')
                    : t('chat.audio_play')
        }
        onDoubleClick={(e) => e.preventDefault()}
      >
        {waiting ? (
          <Loader2 className={`${iconSize} animate-spin`} />
        ) : playing ? (
          <Pause className={iconSize} />
        ) : (
          <Volume2 className={iconSize} />
        )}
      </button>
      <div
        ref={barRef}
        className={`h-1.5 bg-secondary rounded-full cursor-pointer flex-shrink-0 overflow-hidden transition-all duration-300 ease-out ${isActive ? 'w-24 opacity-100' : 'w-0 opacity-0'}`}
        onClick={handleBarClick}
      >
        <div
          className="h-full bg-primary rounded-full"
          style={{ width: `${shownProgress}%` }}
        />
      </div>
      {/* Kept mounted and collapsed rather than unmounted, so appearing does not
          shove the progress bar sideways — same trick the bar itself uses. */}
      <button
        className={`${btnClass} overflow-hidden transition-all duration-300 ease-out ${
          isActive ? 'opacity-100' : 'w-0 p-0 opacity-0 pointer-events-none'
        }`}
        onClick={handleStop}
        title={t('chat.audio_stop')}
        tabIndex={isActive ? 0 : -1}
        aria-hidden={!isActive}
      >
        <Square className={iconSize} />
      </button>
    </div>
  )
}

function UserCopyAction({ content }: { content: string }) {
  const { t } = useTranslation()
  const { copied, handleCopy } = useCopy(content)

  return (
    <div className="flex justify-end mt-0.5 opacity-0 group-hover:opacity-100">
      <button
        className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary/60"
        onClick={handleCopy}
        title={t('common.copy')}
      >
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      </button>
    </div>
  )
}
