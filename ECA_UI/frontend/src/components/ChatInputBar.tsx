import { useState, useRef, useEffect, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUp, Mic, Square, Plus, Globe, Image, X, Volume2 } from 'lucide-react'
import TextareaAutosize from 'react-textarea-autosize'
import { useChat } from '../hooks/useChat'
import { dictationErrorKey } from '../lib/dictation'

/**
 * ChatInputBar — the message composer (textarea + attachments + mic + send).
 *
 * Extracted from ChatPanel so mobile can pin exactly this block to the
 * bottom of the screen while the message list lives in a toggleable sheet.
 * State comes from `useChat()` (ChatProvider), so the bar and any
 * ChatPanel share input/draft/recording state with no prop threading.
 */
export default function ChatInputBar({ embedded = false }: { embedded?: boolean }) {
  const {
    input,
    setInput,
    isGenerating,
    ui,
    webSearch,
    setWebSearch,
    voiceReply,
    setVoiceReply,
    handleSend,
    handleStop,
    imageUrls,
    addImage,
    removeImage,
    isRecording,
    recordingDuration,
    recordingError,
    dictationSupported,
    startRecord,
    stopRecord,
  } = useChat()

  const { t } = useTranslation()
  const recordingErrorKey = recordingError ? dictationErrorKey(recordingError) : null
  const recordingErrorText = recordingErrorKey ? t(recordingErrorKey) : null
  const [showAddMenu, setShowAddMenu] = useState(false)

  const addMenuRef = useRef<HTMLDivElement>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)

  /* close add menu on outside click */
  useEffect(() => {
    if (!showAddMenu) return
    const handler = (e: MouseEvent) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target as Node)) {
        setShowAddMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showAddMenu])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className={embedded ? 'bg-transparent shrink-0' : 'px-3 pb-3 pt-0 md:px-4 md:pb-4 md:pt-0 bg-transparent shrink-0'}>
      <div className={`mobile-chat-composer flex flex-col gap-0 md:gap-3 transition-colors relative ${embedded ? 'bg-transparent' : 'bg-white md:bg-transparent border border-border/40 rounded-2xl p-2 md:focus-within:ring-1 md:focus-within:ring-primary/50 md:focus-within:border-primary/50'}`}>
        {imageUrls.length > 0 && (
          <div className="flex gap-1.5 overflow-x-auto mx-1 p-2">
            {imageUrls.map((url, i) => (
              <div key={url} className="relative group shrink-0">
                <img
                  src={url}
                  alt={t('chat.attachment_preview_alt')}
                  className="w-20 h-20 rounded-lg object-cover"
                />
                <button
                  onClick={() => removeImage(i)}
                  className="absolute -top-1.5 -left-1.5 w-5 h-5 rounded-full bg-black/70 hover:bg-black flex items-center justify-center transition-colors opacity-0 group-hover:opacity-100 cursor-pointer"
                >
                  <X className="w-3 h-3 text-white" />
                </button>
              </div>
            ))}
          </div>
        )}
        <TextareaAutosize
          minRows={1}
          maxRows={6}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={ui.placeholder}
          disabled={isGenerating}
          className="w-full bg-transparent px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/70 resize-none focus:outline-none disabled:opacity-50"
        />

        <div className="flex items-center gap-1 shrink-0 pt-2 px-1 pb-1">
          <div className="relative" ref={addMenuRef}>
            <button
              onClick={() => setShowAddMenu((prev) => !prev)}
              title={t('common.add')}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors"
            >
              <Plus className="w-5 h-5" />
            </button>

            {showAddMenu && (
              <div className="absolute bottom-full left-0 mb-2 w-48 bg-card border border-border/50 rounded-xl shadow-xl overflow-hidden z-50 animate-panel-in">
                <button
                  onClick={() => { setWebSearch(!webSearch); setShowAddMenu(false) }}
                  className="w-full flex items-center gap-3 px-4 py-3 text-sm text-foreground hover:bg-secondary/60 transition-colors"
                >
                  <Globe className="w-4 h-4 text-muted-foreground" />
                  {t('chat.menu_search_online')}
                  {webSearch && <span className="ml-auto text-xs text-primary">{t('common.on')}</span>}
                </button>
                <div className="h-px bg-border/40 mx-3" />
                <button
                  onClick={() => { setVoiceReply(!voiceReply); setShowAddMenu(false) }}
                  className="w-full flex items-center gap-3 px-4 py-3 text-sm text-foreground hover:bg-secondary/60 transition-colors"
                >
                  <Volume2 className="w-4 h-4 text-muted-foreground" />
                  {t('chat.menu_voice_reply')}
                  {voiceReply && <span className="ml-auto text-xs text-primary">{t('common.on')}</span>}
                </button>
                <div className="h-px bg-border/40 mx-3" />
                <button
                  onClick={() => { imageInputRef.current?.click(); setShowAddMenu(false) }}
                  disabled={imageUrls.length >= 10}
                  className="w-full flex items-center gap-3 px-4 py-3 text-sm text-foreground hover:bg-secondary/60 transition-colors disabled:opacity-50"
                >
                  <Image className="w-4 h-4 text-muted-foreground" />
                  {t('chat.menu_media')}
                </button>
              </div>
            )}
          </div>

          {webSearch && (
            <div className="flex items-center gap-1 bg-secondary rounded-lg px-2 py-1 text-xs text-muted-foreground">
              <Globe className="w-3 h-3" />
              {t('chat.chip_web')}
              <button
                onClick={() => setWebSearch(false)}
                className="hover:text-foreground transition-colors cursor-pointer"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )}

          {voiceReply && (
            <div className="flex items-center gap-1 bg-secondary rounded-lg px-2 py-1 text-xs text-muted-foreground">
              <Volume2 className="w-3 h-3" />
              {t('chat.chip_voice')}
              <button
                onClick={() => setVoiceReply(false)}
                className="hover:text-foreground transition-colors cursor-pointer"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )}

          {isRecording && (
            <span className="text-xs text-destructive animate-pulse">● {String(Math.floor(recordingDuration / 60)).padStart(2, '0')}:{String(recordingDuration % 60).padStart(2, '0')}</span>
          )}
          {recordingErrorText && (
            <span className="text-xs text-destructive truncate max-w-[160px]" title={recordingErrorText}>{recordingErrorText}</span>
          )}

          <div className="flex-1" />

          <div className="flex items-center gap-1">
            <button
              title={
                !dictationSupported
                  ? t('chat.dictation_unsupported')
                  : isRecording ? t('chat.record_stop') : t('chat.record_start')
              }
              onClick={() => (isRecording ? stopRecord() : startRecord())}
              className={`p-2 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${isRecording ? 'bg-destructive text-destructive-foreground animate-pulse' : 'text-muted-foreground hover:text-foreground hover:bg-secondary/60'}`}
              disabled={isGenerating || !dictationSupported}
            >
              <Mic className="w-5 h-5" />
            </button>
            {isGenerating ? (
              <button
                onClick={handleStop}
                className="w-8 h-8 rounded-full bg-primary hover:bg-primary/90 flex items-center justify-center transition-all text-primary-foreground"
              >
                <Square className="w-4 h-4 fill-current" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="w-8 h-8 rounded-full bg-primary hover:bg-primary/90 disabled:opacity-30 disabled:cursor-not-allowed flex items-center justify-center transition-all text-primary-foreground"
              >
                <ArrowUp className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>
      <input
        ref={imageInputRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        onChange={(e) => {
          const files = e.target.files
          if (files) {
            const remaining = 10 - imageUrls.length
            for (let i = 0; i < Math.min(files.length, remaining); i++) {
              addImage(files[i])
            }
          }
          e.target.value = ''
        }}
      />
    </div>
  )
}
