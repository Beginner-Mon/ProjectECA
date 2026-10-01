/**
 * Nhãn trạng thái chat theo nguồn tra cứu thật (plan T6).
 *
 * Trước đây ChatContext đặt cứng `stage_searching` ("Đang tìm trong thư
 * viện...") cho MỌI lượt — kể cả "xin chào" không tra gì. Giờ backend gửi
 * kèm `sources` (id nguồn từ bảng sources.py) trong sự kiện stage của
 * retriever_agent, và hàm thuần này quyết định nhãn kế tiếp.
 */
import type { UiStrings } from './characterCopy'

export type StageEvent = {
  node: string
  status: string
  /** Source ids của backend (library | memory | web | video), giữ thứ tự gọi. */
  sources?: string[]
}

type StageCopyKey =
  | 'stage_searching'
  | 'stage_recalling'
  | 'stage_web'
  | 'stage_video'

const SOURCE_TO_KEY: Record<string, StageCopyKey> = {
  library: 'stage_searching',
  memory: 'stage_recalling',
  web: 'stage_web',
  video: 'stage_video',
}

export function stageLabelFor(
  event: StageEvent | null,
  copy: UiStrings,
  current: string | null,
): string | null {
  // Lúc gửi: trung tính, không bao giờ là nhãn thư viện.
  if (event === null) return copy.stage_thinking
  // planner complete: giữ nguyên nhãn hiện tại.
  if (event.node === 'planner' && event.status === 'complete') return current
  // retriever_agent complete có sources: nhãn của nguồn đầu tiên.
  if (event.node === 'retriever_agent' && event.status === 'complete') {
    const first = event.sources?.[0]
    if (first) {
      const key = SOURCE_TO_KEY[first]
      if (key) return copy[key]
    }
    return copy.stage_composing
  }
  // retriever rỗng đã xử lý ở trên; synthesizer started: composing.
  if (event.node === 'synthesizer' && event.status === 'started') {
    return copy.stage_composing
  }
  return current
}
