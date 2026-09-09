import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { signUp, confirmSignUp, signIn } from 'aws-amplify/auth'
import { errorMessage } from '../lib/errors'
import { useRedirectIfAuthenticated } from '../hooks/useRedirectIfAuthenticated'
import { Loader2 } from 'lucide-react'
import AuthLayout from '../layouts/AuthLayout'
import { PasswordInput } from '../components/ui/password-input'

export default function CreateAccountPage() {
  const navigate = useNavigate()
  const location = useLocation()
  useRedirectIfAuthenticated()
  const email = (location.state as { email?: string } | null)?.email || ''

  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [step, setStep] = useState<'form' | 'verification' | 'success'>('form')
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSignUp = async () => {
    setError(null)
    if (password !== confirmPassword) { setError('Passwords do not match'); return }
    if (password.length < 8) { setError('Password must be at least 8 characters'); return }
    if (!displayName.trim()) { setError('Please enter your display name'); return }

    setLoading(true)
    try {
      await signUp({
        username: email,
        password,
        options: { userAttributes: { preferred_username: displayName.trim() } },
      })
      setStep('verification')
    } catch (err: unknown) {
      setError(errorMessage(err) || 'Failed to create account')
    } finally {
      setLoading(false)
    }
  }

  const handleConfirmSignUp = async () => {
    setError(null)
    if (!code.trim()) { setError('Please enter the verification code'); return }

    setLoading(true)
    try {
      await confirmSignUp({ username: email, confirmationCode: code.trim() })
      await signIn({ username: email, password })
      setStep('success')
      setTimeout(() => navigate('/'), 1500)
    } catch (err: unknown) {
      setError(errorMessage(err) || 'Failed to verify code')
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthLayout>
        {step === 'form' && (
          <>
            <div>
              <h1 className="text-2xl font-semibold text-foreground">Create your account</h1>
              <div className="text-sm text-muted-foreground mt-2 bg-secondary/40 rounded-lg px-4 py-3">
                <span className="text-foreground font-medium">{email}</span>
              </div>
            </div>

            {error && (
              <div className="text-sm text-destructive bg-destructive/10 py-2 px-3 rounded-lg">{error}</div>
            )}

            <div className="space-y-4">
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">Display Name</label>
                <input
                  type="text"
                  value={displayName}
                  onChange={e => setDisplayName(e.target.value)}
                  placeholder="Your display name"
                  className="w-full text-sm text-foreground bg-secondary/40 rounded-lg px-3 py-2.5 border border-border/30 outline-none focus:border-primary/50 transition-colors"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">Password</label>
                <PasswordInput
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Min. 8 characters"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1.5 block">Confirm password</label>
                <PasswordInput
                  value={confirmPassword}
                  onChange={e => setConfirmPassword(e.target.value)}
                  placeholder="Re-enter password"
                />
              </div>

              <button
                onClick={handleSignUp}
                disabled={loading}
                className="w-full px-4 py-2.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                Create Account
              </button>
            </div>

            {/* Sign-up is email-only: reaching this page means the user already
                chose to register with Cognito, so offering Google here asks a
                question that has been answered. Google sign-in lives on the
                login page, and linking Google to an existing account lives in
                Profile.

                The link out is not decoration. /create-account is reachable from
                two places on the login page, and one of them — the lookup-skipped
                branch (LoginPage:261) — cannot tell a new address from an
                existing one. Without this, a Google-only user who lands here
                meets UsernameExistsException with no route back. */}
            <p className="text-xs text-muted-foreground text-center">
              Already have an account?{' '}
              <button
                onClick={() => navigate('/login')}
                className="underline hover:text-foreground transition-colors"
              >
                Back to sign in
              </button>
            </p>
          </>
        )}

        {step === 'verification' && (
          <div className="space-y-4">
            <div>
              <h1 className="text-2xl font-semibold text-foreground">Check your email</h1>
              <p className="text-sm text-muted-foreground mt-2">
                A verification code has been sent to <strong className="text-foreground">{email}</strong>.
              </p>
            </div>
            {error && (
              <div className="text-sm text-destructive bg-destructive/10 py-2 px-3 rounded-lg">{error}</div>
            )}
            <div>
              <label className="text-xs text-muted-foreground mb-1.5 block">Verification code</label>
              <input
                type="text"
                value={code}
                onChange={e => setCode(e.target.value)}
                placeholder="6-digit code"
                autoFocus
                className="w-full text-sm text-foreground bg-secondary/40 rounded-lg px-3 py-2.5 border border-border/30 outline-none focus:border-primary/50 transition-colors"
              />
            </div>
            <button
              onClick={handleConfirmSignUp}
              disabled={loading}
              className="w-full px-4 py-2.5 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              Verify
            </button>
          </div>
        )}

        {step === 'success' && (
          <div className="text-center space-y-4">
            <h1 className="text-2xl font-semibold text-foreground">Account created</h1>
            <p className="text-sm text-muted-foreground">Redirecting...</p>
          </div>
        )}
    </AuthLayout>
  )
}
