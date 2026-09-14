import Anthropic from '@anthropic-ai/sdk';
import config from '../config.js';
import { SYSTEM_PROMPT, buildUserPrompt } from './prompt.js';
import { EMIT_SITE_TOOL } from './tool-schema.js';
import { normalizeSpec } from './spec.js';
import { normalizeBusiness } from './business.js';
import { generateFromTemplate } from './templates.js';

let client;
function getClient() {
  if (!client) client = new Anthropic({ apiKey: config.anthropic.apiKey });
  return client;
}

const FALLBACK_BETA = 'server-side-fallback-2026-07-01';

function extractToolInput(message) {
  for (const block of message?.content ?? []) {
    if (block.type === 'tool_use' && block.name === EMIT_SITE_TOOL.name) return block.input;
  }
  // The model answered in prose instead of calling the tool. Salvage JSON if it's there.
  for (const block of message?.content ?? []) {
    if (block.type !== 'text') continue;
    const match = /\{[\s\S]*\}/.exec(block.text);
    if (!match) continue;
    try {
      return JSON.parse(match[0]);
    } catch {
      /* not usable — fall through */
    }
  }
  return null;
}

async function callModel(params, { withFallbacks }) {
  const request = {
    model: config.anthropic.model,
    max_tokens: 32000,
    thinking: { type: 'adaptive' },
    system: SYSTEM_PROMPT,
    tools: [EMIT_SITE_TOOL],
    // Forced tool choice is avoided so the request stays compatible with
    // adaptive thinking; the system prompt asks for the call explicitly.
    tool_choice: { type: 'auto' },
    messages: [{ role: 'user', content: buildUserPrompt(params) }],
    ...(withFallbacks ? { betas: [FALLBACK_BETA], fallbacks: 'default' } : {}),
  };

  const stream = withFallbacks
    ? getClient().beta.messages.stream(request)
    : getClient().messages.stream(request);
  return stream.finalMessage();
}

/**
 * Generate a site from a natural-language prompt.
 *
 * Always resolves to something usable: if the API is unconfigured, refuses, or
 * fails, the deterministic template engine takes over so the product still
 * works end to end.
 */
export async function generateSite(params) {
  if (!config.anthropic.enabled) {
    return { ...generateFromTemplate(params), source: 'template', reason: 'no_api_key' };
  }

  let message;
  try {
    message = await callModel(params, { withFallbacks: true });
  } catch (err) {
    // Older API surfaces reject the fallbacks beta; the request itself is fine.
    const retryable = err?.status === 400 || err?.status === 404;
    if (!retryable) return degrade(params, err);
    try {
      message = await callModel(params, { withFallbacks: false });
    } catch (retryErr) {
      return degrade(params, retryErr);
    }
  }

  if (message?.stop_reason === 'refusal') {
    return degrade(params, new Error('The model declined this prompt'), 'refused');
  }

  const input = extractToolInput(message);
  if (!input) return degrade(params, new Error('Model returned no site'), 'unparsable');

  return {
    spec: normalizeSpec(input.site),
    business: normalizeBusiness(input),
    source: 'ai',
    model: message.model || config.anthropic.model,
  };
}

function degrade(params, err, reason = 'api_error') {
  console.warn('[ai] falling back to templates:', err?.message || err);
  return {
    ...generateFromTemplate(params),
    source: 'template',
    reason,
    detail: err?.message || String(err),
  };
}
