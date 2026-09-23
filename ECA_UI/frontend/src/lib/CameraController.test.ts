import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { CameraController } from './CameraController'

describe('CameraController manual override', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('auto: exercise -> hips -> idle -> head after 3s', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    expect(c.cameraMode).toBe('head')
    c.onStateChanged('exercise')
    expect(c.cameraMode).toBe('hips')
    expect(cb).toHaveBeenLastCalledWith('hips')
    c.onStateChanged('idle')
    // still hips during cooldown
    expect(c.cameraMode).toBe('hips')
    vi.advanceTimersByTime(3000)
    expect(c.cameraMode).toBe('head')
    c.dispose()
  })

  it('manual blocks auto', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.notifyManualInteraction()
    expect(c.cameraMode).toBe('manual')
    c.onStateChanged('exercise')
    expect(c.cameraMode).toBe('manual')
    // even after idle, still manual
    c.onStateChanged('idle')
    expect(c.cameraMode).toBe('manual')
    c.dispose()
  })

  it('manual auto-returns to head after 150s', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.notifyManualInteraction()
    expect(c.cameraMode).toBe('manual')
    vi.advanceTimersByTime(150_000)
    expect(c.cameraMode).toBe('head')
    expect(cb).toHaveBeenLastCalledWith('head')
    c.dispose()
  })

  it('manual timer resets on second interaction', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.notifyManualInteraction()
    vi.advanceTimersByTime(60_000)
    expect(c.cameraMode).toBe('manual')
    c.notifyManualInteraction() // reset
    vi.advanceTimersByTime(100_000)
    expect(c.cameraMode).toBe('manual')
    vi.advanceTimersByTime(50_000)
    expect(c.cameraMode).toBe('head')
    c.dispose()
  })

  it('setMode exits manual and clears timer', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.notifyManualInteraction()
    expect(c.isManual).toBe(true)
    c.setMode('hips')
    expect(c.cameraMode).toBe('hips')
    expect(c.isManual).toBe(false)
    vi.advanceTimersByTime(150_000)
    // should stay hips, not auto-return
    expect(c.cameraMode).toBe('hips')
    c.dispose()
  })

  it('gesture locks to face and returns to head', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.onStateChanged('gesture')
    expect(c.cameraMode).toBe('face')
    expect(c.isLocked).toBe(true)
    expect(cb).toHaveBeenLastCalledWith('face')
    c.onStateChanged('idle')
    expect(c.isLocked).toBe(false)
    expect(c.cameraMode).toBe('head')
    c.dispose()
  })

  it('gesture overrides manual, then hands the camera back to manual', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.notifyManualInteraction()
    expect(c.cameraMode).toBe('manual')
    c.onStateChanged('gesture')
    expect(c.cameraMode).toBe('face')
    c.onStateChanged('idle')
    expect(c.cameraMode).toBe('manual')
    // The manual idle timer restarts on hand-back, so it still auto-returns.
    vi.advanceTimersByTime(150_000)
    expect(c.cameraMode).toBe('head')
    c.dispose()
  })

  it('locked camera ignores user input and dev-panel presets', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.onStateChanged('gesture')
    c.notifyManualInteraction()
    expect(c.cameraMode).toBe('face')
    c.setMode('hips')
    expect(c.cameraMode).toBe('face')
    c.setMode('face') // never a user preset
    c.onStateChanged('idle')
    expect(c.cameraMode).toBe('head')
    c.setMode('face')
    expect(c.cameraMode).toBe('head')
    c.dispose()
  })

  it('lock entered during a hips cooldown cancels the cooldown', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.onStateChanged('exercise')
    c.onStateChanged('idle')
    expect(c.cameraMode).toBe('hips') // cooldown running
    c.onStateChanged('gesture')
    expect(c.cameraMode).toBe('face')
    vi.advanceTimersByTime(3000)
    expect(c.cameraMode).toBe('face') // cooldown did not fire underneath the lock
    c.onStateChanged('idle')
    expect(c.cameraMode).toBe('head')
    c.dispose()
  })

  it('manual cancels pending cooldown', () => {
    const cb = vi.fn()
    const c = new CameraController(cb)
    c.onStateChanged('exercise')
    expect(c.cameraMode).toBe('hips')
    c.onStateChanged('idle')
    expect(c.cameraMode).toBe('hips') // cooldown pending
    c.notifyManualInteraction()
    expect(c.cameraMode).toBe('manual')
    vi.advanceTimersByTime(3000)
    expect(c.cameraMode).toBe('manual') // cooldown should not fire
    c.dispose()
  })
})
