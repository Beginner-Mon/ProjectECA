import { describe, expect, it } from 'vitest'
import { LOCALES } from '../i18n/locale'
import { LEGAL_CONTACT_EMAIL, LEGAL_DOCS, LEGAL_PATH, legalMarkdown } from '.'

const sections = (md: string) => md.split('\n').filter((l) => l.startsWith('## ')).length

describe('legal documents (SCRUM-41)', () => {
  it('has every document in every site language', () => {
    for (const doc of LEGAL_DOCS) {
      for (const locale of LOCALES) {
        expect(legalMarkdown(doc, locale).length, `${doc}.${locale}`).toBeGreaterThan(500)
      }
    }
  })

  it('keeps English and Vietnamese in step, section for section', () => {
    for (const doc of LEGAL_DOCS) {
      expect(sections(legalMarkdown(doc, 'vi')), doc).toBe(sections(legalMarkdown(doc, 'en')))
    }
  })

  it('fills every placeholder', () => {
    for (const doc of LEGAL_DOCS) {
      for (const locale of LOCALES) {
        const md = legalMarkdown(doc, locale)
        expect(md, `${doc}.${locale}`).not.toMatch(/\{[A-Z_]+\}/)
        expect(md).toContain(LEGAL_CONTACT_EMAIL)
      }
    }
  })

  it('names every third party that receives user data, in both languages', () => {
    // If one of these stops being true, change the policy; if a new one starts
    // receiving data, add it here and to privacy.*.md.
    for (const locale of LOCALES) {
      const md = legalMarkdown('privacy', locale)
      for (const party of ['OpenAI', 'DeepSeek', 'Google Gemini', 'Amazon Web Services', 'Neon', 'Ko-fi']) {
        expect(md, `${party} in privacy.${locale}`).toContain(party)
      }
    }
  })

  it('tells users ECA is not medical care and to call 115 in an emergency', () => {
    for (const locale of LOCALES) {
      expect(legalMarkdown('terms', locale)).toContain('115')
    }
  })

  it('serves each document at its own public path', () => {
    expect(LEGAL_PATH).toEqual({ terms: '/terms', privacy: '/privacy' })
  })
})
