import { useCallback, useEffect, useRef, useState } from 'react'
import type { Locale } from '../i18n/locale'
import {
  configureRecognition,
  getRecognitionCtor,
  type Recognition,
} from '../lib/dictation'

/**
 * Voice input: dictate into the chat box. See lib/dictation.ts for the engine,
 * its browser support and the privacy note.
 *
 * `start(base, onText)` remembers what was already typed (`base`) and calls
 * `onText` with base + everything heard so far, live, including the words the
 * recogniser is still unsure of. `stop()` keeps what was said; the last
 * results arrive just after it and still land in the box.
 */
export function useDictation(locale: Locale) {
  const [isListening, setIsListening] = useState(false)
  const [duration, setDuration] = useState(0)
  /** Browser error code (e.g. 'not-allowed'); map with dictationErrorKey. */
  const [error, setError] = useState<string | null>(null)
  const supported = getRecognitionCtor() !== null

  const recRef = useRef<Recognition | null>(null)
  const timerRef = useRef<number | null>(null)

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const start = useCallback((base: string, onText: (text: string) => void) => {
    const Ctor = getRecognitionCtor()
    if (!Ctor) {
      setError('unsupported')
      return
    }
    recRef.current?.abort()
    setError(null)

    const rec: Recognition = configureRecognition(new Ctor(), {
      locale,
      base,
      onText,
      onError: setError,
      // Fires on stop(), on abort(), on an error, and when the browser gives up
      // after a stretch of silence — every way out, so the UI never sticks on
      // "listening".
      onEnd: () => {
        if (recRef.current === rec) recRef.current = null
        setIsListening(false)
        clearTimer()
      },
    })

    recRef.current = rec
    try {
      rec.start()
    } catch {
      // start() throws if a previous session has not released the mic yet.
      recRef.current = null
      setError('failed')
      return
    }
    setIsListening(true)
    setDuration(0)
    const t0 = Date.now()
    clearTimer()
    timerRef.current = window.setInterval(() => setDuration(Math.floor((Date.now() - t0) / 1000)), 250)
  }, [locale, clearTimer])

  const stop = useCallback(() => {
    recRef.current?.stop()
  }, [])

  /** Stop AND drop results still in flight — for when the text was just sent,
   *  so late words cannot refill the box that send cleared. */
  const cancel = useCallback(() => {
    recRef.current?.abort()
  }, [])

  // Leaving the page (or remounting) must release the microphone.
  useEffect(() => () => {
    recRef.current?.abort()
    recRef.current = null
    clearTimer()
  }, [clearTimer])

  return { supported, isListening, duration, error, start, stop, cancel }
}
