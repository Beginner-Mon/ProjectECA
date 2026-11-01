import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { fetchAuthSession } from 'aws-amplify/auth'
import { ArrowLeft, RotateCcw, Search, Star, Trash2 } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import ConfirmDialog from '../components/ui/confirm-dialog'
import {
  adminAuditLog,
  adminCreatePlan,
  adminDeletePlan,
  adminFindUsers,
  adminListPlans,
  adminResetUsage,
  adminUpdatePlan,
  adminUpdateUser,
  type AdminUserRow,
  type AuditEntry,
  type Plan,
} from '../lib/api'
import { PLAN_NAME_RE, formatTokens, isAdminToken, parseLimit } from '../lib/quota'
import { errorMessage } from '../lib/errors'

/**
 * Tiers and daily token quota (docs/plans/roles-and-quota.md).
 *
 * Reached from the profile's Usage section, which only shows the link to
 * members of the Cognito `admin` group. That is presentation, not protection:
 * every request below is checked by api/routes_quota.py (require_admin) and by
 * migration 018's row-level policies. Who IS an admin is changed in the
 * Cognito console only — there is deliberately no control for it here.
 */

/** The server's own `detail` when it sent one (409 "users are on this tier"…). */
function apiErrorText(e: unknown): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return errorMessage(e)
}

/** `obj` without `key` — drops a draft once it is saved. */
function without<T>(obj: Record<string, T>, key: string): Record<string, T> {
  const next = { ...obj }
  delete next[key]
  return next
}

const card = 'rounded-xl border border-border/40 bg-card p-4 space-y-3'
const cell = 'px-2 py-2 text-sm'

