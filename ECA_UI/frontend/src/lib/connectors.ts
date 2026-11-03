import type { ConnectorInfo } from './api'

/** The control the panel shows for one OAuth connector. */
export type ConnectorControl = 'connect' | 'disconnect' | 'reconnect'

/**
 * Parse the OAuth callback query string.
 *
 * `code` and `state` are required; `iss` is optional (the route accepts a
 * missing issuer and fails closed later if the AS advertises one). Returns an
 * `{ error: 'missing_parameter' }` shape instead of throwing so the caller can
 * show an error without calling the API. Never logs or returns the values —
 * callers treat them as opaque and strip them from the URL immediately.
 */
export function parseCallbackQuery(
  search: string,
): { code: string; state: string; iss: string } | { error: 'missing_parameter' } {
  const params = new URLSearchParams(search)
  const code = params.get('code')
  const state = params.get('state')
  const iss = params.get('iss') ?? ''
  if (!code || !state) return { error: 'missing_parameter' }
  return { code, state, iss }
}

/**
 * The one control to show for a connector, from its connection status:
 * no connection → Connect, active → Disconnect, auth_expired → Reconnect.
 * Anything else (off, unconfigured, …) → Connect.
 */
export function controlFor(connection: ConnectorInfo | null): ConnectorControl {
  if (!connection) return 'connect'
  if (connection.status === 'auth_expired') return 'reconnect'
  if (connection.status === 'active') return 'disconnect'
  return 'connect'
}
