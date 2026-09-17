import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { withGreeting, resolveGreeting, type CapturedGreeting } from './greeting'
import { FALLBACK_UI_STRINGS, getGreetingForSlot, getTimeSlot } from './characterCopy'

const G = '1'

type Msg = { id: string; content: string }

const greeting = (content: string): Msg => ({ id: G, content })
const said = (content: string): Msg => ({ id: 'x', content })

describe('withGreeting', () => {
  it('rewrites the opening line while it is the only thing on screen', () => {
    const out = withGreeting([greeting('Chào buổi sáng!')], G, 'Good morning!')
    expect(out.map((m) => m.content)).toEqual(['Good morning!'])
  })

  it('leaves it alone once a conversation exists', () => {
    // The rule the character-switch path already follows: rewriting a line that
    // has been sitting above real messages makes the screen claim something was
    // said that never was.
    const before = [greeting('Chào buổi sáng!'), said('tôi bị đau lưng')]
    expect(withGreeting(before, G, 'Good morning!')).toBe(before)
  })

  it('returns the SAME array when nothing would change', () => {
    // Identity, not just equality: this feeds setMessages, and a fresh array
    // re-renders the whole transcript on every locale read for no reason.
    const before = [greeting('Good morning!')]
    expect(withGreeting(before, G, 'Good morning!')).toBe(before)
  })

  it('leaves a transcript whose first line is not the greeting alone', () => {
    const before = [said('hello'), said('hi')]
    expect(withGreeting(before, G, 'Good morning!')).toBe(before)
  })

  it('handles an empty transcript without inventing a message', () => {
    const before: Msg[] = []
    expect(withGreeting(before, G, 'Good morning!')).toBe(before)
  })

  it('refuses to blank the greeting', () => {
    // A locale with no copy for this character resolves to '' upstream. Showing
    // an empty first bubble is worse than showing the previous language.
    const before = [greeting('Chào buổi sáng!')]
    expect(withGreeting(before, G, '')).toBe(before)
    expect(withGreeting(before, G, '   ')).toBe(before)
  })
})

/**
 * The bug this closes: the DISPLAYED bubble was rewritten only from the two
 * "write" effects (avatar switch, locale switch) via `withGreeting`, but the
 * slot used to key the greeting's VOICE was a bare `getTimeSlot()` read at
 * render time — recomputed on every render, including one a keystroke
 * triggers. App opened at 11:58 (pristine, morning shown); user starts
 * typing at 12:01 — every keystroke re-renders ChatProvider, the live clock
 * now reads "afternoon", the key built from it changes, and the audio effect
 * plays the AFTERNOON clip over the MORNING text still on screen.
 *
 * `resolveGreeting` is the fix: it is the only function allowed to move a
 * caller's captured slot+text forward, and it only does so when `withGreeting`
 * actually rewrote the bubble. A "render" that merely rebuilds a candidate
 * from a fresher clock read — without going through a write effect — must get
 * the SAME captured value back, byte-for-byte.
 */
describe('resolveGreeting — slot frozen with the displayed text', () => {
  const ui = FALLBACK_UI_STRINGS.en

  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('captures slot+text together on the first write (mount)', () => {
    vi.setSystemTime(new Date(2026, 0, 1, 11, 58))
    const slot = getTimeSlot()
    const candidate: CapturedGreeting = { slot, text: getGreetingForSlot(ui, slot) }

    const { messages, captured } = resolveGreeting([greeting('placeholder')], G, candidate, null)

    expect(captured).toEqual(candidate)
    expect(messages[0].content).toBe(candidate.text)
    expect(captured.slot).toBe('morning')
  })

  it('a clock crossing a slot boundary does not move the capture unless a write effect re-runs', () => {
    vi.setSystemTime(new Date(2026, 0, 1, 11, 58))
    const morningSlot = getTimeSlot()
    const morning: CapturedGreeting = { slot: morningSlot, text: getGreetingForSlot(ui, morningSlot) }
    const mounted = resolveGreeting([greeting('placeholder')], G, morning, null)
    expect(mounted.captured.slot).toBe('morning')

    // The clock crosses into the afternoon slot — this is what a keystroke's
    // re-render would observe if it read the clock. Nothing here calls
    // resolveGreeting again (no avatar switch, no locale switch), which is
    // exactly what a bare re-render must do: nothing.
    vi.setSystemTime(new Date(2026, 0, 1, 12, 1))

    // The state a render would read is untouched: same object, same slot,
    // same text — the text passed to playback would equal what's displayed.
    expect(mounted.captured.slot).toBe('morning')
    expect(mounted.messages[0].content).toBe(mounted.captured.text)
    expect(mounted.captured.text).toBe(morning.text)
  })

  it('a genuine write effect (avatar/locale switch) while pristine moves slot and text together, never one without the other', () => {
    vi.setSystemTime(new Date(2026, 0, 1, 11, 58))
    const morningSlot = getTimeSlot()
    const morning: CapturedGreeting = { slot: morningSlot, text: getGreetingForSlot(ui, morningSlot) }
    const mounted = resolveGreeting([greeting('placeholder')], G, morning, null)

    vi.setSystemTime(new Date(2026, 0, 1, 12, 1))
    const afternoonSlot = getTimeSlot()
    const afternoon: CapturedGreeting = { slot: afternoonSlot, text: getGreetingForSlot(ui, afternoonSlot) }
    const switched = resolveGreeting(mounted.messages, G, afternoon, mounted.captured)

    expect(switched.captured.slot).toBe('afternoon')
    expect(switched.messages[0].content).toBe(switched.captured.text)
    expect(switched.captured.text).not.toBe(morning.text)
  })

  it('once real history exists, the bubble and the captured slot are both frozen — a later candidate never moves either', () => {
    const morning: CapturedGreeting = { slot: 'morning', text: 'Good morning!' }
    const afternoon: CapturedGreeting = { slot: 'afternoon', text: 'Good afternoon!' }
    const withHistory = [greeting('Good morning!'), said('tôi bị đau lưng')]

    const { messages, captured } = resolveGreeting(withHistory, G, afternoon, morning)

    expect(messages).toBe(withHistory) // withGreeting refuses — this is a transcript now
    expect(captured).toBe(morning) // the key must not drift either
  })
})
