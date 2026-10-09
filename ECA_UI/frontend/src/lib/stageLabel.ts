/**
 * Nhãn trạng thái chat theo tin backend gửi (stage-labels).
 *
 * Thứ tự nhãn: thinking lúc gửi → nhãn nguồn (chỉ ở lượt có tra) →
 * composing từ lúc backend báo synthesizer bắt đầu → chữ. Không đồng hồ,
 * không đoán: nhãn chỉ đổi khi có sự kiện stage thật.
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
  // retriever_agent complete: nhãn của mọi nguồn được tra, theo thứ tự gọi.
  // Các tool chạy song song nên các nhãn hiện cùng lúc.
  if (event.node === 'retriever_agent' && event.status === 'complete') {
    const labels = (event.sources ?? [])
      .map((source) => SOURCE_TO_KEY[source])
      .filter((key): key is StageCopyKey => Boolean(key))
      .map((key) => copy[key])
    // Không tra nguồn nào: giữ nhãn hiện tại. Nhãn cuối chỉ đổi khi backend
    // báo synthesizer bắt đầu.
    return labels.length > 0 ? labels.join(' ') : current
  }
  // retriever rỗng đã xử lý ở trên; synthesizer started: composing.
  if (event.node === 'synthesizer' && event.status === 'started') {
    return copy.stage_composing
  }
  return current
}
