/**
 * Voice input as dictation: speech becomes text in the ordinary chat box, where
 * the user can read it, fix it, and send it like anything typed.
 *
 * Engine: the browser's Web Speech API (`SpeechRecognition`). No backend.
 *
 * PRIVACY — decide before relying on this in production. Chrome and Edge send
 * the audio to Google / Microsoft servers to transcribe it, Safari to Apple.
 * In a health app that means spoken symptoms leave our system. A self-hosted
 * engine (e.g. Whisper behind our own endpoint) would replace `useDictation`'s
 * recogniser only: everything in this file and the chat UI stays the same.
 *
 * Support: Chrome, Edge, Safari. Not Firefox. Not the Android WebView the
 * Capacitor app runs in (that needs a native speech plugin).
 */

import type { Locale } from '../i18n/locale'

/** Join what the user typed with what has been heard so far, one space apart. */
export function composeDictation(base: string, finals: string, interim: string): string {
  return [base, finals, interim]
    .map((part) => part.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join(' ')
}

export function recognitionLang(locale: Locale): string {
  return locale === 'vi' ? 'vi-VN' : 'en-US'
}

/**
 * i18n key for a recogniser error, or null when it is not an error to show.
 * `aborted` is what our own `abort()` produces (unmount, new session) — the
 * user did that, so there is nothing to tell them.
 */
export function dictationErrorKey(code: string): string | null {
  switch (code) {
    case 'aborted':
      return null
    case 'not-allowed':
    case 'service-not-allowed':
      return 'chat.dictation_denied'
    case 'audio-capture':
      return 'chat.dictation_no_mic'
    case 'no-speech':
      return 'chat.dictation_no_speech'
    case 'network':
      return 'chat.dictation_network'
    case 'unsupported':
      return 'chat.dictation_unsupported'
    default:
      return 'chat.dictation_failed'
  }
}

// ── Minimal typing for the recogniser ──────────────────────────────────────
// lib.dom ships the standard interface but not the `webkit`-prefixed
// constructor Safari and older Chrome expose, so describe just what we use.

export interface RecognitionResultList {
  length: number
  [index: number]: { isFinal: boolean; 0: { transcript: string } }
}

export interface Recognition {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: { results: RecognitionResultList }) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

type RecognitionCtor = new () => Recognition

export function getRecognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** Split a result list into the settled text and the part still being guessed. */
export function readResults(results: RecognitionResultList): { finals: string; interim: string } {
  let finals = ''
  let interim = ''
  for (let i = 0; i < results.length; i++) {
    const text = results[i][0].transcript
    if (results[i].isFinal) finals += ` ${text}`
    else interim += ` ${text}`
  }
  return { finals, interim }
}

/**
 * Configure a recogniser for dictation and wire its events. Kept out of the
 * React hook so it can be tested with a fake recogniser in plain Node.
 */
export function configureRecognition(
  rec: Recognition,
  opts: {
    locale: Locale
    /** Text already in the box when dictation started; kept in front. */
    base: string
    onText: (text: string) => void
    onError: (code: string) => void
    onEnd: () => void
  },
): Recognition {
  rec.lang = recognitionLang(opts.locale)
  rec.continuous = true
  rec.interimResults = true
  rec.onresult = (e) => {
    const { finals, interim } = readResults(e.results)
    opts.onText(composeDictation(opts.base, finals, interim))
  }
  rec.onerror = (e) => opts.onError(e.error)
  rec.onend = () => opts.onEnd()
  return rec
}
