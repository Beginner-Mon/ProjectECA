import { describe, expect, it } from 'vitest'
import { FALLBACK_UI_STRINGS } from './characterCopy'
import { stageLabelFor } from './stageLabel'

const vi = FALLBACK_UI_STRINGS.vi

describe('stageLabelFor — nhãn theo nguồn thật (plan T6)', () => {
  it('lúc gửi hiện thinking, không bao giờ là nhãn thư viện', () => {
    const label = stageLabelFor(null, vi, null)

    expect(label).toBe(vi.stage_thinking)
    expect(label).not.toBe(vi.stage_searching)
  })

  it('planner complete giữ nguyên nhãn hiện tại', () => {
    expect(
      stageLabelFor({ node: 'planner', status: 'complete' }, vi, vi.stage_thinking),
    ).toBe(vi.stage_thinking)
  })

  it('retriever gọi kb_search ra nhãn thư viện', () => {
    expect(
      stageLabelFor(
        { node: 'retriever_agent', status: 'complete', sources: ['library'] },
        vi,
        vi.stage_thinking,
      ),
    ).toBe(vi.stage_searching)
  })

  it('retriever gọi memory_search ra nhãn nhớ lại', () => {
    expect(
      stageLabelFor(
        { node: 'retriever_agent', status: 'complete', sources: ['memory'] },
        vi,
        vi.stage_thinking,
      ),
    ).toBe(vi.stage_recalling)
  })

  it('mỗi nguồn ra nhãn riêng (web, video)', () => {
    expect(
      stageLabelFor(
        { node: 'retriever_agent', status: 'complete', sources: ['web'] },
        vi,
        null,
      ),
    ).toBe(vi.stage_web)
    expect(
      stageLabelFor(
        { node: 'retriever_agent', status: 'complete', sources: ['video'] },
        vi,
        null,
      ),
    ).toBe(vi.stage_video)
  })

  it('retriever không gọi gì, hoặc synthesizer started, ra composing', () => {
    expect(
      stageLabelFor(
        { node: 'retriever_agent', status: 'complete', sources: [] },
        vi,
        vi.stage_thinking,
      ),
    ).toBe(vi.stage_composing)
    expect(
      stageLabelFor({ node: 'synthesizer', status: 'started' }, vi, vi.stage_searching),
    ).toBe(vi.stage_composing)
  })
})
