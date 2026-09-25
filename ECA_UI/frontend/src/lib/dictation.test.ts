import { describe, expect, it } from 'vitest'
import { composeDictation, configureRecognition, dictationErrorKey, recognitionLang, type Recognition } from './dictation'

describe('composeDictation — spoken words land in the text box', () => {
  it('fills an empty box with what was said', () => {
    expect(composeDictation('', 'đau vai phải', '')).toBe('đau vai phải')
  })

  it('appends to text the user had already typed, with one space', () => {
    expect(composeDictation('Tôi bị', 'đau lưng', '')).toBe('Tôi bị đau lưng')
    expect(composeDictation('Tôi bị ', 'đau lưng', '')).toBe('Tôi bị đau lưng')
  })

  it('shows the words still being recognised after the settled ones', () => {
    expect(composeDictation('', 'my knee', 'hurts when')).toBe('my knee hurts when')
  })

  it('collapses the stray whitespace recognisers put between segments', () => {
    expect(composeDictation('', ' one  two ', '  three ')).toBe('one two three')
  })

  it('leaves the typed text untouched when nothing has been heard yet', () => {
    expect(composeDictation('Xin chào', '', '')).toBe('Xin chào')
  })
})

describe('recognitionLang', () => {
  it('maps the site locale to a recogniser language', () => {
    expect(recognitionLang('vi')).toBe('vi-VN')
    expect(recognitionLang('en')).toBe('en-US')
  })
})

describe('dictationErrorKey', () => {
  it('maps browser error codes to translated messages', () => {
    expect(dictationErrorKey('not-allowed')).toBe('chat.dictation_denied')
    expect(dictationErrorKey('service-not-allowed')).toBe('chat.dictation_denied')
    expect(dictationErrorKey('audio-capture')).toBe('chat.dictation_no_mic')
    expect(dictationErrorKey('no-speech')).toBe('chat.dictation_no_speech')
    expect(dictationErrorKey('network')).toBe('chat.dictation_network')
    expect(dictationErrorKey('unsupported')).toBe('chat.dictation_unsupported')
  })

  it('never surfaces a user-initiated stop as an error', () => {
    expect(dictationErrorKey('aborted')).toBeNull()
  })

  it('falls back to a generic message for anything unexpected', () => {
    expect(dictationErrorKey('language-not-supported')).toBe('chat.dictation_failed')
    expect(dictationErrorKey('something-new')).toBe('chat.dictation_failed')
  })
})

function fakeRecognition(): Recognition {
  return {
    lang: '', continuous: false, interimResults: false,
    onresult: null, onerror: null, onend: null,
    start() {}, stop() {}, abort() {},
  }
}

function results(...parts: Array<[string, boolean]>) {
  const list = parts.map(([transcript, isFinal]) => ({ isFinal, 0: { transcript } }))
  return { results: Object.assign(list, { length: list.length }) }
}

describe('configureRecognition — what the mic button actually wires up', () => {
  it('listens continuously, in the site language, with live partial results', () => {
    const rec = configureRecognition(fakeRecognition(), {
      locale: 'vi', base: '', onText: () => {}, onError: () => {}, onEnd: () => {},
    })
    expect(rec.lang).toBe('vi-VN')
    expect(rec.continuous).toBe(true)
    expect(rec.interimResults).toBe(true)
  })

  it('streams speech into the box after what was typed, settling as it goes', () => {
    const seen: string[] = []
    const rec = configureRecognition(fakeRecognition(), {
      locale: 'en', base: 'Hi,', onText: (t) => seen.push(t), onError: () => {}, onEnd: () => {},
    })
    rec.onresult!(results(['my shoulder', false]))
    rec.onresult!(results(['my shoulder hurts', true]))
    rec.onresult!(results(['my shoulder hurts', true], ['when I lift', false]))
    expect(seen).toEqual([
      'Hi, my shoulder',
      'Hi, my shoulder hurts',
      'Hi, my shoulder hurts when I lift',
    ])
  })

  it('reports errors and the end of listening', () => {
    const errors: string[] = []
    let ended = 0
    const rec = configureRecognition(fakeRecognition(), {
      locale: 'en', base: '', onText: () => {}, onError: (c) => errors.push(c), onEnd: () => { ended++ },
    })
    rec.onerror!({ error: 'not-allowed' })
    rec.onend!()
    expect(errors).toEqual(['not-allowed'])
    expect(ended).toBe(1)
  })
})
