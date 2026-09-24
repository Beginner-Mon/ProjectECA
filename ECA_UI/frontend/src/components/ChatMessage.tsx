import { useState, useRef, useEffect, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, ThumbsUp, ThumbsDown, Volume2, Check, Pause, Square, Loader2 } from 'lucide-react'
import { DEFAULT_PERSONA_ID } from '../lib/api'
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
  /** Restored turns only: the motion this reply rendered, and when it dies.
   *  A live turn plays its motion straight from the SSE event and needs
   *  neither. */
  motionJobId?: string
  motionExpiresAt?: string
  /** What the user asked for, so the replay picker can label it. */
  motionLabel?: string
}

interface ChatMessageProps {
  message: Message
  isStreaming?: boolean
}

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
    /* Assistant message được tạo rỗng ngay lúc user gửi (ChatContext.tsx:364)
     * để id của nó bắt token từ stream. Chưa có text thì không render — cái
     * shell rỗng vẫn ăn py-3 + space-y-2 của container, đẩy loading indicator
     * xuống hơn 30px so với user message. */
    if (!message.content) return null

    const cleaned = message.content.replace(/<\/?evidence_citation>/g, '')
    return (
      <div className="px-3 md:px-5 py-1 md:py-3 animate-message-in w-full max-w-full">
        <div className="prose dark:prose-invert prose-p:leading-relaxed prose-strong:text-foreground prose-headings:text-foreground prose-pre:bg-secondary/50 prose-pre:border prose-pre:border-border/40 max-w-none text-[clamp(0.75rem,0.68rem+0.3vw,0.875rem)] text-foreground/90">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleaned}</ReactMarkdown>
        </div>
        {message.motionNotice && (
          // Muted and small: an aside about the 3D clip, not part of the
          // answer. It disappears on its own once the avatar starts moving.
          <p className="mt-1 text-[0.7rem] italic text-muted-foreground">
            {message.motionNotice}
          </p>
        )}
        <AssistantActions
          content={message.content}
          speech={message.speech}
          personaId={message.personaId}
          isStreaming={isStreaming}
        />
      </div>
    )
  }

  return (
    <div className="group flex px-2 md:px-4 py-0.5 md:py-1 animate-message-in flex-row-reverse w-full max-w-full">
      <div className="w-fit max-w-[80%]">
        {(message.content || !message.audioUrl) && (
          <div className="min-w-0 rounded-2xl px-2.5 md:px-3 py-1 md:py-2 text-[clamp(0.75rem,0.68rem+0.3vw,0.875rem)] leading-relaxed bg-primary text-primary-foreground">
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

function AssistantActions({
  content,
  speech,
  personaId,
  isStreaming,
}: {
  content: string
  speech?: SpeechClip
  personaId?: string
  isStreaming?: boolean
}) {
  const { t } = useTranslation()
  const { copied, handleCopy } = useCopy(content)
  const [liked, setLiked] = useState(false)
  const [disliked, setDisliked] = useState(false)

  if (isStreaming) return null

  const handleLike = () => {
    setLiked((v) => !v)
    if (!liked) setDisliked(false)
  }

  const handleDislike = () => {
    setDisliked((v) => !v)
    if (!disliked) setLiked(false)
  }

  const btnClass =
    'p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary/60'

  const iconSize = 'size-4'

  return (
    <div className="flex items-center gap-1 mt-1.5">
      <button className={btnClass} onClick={handleCopy} title={t('common.copy')}>
        {copied ? <Check className={iconSize} /> : <Copy className={iconSize} />}
      </button>
      <button className={btnClass} onClick={handleLike} title={t('chat.like')}>
        <ThumbsUp className={`${iconSize} ${liked ? 'text-green-500' : ''}`} />
      </button>
      <button className={btnClass} onClick={handleDislike} title={t('chat.dislike')}>
        <ThumbsDown className={`${iconSize} ${disliked ? 'text-blue-500' : ''}`} />
      </button>
      <AudioButton
        speech={speech}
        personaId={personaId}
        text={content}
        btnClass={btnClass}
        iconSize={iconSize}
      />
    </div>
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
  /** Looking in the cache / opening the request — before any clip exists. */
  const [opening, setOpening] = useState(false)
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
    opening ||
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
   * it — still loading, or ready to play. */
  useEffect(() => {
    const found = liveSpeech(text, personaId || selectedVrmId || DEFAULT_PERSONA_ID)
    if (found) setOwnClip(found)
  }, [text, personaId, selectedVrmId])

  const handleToggle = async () => {
    // Before any await: Safari and iOS WebViews start an AudioContext only
    // from inside the gesture's own call stack, and the play() below may be a
    // cache lookup away from this click.
    unlockSpeechAudio()

    if (buffering) {
      // The spinner is the cancel control while the wait is on: the separate
      // stop button only appears once there is sound to stop.
      speechPlayer.stop()
      cancelSpeech(text, personaId || selectedVrmId || DEFAULT_PERSONA_ID)
      setOwnClip(null)
      return
    }
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
    if (opening) return

    // Nothing in memory — the cache, then the network.
    setOpening(true)
    try {
      const fresh = await openSpeech(text, personaId || selectedVrmId || DEFAULT_PERSONA_ID)
      setOwnClip(fresh)
      // Straight away, even if nothing has arrived: the player schedules
      // chunks as they land, so the first one plays the moment it decodes.
      void speechPlayer.play(fresh, avatarRef.current)
    } finally {
      setOpening(false)
    }
  }

  /** Stop for good: unlike pause, the next click starts from the beginning. */
  const handleStop = () => {
    if (mine) speechPlayer.stop()
  }

  const handleBarClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const bar = barRef.current
    if (!mine || !bar) return
    const rect = bar.getBoundingClientRect()
    const fraction = (e.clientX - rect.left) / rect.width
    speechPlayer.seek(fraction)
    setProgress(fraction * 100)
  }

  const isActive = playing || paused
  const shownProgress = mine ? progress : 0

  return (
    <div className="group flex items-center gap-1">
      <button
        className={`${btnClass} ${isActive ? 'text-foreground' : ''} ${failed ? 'text-destructive' : ''}`}
        onClick={handleToggle}
        disabled={waiting && !buffering}
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
