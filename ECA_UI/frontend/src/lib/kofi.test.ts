import { describe, expect, it } from 'vitest'
import { kofiUrl } from './kofi'

describe('kofiUrl', () => {
  it('accepts a creator page on ko-fi.com', () => {
    expect(kofiUrl('https://ko-fi.com/ecaassistant')).toBe('https://ko-fi.com/ecaassistant')
    expect(kofiUrl('  https://www.ko-fi.com/eca  ')).toBe('https://www.ko-fi.com/eca')
  })

  it('shows nothing when unset or blank', () => {
    expect(kofiUrl(undefined)).toBeNull()
    expect(kofiUrl('')).toBeNull()
    expect(kofiUrl('   ')).toBeNull()
  })

  it('rejects anything that is not a https ko-fi.com creator page', () => {
    expect(kofiUrl('http://ko-fi.com/eca')).toBeNull()
    expect(kofiUrl('https://ko-fi.com/')).toBeNull()
    expect(kofiUrl('https://evil.example/ko-fi.com/eca')).toBeNull()
    expect(kofiUrl('https://ko-fi.com.evil.example/eca')).toBeNull()
    expect(kofiUrl('javascript:alert(1)')).toBeNull()
    expect(kofiUrl('not a url')).toBeNull()
  })
})
