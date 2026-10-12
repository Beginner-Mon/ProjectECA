// Provider presets for BYOK — name + address prefilled, key never leaves the browser.
//
// Names and addresses taken from Project AIRI's provider-inference definitions
// (github.com/moeru-ai/airi, packages/provider-inference, MIT) on 12/10/2026.
// The "browser check" column in the plan is K's 12/10/2026 probe of whether a
// browser could reach each provider directly (no valid key); it does not affect
// chat time, when the server is the caller — only the pre-connect check.

export interface LlmPreset {
  id: string
  name: string
  baseUrl: string
  /** Provider id on models.dev for capability metadata. Absent → global id lookup. */
  catalogId?: string
  /** Extra headers for browser-direct calls (e.g. Anthropic's browser flag). */
  checkHeaders?: Record<string, string>
  /** Shown only when the backend allows local endpoints (BYOK_ALLOW_LOCAL). */
  local?: true
  /** Extra note shown under the detail view (e.g. Bedrock region/key lifetime). */
  note?: string
}

// The order here is the order of the cards on the Providers screen, after ECA.
export const LLM_PRESETS: LlmPreset[] = [
  // First, next to ECA: the entry that takes any endpoint, so a provider with
  // no card of its own is not something to scroll to the bottom to find.
  { id: 'openai-compatible', name: 'OpenAI Compatible', baseUrl: '' },
  { id: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', catalogId: 'openai' },
  {
    id: 'anthropic',
    name: 'Anthropic',
    baseUrl: 'https://api.anthropic.com/v1/',
    checkHeaders: { 'anthropic-dangerous-direct-browser-access': 'true' },
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    catalogId: 'google',
  },
  { id: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/', catalogId: 'deepseek' },
  { id: 'groq', name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1/', catalogId: 'groq' },
  { id: 'mistral', name: 'Mistral', baseUrl: 'https://api.mistral.ai/v1/', catalogId: 'mistral' },
  { id: 'xai', name: 'xAI', baseUrl: 'https://api.x.ai/v1' },
  { id: 'openrouter', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1/' },
  { id: 'together', name: 'Together.ai', baseUrl: 'https://api.together.xyz/v1/' },
  { id: 'fireworks', name: 'Fireworks.ai', baseUrl: 'https://api.fireworks.ai/inference/v1/' },
  { id: 'perplexity', name: 'Perplexity', baseUrl: 'https://api.perplexity.ai/' },
  { id: 'moonshot', name: 'Moonshot AI', baseUrl: 'https://api.moonshot.ai/v1/' },
  { id: 'minimax', name: 'MiniMax', baseUrl: 'https://api.minimaxi.com/v1/' },
  { id: 'xiaomi-mimo', name: 'Xiaomi MiMo', baseUrl: 'https://api.xiaomimimo.com/v1/' },
  { id: 'modelscope', name: 'ModelScope', baseUrl: 'https://api-inference.modelscope.cn/v1/' },
  { id: 'novita', name: 'Novita', baseUrl: 'https://api.novita.ai/openai/' },
  { id: 'featherless', name: 'Featherless.ai', baseUrl: 'https://api.featherless.ai/v1/' },
  { id: 'atlascloud', name: 'Atlas Cloud', baseUrl: 'https://api.atlascloud.ai/v1' },
  { id: '302ai', name: '302.AI', baseUrl: 'https://api.302.ai/v1/' },
  { id: 'aihubmix', name: 'AIHubMix', baseUrl: 'https://aihubmix.com/v1/' },
  { id: 'cometapi', name: 'CometAPI', baseUrl: 'https://api.cometapi.com/v1/' },
  { id: 'n1n', name: 'n1n', baseUrl: 'https://api.n1n.ai/v1' },
  { id: 'cerebras', name: 'Cerebras', baseUrl: 'https://api.cerebras.ai/v1/' },
  { id: 'zai', name: 'Z.ai', baseUrl: 'https://api.z.ai/api/paas/v4' },
  { id: 'openpaths', name: 'OpenPaths', baseUrl: 'https://openpaths.io/v1' },
  {
    id: 'bedrock',
    name: 'Amazon Bedrock',
    baseUrl: 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1',
    note: 'bedrock_note',
  },
  { id: 'ollama', name: 'Ollama', baseUrl: 'http://localhost:11434/v1/', local: true },
  { id: 'lm-studio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1/', local: true },
]

export function presetById(id: string): LlmPreset | undefined {
  return LLM_PRESETS.find((p) => p.id === id)
}
