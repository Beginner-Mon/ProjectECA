/**
 * Daily token quota — pure helpers for the profile usage block and the admin
 * page (docs/plans/roles-and-quota.md). No DOM, no axios: tested in node.
 */

import type { QuotaStatus } from './api'

/**
 * Does this ID token carry the Cognito `admin` group?
 *
 * Only decides what the UI SHOWS. The backend checks the same claim on every
 * admin route and the database checks it again; a user who edits this in the
 * browser sees an admin page whose every request answers 403.
 */
export function isAdminToken(payload: Record<string, unknown> | null | undefined): boolean {
  const groups = payload?.['cognito:groups']
  return Array.isArray(groups) && groups.includes('admin')
}

/** 0..1 share of today's limit used; 0 for an unlimited account. */
export function usageFraction(status: Pick<QuotaStatus, 'limit' | 'used'>): number {
  if (status.limit === null) return 0
  if (status.limit <= 0) return 1
  return Math.min(1, Math.max(0, status.used / status.limit))
}

/** "12,345" in the user's locale. */
export function formatTokens(n: number, locale?: string): string {
  return new Intl.NumberFormat(locale).format(n)
}

/** A positive whole number from a text field, or null. */
export function parseLimit(text: string): number | null {
  const trimmed = text.trim().replace(/[,\s_.]/g, '')
  if (!/^\d+$/.test(trimmed)) return null
  const n = Number(trimmed)
  return Number.isSafeInteger(n) ? n : null
}

/** Tier names the backend accepts (routes_quota.PLAN_NAME_PATTERN). */
export const PLAN_NAME_RE = /^[a-z0-9_-]{1,32}$/
