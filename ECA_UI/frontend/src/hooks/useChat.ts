import { createContext, useContext } from 'react'
import type { Message } from '../components/ChatMessage'
import type { UiStrings } from '../lib/characterCopy'
import type { MessageFeedback } from '../lib/api'

export interface SessionItem {
  session_id: string
  created_at: string
  updated_at: string
  first_user_message_preview: string
  message_count: number
}

/** Something the chat asks the app shell to do, because the shell owns the panels.
 *  `seq` makes every request distinct, so asking twice in a row acts twice. */
export interface ChatUiRequest {
  seq: number
  /** show_chat: a turn failed — open the chat dock if it is closed (mobile).
   *  llm_settings: open the key settings (Providers) from an error. */
  kind: 'show_chat' | 'llm_settings'
}

export interface ChatContextType {
  messages: Message[]
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
  input: string
  setInput: (value: string) => void
  isTyping: boolean
  isGenerating: boolean
  stageLabel: string | null
  /** Chat-surface copy for the selected character (greeting, placeholder,
   *  stage labels, error line). Always fully populated — see uiStringsFor. */
  ui: UiStrings
  webSearch: boolean
  setWebSearch: (value: boolean) => void
  voiceReply: boolean
  setVoiceReply: (value: boolean) => void
  isRestoring: boolean
  isSwitching: boolean
  startNewSession: () => void
  handleSend: () => Promise<void>
  handleStop: () => void
  imageUrls: string[]
  addImage: (file: File) => void
  removeImage: (index: number) => void
  sessionList: SessionItem[]
  sessionsDirty: boolean
  activeSessionId: string | null
  refreshSessions: () => Promise<void>
  switchToSession: (sessionId: string) => Promise<void>
  deleteSessionAction: (sessionId: string) => Promise<void>
  markSessionsClean: () => void
  /** Set (or clear, with `null`) one message's saved vote by its server id
   *  (`message.serverId`, a UUID) — the optimistic update AssistantActions
   *  makes before/after the feedback API. Keyed by serverId rather than the
   *  client `id`: restored/switched messages get positional client ids
   *  (`switched-3`) that repeat across sessions, so a client-id match could
   *  land on a different session's message after a late update resolves
   *  post-switch. A serverId is a real UUID, so a stale update from a
   *  session the user has since left simply matches nothing. */
  setMessageFeedback: (serverId: string, feedback: MessageFeedback | null) => void
  /** The assistant message that may offer "Try again": the newest one. */
  latestAssistantId: string | null
  /** Send the prompt behind a failed assistant message again, as a new turn. */
  retryTurn: (assistantMessageId: string) => void
  /** The latest request to the app shell (FloatingNavBar acts on it). */
  uiRequest: ChatUiRequest | null
  requestUi: (kind: ChatUiRequest['kind']) => void
  // Audio recording (frontend only, click toggle)
  /** Voice input (dictation) is listening. Speech lands in `input`. */
  isRecording: boolean
  recordingDuration: number
  /** Browser recogniser error code; show via dictationErrorKey (lib/dictation). */
  recordingError: string | null
  /** False in browsers without speech recognition (Firefox, Android WebView). */
  dictationSupported: boolean
  startRecord: () => void
  stopRecord: () => void
}

export const ChatContext = createContext<ChatContextType | null>(null)

export function useChat(): ChatContextType {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error('useChat must be used within ChatProvider')
  return ctx
}
