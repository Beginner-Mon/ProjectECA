/**
 * Terms of Service and Privacy Policy (SCRUM-41), in both site languages.
 *
 * The text lives in the .md files next to this one so it can be read and
 * reviewed as prose. What it says about data must stay true of the code: if you
 * add a third party that receives user data, change how long something is kept,
 * or add a way to delete data, update privacy.en.md AND privacy.vi.md in the
 * same change. legal.test.ts checks the two languages keep the same sections.
 */

import type { Locale } from '../i18n/locale'
import privacyEn from './privacy.en.md?raw'
import privacyVi from './privacy.vi.md?raw'
import termsEn from './terms.en.md?raw'
import termsVi from './terms.vi.md?raw'

export type LegalDoc = 'terms' | 'privacy'

export const LEGAL_DOCS: readonly LegalDoc[] = ['terms', 'privacy']

/** Where users send access and deletion requests. The team's address, as on About Us. */
export const LEGAL_CONTACT_EMAIL = 'contact@ordinary.studio'

/** Bump when either document changes in substance. */
export const LEGAL_EFFECTIVE_DATE: Record<Locale, string> = {
  en: '29 October 2026',
  vi: '29/10/2026',
}

const SOURCES: Record<LegalDoc, Record<Locale, string>> = {
  terms: { en: termsEn, vi: termsVi },
  privacy: { en: privacyEn, vi: privacyVi },
}

export function legalMarkdown(doc: LegalDoc, locale: Locale): string {
  return SOURCES[doc][locale]
    .replaceAll('{CONTACT_EMAIL}', LEGAL_CONTACT_EMAIL)
    .replaceAll('{EFFECTIVE_DATE}', LEGAL_EFFECTIVE_DATE[locale])
}

/** The public URL of each document, readable before signing in. */
export const LEGAL_PATH: Record<LegalDoc, string> = {
  terms: '/terms',
  privacy: '/privacy',
}
