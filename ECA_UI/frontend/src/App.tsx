import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import AuthGuard from './components/AuthGuard'
import MainLayout from './layouts/MainLayout'
import ChatPage from './pages/ChatPage'
import LoginPage from './pages/LoginPage'
import EnterPasswordPage from './pages/EnterPasswordPage'
import CreateAccountPage from './pages/CreateAccountPage'
import SetPasswordPage from './pages/SetPasswordPage'
import './App.css'

// Lazy: the two documents are ~25 KB of text most visits never open.
const LegalPage = lazy(() => import('./pages/LegalPage'))
// Lazy: only admins ever open it.
const AdminPage = lazy(() => import('./pages/AdminPage'))

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public */}
        <Route path="/login" element={<LoginPage />} />
        <Route path="/enter-password" element={<EnterPasswordPage />} />
        <Route path="/create-account" element={<CreateAccountPage />} />
        <Route path="/terms" element={<Suspense><LegalPage doc="terms" /></Suspense>} />
        <Route path="/privacy" element={<Suspense><LegalPage doc="privacy" /></Suspense>} />

        {/* Protected */}
        <Route element={<AuthGuard />}>
          <Route element={<MainLayout />}>
            <Route path="/" element={<ChatPage />} />
          </Route>
          <Route path="/set-password" element={<SetPasswordPage />} />
          <Route path="/admin" element={<Suspense><AdminPage /></Suspense>} />
        </Route>

        {/* Catch-all */}
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