export default function AdminPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const locale = i18n.language
  const [allowed, setAllowed] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [plans, setPlans] = useState<Plan[]>([])
  const [limitDrafts, setLimitDrafts] = useState<Record<string, string>>({})
  const [newName, setNewName] = useState('')
  const [newLimit, setNewLimit] = useState('')
  const [planToDelete, setPlanToDelete] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [users, setUsers] = useState<AdminUserRow[] | null>(null)
  const [overrideDrafts, setOverrideDrafts] = useState<Record<string, string>>({})
  const [userToReset, setUserToReset] = useState<AdminUserRow | null>(null)

  const [audit, setAudit] = useState<AuditEntry[]>([])

  const run = useCallback(async (fn: () => Promise<void>) => {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(apiErrorText(e))
    }
  }, [])

  const loadPlans = useCallback(() => run(async () => setPlans(await adminListPlans())), [run])
  const loadAudit = useCallback(() => run(async () => setAudit(await adminAuditLog())), [run])

  useEffect(() => {
    fetchAuthSession()
      .then((s) => setAllowed(isAdminToken(s.tokens?.idToken?.payload as Record<string, unknown>)))
      .catch(() => setAllowed(false))
  }, [])

  useEffect(() => {
    if (!allowed) return
    let alive = true
    Promise.all([adminListPlans(), adminAuditLog()])
      .then(([p, a]) => { if (alive) { setPlans(p); setAudit(a) } })
      .catch((e: unknown) => { if (alive) setError(apiErrorText(e)) })
    return () => { alive = false }
  }, [allowed])

  const afterChange = async () => {
    await loadPlans()
    await loadAudit()
  }

  const saveLimit = (plan: Plan) => run(async () => {
    const limit = parseLimit(limitDrafts[plan.name] ?? '')
    if (limit === null) throw new Error(t('admin.invalid_limit'))
    await adminUpdatePlan(plan.name, { daily_token_limit: limit })
    setLimitDrafts((d) => without(d, plan.name))
    await afterChange()
  })

  const makeDefault = (plan: Plan) => run(async () => {
    await adminUpdatePlan(plan.name, { is_default: true })
    await afterChange()
  })

  const createPlan = () => run(async () => {
    const name = newName.trim()
    const limit = parseLimit(newLimit)
    if (!PLAN_NAME_RE.test(name)) throw new Error(t('admin.invalid_name'))
    if (limit === null) throw new Error(t('admin.invalid_limit'))
    await adminCreatePlan(name, limit)
    setNewName('')
    setNewLimit('')
    await afterChange()
  })

  const deletePlan = (name: string) => run(async () => {
    await adminDeletePlan(name)
    await afterChange()
  })

  const search = () => run(async () => setUsers(await adminFindUsers(query)))

  const replaceUser = (row: AdminUserRow) =>
    setUsers((list) => list?.map((u) => (u.user_id === row.user_id ? row : u)) ?? null)

  const changeTier = (user: AdminUserRow, planName: string) => run(async () => {
    replaceUser(await adminUpdateUser(user.user_id, { plan_name: planName }))
    await afterChange()
  })

  const saveOverride = (user: AdminUserRow) => run(async () => {
    const draft = (overrideDrafts[user.user_id] ?? '').trim()
    const row = draft === ''
      ? await adminUpdateUser(user.user_id, { clear_limit_override: true })
      : await (async () => {
        const limit = parseLimit(draft)
        if (limit === null) throw new Error(t('admin.invalid_limit'))
        return adminUpdateUser(user.user_id, { limit_override: limit })
      })()
    replaceUser(row)
    setOverrideDrafts((d) => without(d, user.user_id))
    await loadAudit()
  })

  const resetUsage = (user: AdminUserRow) => run(async () => {
    replaceUser(await adminResetUsage(user.user_id))
    await loadAudit()
  })

  if (allowed === null) return null

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon-sm" onClick={() => navigate('/')} aria-label={t('admin.back')}>
            <ArrowLeft />
          </Button>
          <h1 className="text-lg font-semibold">{t('admin.title')}</h1>
        </div>

        {!allowed ? (
          <p className="text-sm text-muted-foreground">{t('admin.not_admin')}</p>
        ) : (
          <>
            {error && (
              <p role="alert" className="text-sm text-destructive bg-destructive/10 rounded-lg px-3 py-2">{error}</p>
            )}

            {/* ── Tiers ───────────────────────────────────────────── */}
            <section className={card}>
              <div>
                <h2 className="text-base font-semibold">{t('admin.tiers')}</h2>
                <p className="text-sm text-muted-foreground">{t('admin.tiers_desc')}</p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground">
                      <th className={cell}>{t('admin.tier')}</th>
                      <th className={cell}>{t('admin.daily_limit')}</th>
                      <th className={cell}>{t('admin.users')}</th>
                      <th className={cell} />
                    </tr>
                  </thead>
                  <tbody>
                    {plans.map((p) => (
                      <tr key={p.name} className="border-t border-border/30">
                        <td className={cell}>
                          <span className="font-medium">{p.name}</span>
                          {p.is_default && (
                            <span className="ml-2 text-xs text-primary">{t('admin.default')}</span>
                          )}
                        </td>
                        <td className={cell}>
                          <div className="flex items-center gap-2">
                            <Input
                              className="h-8 w-32"
                              inputMode="numeric"
                              value={limitDrafts[p.name] ?? String(p.daily_token_limit)}
                              onChange={(e) => setLimitDrafts((d) => ({ ...d, [p.name]: e.target.value }))}
                              aria-label={t('admin.daily_limit')}
                            />
                            {limitDrafts[p.name] !== undefined && (
                              <Button size="sm" onClick={() => void saveLimit(p)}>{t('admin.save')}</Button>
                            )}
                          </div>
                        </td>
                        <td className={cell}>{p.users}</td>
                        <td className={`${cell} text-right whitespace-nowrap`}>
                          {!p.is_default && (
                            <>
                              <Button variant="ghost" size="icon-sm" onClick={() => void makeDefault(p)}
                                title={t('admin.make_default')} aria-label={t('admin.make_default')}>
                                <Star />
                              </Button>
                              <Button variant="ghost" size="icon-sm" onClick={() => setPlanToDelete(p.name)}
                                title={t('admin.delete')} aria-label={t('admin.delete')}>
                                <Trash2 />
                              </Button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-border/30">
                <Input className="h-8 w-36" placeholder={t('admin.new_tier_name')} value={newName}
                  onChange={(e) => setNewName(e.target.value)} aria-label={t('admin.new_tier_name')} />
                <Input className="h-8 w-36" inputMode="numeric" placeholder={t('admin.daily_limit')} value={newLimit}
                  onChange={(e) => setNewLimit(e.target.value)} aria-label={t('admin.daily_limit')} />
                <Button size="sm" onClick={() => void createPlan()}>{t('admin.add_tier')}</Button>
              </div>
            </section>

            {/* ── Users ───────────────────────────────────────────── */}
            <section className={card}>
              <div>
                <h2 className="text-base font-semibold">{t('admin.users_title')}</h2>
                <p className="text-sm text-muted-foreground">{t('admin.users_desc')}</p>
              </div>
              <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void search() }}>
                <Input className="h-8" placeholder={t('admin.search_email')} value={query}
                  onChange={(e) => setQuery(e.target.value)} aria-label={t('admin.search_email')} />
                <Button size="sm" type="submit"><Search />{t('admin.search')}</Button>
              </form>
              {users !== null && users.length === 0 && (
                <p className="text-sm text-muted-foreground">{t('admin.no_users')}</p>
              )}
              {users !== null && users.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-xs text-muted-foreground">
                        <th className={cell}>{t('admin.email')}</th>
                        <th className={cell}>{t('admin.tier')}</th>
                        <th className={cell}>{t('admin.personal_limit')}</th>
                        <th className={cell}>{t('admin.used_today')}</th>
                        <th className={cell} />
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.user_id} className="border-t border-border/30">
                          <td className={`${cell} break-all`}>{u.email ?? u.user_id}</td>
                          <td className={cell}>
                            <select
                              className="h-8 rounded-md border border-border/40 bg-background px-2 text-sm"
                              value={u.plan}
                              onChange={(e) => void changeTier(u, e.target.value)}
                              aria-label={t('admin.tier')}
                            >
                              {plans.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                            </select>
                          </td>
                          <td className={cell}>
                            <div className="flex items-center gap-2">
                              <Input
                                className="h-8 w-28"
                                inputMode="numeric"
                                placeholder={t('admin.from_tier')}
                                value={overrideDrafts[u.user_id] ?? (u.limit_override === null ? '' : String(u.limit_override))}
                                onChange={(e) => setOverrideDrafts((d) => ({ ...d, [u.user_id]: e.target.value }))}
                                aria-label={t('admin.personal_limit')}
                              />
                              {overrideDrafts[u.user_id] !== undefined && (
                                <Button size="sm" onClick={() => void saveOverride(u)}>{t('admin.save')}</Button>
                              )}
                            </div>
                          </td>
                          <td className={`${cell} whitespace-nowrap`}>
                            {formatTokens(u.used_today, locale)} / {formatTokens(u.limit, locale)}
                          </td>
                          <td className={`${cell} text-right`}>
                            <Button variant="ghost" size="icon-sm" onClick={() => setUserToReset(u)}
                              title={t('admin.reset_usage')} aria-label={t('admin.reset_usage')}>
                              <RotateCcw />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* ── Audit ───────────────────────────────────────────── */}
            <section className={card}>
              <h2 className="text-base font-semibold">{t('admin.audit')}</h2>
              {audit.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t('admin.audit_empty')}</p>
              ) : (
                <ul className="space-y-1">
                  {audit.map((a, i) => (
                    <li key={`${a.at}-${i}`} className="text-xs text-muted-foreground break-all">
                      <span className="text-foreground">{new Date(a.at).toLocaleString(locale)}</span>
                      {' · '}{a.actor ?? '?'}{' · '}<span className="font-mono">{a.action}</span>
                      {' · '}{a.target}{' · '}<span className="font-mono">{JSON.stringify(a.detail)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>

      <ConfirmDialog
        open={planToDelete !== null}
        title={t('admin.delete_title')}
        message={t('admin.delete_message', { tier: planToDelete ?? '' })}
        confirmLabel={t('admin.delete')}
        cancelLabel={t('admin.cancel')}
        variant="danger"
        onConfirm={() => { const name = planToDelete; setPlanToDelete(null); if (name) void deletePlan(name) }}
        onCancel={() => setPlanToDelete(null)}
      />
      <ConfirmDialog
        open={userToReset !== null}
        title={t('admin.reset_title')}
        message={t('admin.reset_message', { email: userToReset?.email ?? userToReset?.user_id ?? '' })}
        confirmLabel={t('admin.reset_usage')}
        cancelLabel={t('admin.cancel')}
        onConfirm={() => { const u = userToReset; setUserToReset(null); if (u) void resetUsage(u) }}
        onCancel={() => setUserToReset(null)}
      />
    </div>
  )
}
