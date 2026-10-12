// The providers on the BYOK screen.
//
// The list itself comes from the open catalog (models.dev): every provider it
// describes as speaking chat completions at a fixed address gets a card, with
// the catalog's own name, address and logo — see catalogProviders().
//
// What is written by hand here is only what the catalog cannot supply:
//  - the entry that takes any address the user types;
//  - providers the catalog knows but describes through their own SDK, with no
//    chat-completions address to read (OpenAI, Anthropic, Google, …). Their
//    addresses were taken from Project AIRI's provider definitions
//    (github.com/moeru-ai/airi, packages/provider-inference, MIT) on 12/10/2026;
//  - programs on the user's own machine.

import { catalogProviders } from './modelCatalog'

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

// The order here is the order of these cards on the Providers screen, after
// ECA and ahead of everything read from the catalog.
export const LLM_PRESETS: LlmPreset[] = [
  // First, next to ECA: the entry that takes any endpoint, so a provider with
  // no card of its own is not something to scroll to the bottom to find.
  { id: 'openai-compatible', name: 'OpenAI Compatible', baseUrl: '' },
  { id: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', catalogId: 'openai' },
  {
    id: 'anthropic',
    name: 'Anthropic',
    baseUrl: 'https://api.anthropic.com/v1/',
    catalogId: 'anthropic',
    checkHeaders: { 'anthropic-dangerous-direct-browser-access': 'true' },
  },
  {
    id: 'gemini',
    name: 'Google Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    catalogId: 'google',
  },
  { id: 'groq', name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1/', catalogId: 'groq' },
  { id: 'mistral', name: 'Mistral', baseUrl: 'https://api.mistral.ai/v1/', catalogId: 'mistral' },
  { id: 'xai', name: 'xAI', baseUrl: 'https://api.x.ai/v1', catalogId: 'xai' },
  { id: 'openrouter', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1/', catalogId: 'openrouter' },
  { id: 'together', name: 'Together.ai', baseUrl: 'https://api.together.xyz/v1/', catalogId: 'togetherai' },
  { id: 'perplexity', name: 'Perplexity', baseUrl: 'https://api.perplexity.ai/', catalogId: 'perplexity' },
  { id: 'minimax', name: 'MiniMax', baseUrl: 'https://api.minimaxi.com/v1/' },
  { id: 'aihubmix', name: 'AIHubMix', baseUrl: 'https://aihubmix.com/v1/', catalogId: 'aihubmix' },
  { id: 'cerebras', name: 'Cerebras', baseUrl: 'https://api.cerebras.ai/v1/', catalogId: 'cerebras' },
  {
    id: 'bedrock',
    name: 'Amazon Bedrock',
    baseUrl: 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1',
    note: 'bedrock_note',
  },
  { id: 'ollama', name: 'Ollama', baseUrl: 'http://localhost:11434/v1/', local: true },
  { id: 'lm-studio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1/', local: true },
]

/**
 * Every card after ECA: the hand-written entries, then the catalog's.
 *
 * A catalog provider already written by hand is not repeated — whichever of
 * its two ids matches. Without a catalog (still loading, or unreachable) the
 * hand-written entries are the whole list, so the screen never comes up empty.
 */
export function providerList(catalog: unknown | null): LlmPreset[] {
  const byHand = new Set(LLM_PRESETS.flatMap((p) => (p.catalogId ? [p.id, p.catalogId] : [p.id])))
  const fromCatalog = catalogProviders(catalog)
    .filter((p) => !byHand.has(p.id))
    .map((p): LlmPreset => ({ ...p, catalogId: p.id }))
  return [...LLM_PRESETS, ...fromCatalog]
}
