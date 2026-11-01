import { describe, expect, it } from 'vitest'
import { PLAN_NAME_RE, isAdminToken, parseLimit, usageFraction } from './quota'

describe('isAdminToken', () => {
  it('is true only for the admin Cognito group', () => {
    expect(isAdminToken({ 'cognito:groups': ['admin'] })).toBe(true)
    expect(isAdminToken({ 'cognito:groups': ['staff', 'admin'] })).toBe(true)
    expect(isAdminToken({ 'cognito:groups': ['admins'] })).toBe(false)
    expect(isAdminToken({ 'cognito:groups': 'admin' })).toBe(false)
    expect(isAdminToken({})).toBe(false)
    expect(isAdminToken(null)).toBe(false)
  })
})

describe('usageFraction', () => {
  it('is the share used, clamped to 0..1', () => {
    expect(usageFraction({ limit: 20000, used: 5000 })).toBe(0.25)
    expect(usageFraction({ limit: 20000, used: 30000 })).toBe(1)
    expect(usageFraction({ limit: 0, used: 0 })).toBe(1)
  })

  it('is 0 for an unlimited account', () => {
    expect(usageFraction({ limit: null, used: 999999 })).toBe(0)
  })
})

describe('parseLimit', () => {
  it('accepts whole numbers with separators', () => {
    expect(parseLimit('20000')).toBe(20000)
    expect(parseLimit(' 100,000 ')).toBe(100000)
    expect(parseLimit('0')).toBe(0)
  })

  it('rejects anything else', () => {
    expect(parseLimit('')).toBeNull()
    expect(parseLimit('-5')).toBeNull()
    expect(parseLimit('1e6')).toBeNull()
    expect(parseLimit('abc')).toBeNull()
  })
})

describe('PLAN_NAME_RE', () => {
  it('matches the backend rule', () => {
    expect(PLAN_NAME_RE.test('clinic')).toBe(true)
    expect(PLAN_NAME_RE.test('pro-2')).toBe(true)
    expect(PLAN_NAME_RE.test('Pro')).toBe(false)
    expect(PLAN_NAME_RE.test('has space')).toBe(false)
  })
})
