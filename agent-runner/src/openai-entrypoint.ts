/**
 * N184 Agent Runner — OpenAI / OpenAI-compat runtime
 *
 * This is the "B path" runtime. JobManager selects it (via the registry's
 * `runtime: openai-sdk` field) for any provider whose wire protocol is
 * OpenAI-compatible: openai itself, deepseek, ollama, LiteLLM proxies, etc.
 *
 * Compared to the claude-sdk runtime in index.ts this is intentionally
 * minimal — it does the smallest thing that makes "dispatch agent X to
 * provider Y" actually mean something:
 *
 *   1. Read ContainerInput (same shape as the claude-sdk runtime).
 *   2. Read the soul (CLAUDE.md) and use it as the system prompt.
 *   3. Loop chat.completions with tool calls until the model is done.
 *   4. Expose two tools that mirror the MCP server: send_message and
 *      schedule_task. They publish to the same Redis channels so Honoré
 *      and the controller can't tell whether the message came from a
 *      Claude pod or an OpenAI/DeepSeek pod.
 *
 * What it does NOT do (yet, can be extended):
 *   - Filesystem tool use (Bash/Read/Write). Sub-agents that only need
 *     to chat and dispatch (e.g., a DeepSeek Vautrin reading a code map
 *     and emitting findings) work fine without these.
 *   - Session resumption.
 *   - The full claude-agent-sdk MCP tool surface.
 *
 * If the model the registry resolves to needs filesystem access, dispatch
 * via the claude-sdk runtime instead — or extend this file.
 */

import fs from 'fs';
import OpenAI from 'openai';
import { RedisIPC } from './redis-ipc.js';

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const AGENT_NAME = process.env.N184_AGENT_NAME || 'agent';
const JOB_NAME = process.env.JOB_NAME || '';
const PROVIDER = process.env.N184_PROVIDER || 'openai';
const MODEL = process.env.N184_MODEL || '';
const BASE_URL = process.env.N184_PROVIDER_BASE_URL || '';
const API_KEY_ENV = process.env.N184_PROVIDER_API_KEY_ENV || '';

const SWARM_KILL_KEY = 'n184:swarm:kill';

interface ContainerInput {
  prompt: string;
  groupFolder: string;
  chatJid: string;
  isMain: boolean;
  isScheduledTask?: boolean;
  assistantName?: string;
  provider?: string;
  model?: string;
  script?: string;
  contextMode?: string;
  context_mode?: string;
  resourceLimits?: ResourceLimits;
  resource_limits?: ResourceLimits;
}

interface ResourceLimits {
  maxTurns?: number;
  max_turns?: number;
  maxBudgetUsd?: number;
  max_budget_usd?: number;
  timeoutMs?: number;
  timeout_ms?: number;
}

function log(message: string): void {
  console.error(`[openai-entrypoint:${AGENT_NAME}] ${message}`);
}

async function readContainerInput(redis: RedisIPC): Promise<ContainerInput> {
  // Standard k8s path: JobManager wrote ContainerInput to Redis under
  // n184:job-input:{JOB_NAME}.
  if (JOB_NAME) {
    const raw = await redis.getJobInput(JOB_NAME);
    if (raw) return JSON.parse(raw) as ContainerInput;
  }
  // Fallback path used by Vautrin entrypoint and dev runs: read stdin.
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  const stdin = Buffer.concat(chunks).toString('utf-8').trim();
  if (!stdin) throw new Error('No ContainerInput available (no JOB_NAME, empty stdin)');
  return JSON.parse(stdin) as ContainerInput;
}

function readSoul(): string {
  const path = '/workspace/group/CLAUDE.md';
  try {
    return fs.readFileSync(path, 'utf-8');
  } catch {
    log(`No soul found at ${path}, proceeding without one`);
    return '';
  }
}

function resolveApiKey(): string {
  if (!API_KEY_ENV) return ''; // e.g. local Ollama
  const v = process.env[API_KEY_ENV];
  if (!v) {
    log(
      `WARNING: provider=${PROVIDER} expects key in env var ${API_KEY_ENV} but it is empty. ` +
        'Continuing — the upstream may reject the request.',
    );
  }
  return v || '';
}

function parsePositiveInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function getMaxTurns(input: ContainerInput): number {
  const raw = input.resourceLimits || input.resource_limits || {};
  return raw.maxTurns || raw.max_turns || parsePositiveInt('N184_MAX_TURNS', 32);
}

async function isSwarmKilled(redis: RedisIPC): Promise<string | null> {
  return redis.getValue(SWARM_KILL_KEY);
}

// ── Tool definitions visible to the model ────────────────────────────
//
// Mirrors the MCP server tools (send_message, schedule_task). We keep the
// list small on purpose: a Vautrin pod's job is to analyze and report,
// not to drive a full IDE. Honoré will keep running on claude-sdk where
// the full tool surface lives.

const TOOLS: OpenAI.Chat.Completions.ChatCompletionTool[] = [
  {
    type: 'function',
    function: {
      name: 'send_message',
      description:
        'Send a message back to the user/group while you are still running. Use for progress updates and final findings.',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: 'Message text' },
          sender: { type: 'string', description: 'Optional role/identity name' },
        },
        required: ['text'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'schedule_task',
      description:
        'Dispatch a task to another agent (target_agent: "rastignac", "vautrin", "bianchon", "lousteau", "fil-de-soie"). Honoré uses this to coordinate the swarm.',
      parameters: {
        type: 'object',
        properties: {
          target_agent: { type: 'string' },
          prompt: { type: 'string' },
          provider: { type: 'string' },
          model: { type: 'string' },
          resource_limits: {
            type: 'object',
            properties: {
              max_turns: { type: 'integer' },
              max_budget_usd: { type: 'number' },
              timeout_ms: { type: 'integer' },
            },
          },
        },
        required: ['target_agent', 'prompt'],
      },
    },
  },
];

async function handleToolCall(
  redis: RedisIPC,
  containerInput: ContainerInput,
  call: OpenAI.Chat.Completions.ChatCompletionMessageFunctionToolCall,
): Promise<string> {
  let args: Record<string, unknown> = {};
  try {
    args = JSON.parse(call.function.arguments);
  } catch {
    return `error: invalid JSON arguments for ${call.function.name}`;
  }

  const fnName = call.function.name;

  if (fnName === 'send_message') {
    await redis.sendMessage({
      type: 'message',
      chatJid: containerInput.chatJid,
      text: String(args.text ?? ''),
      sender: typeof args.sender === 'string' ? args.sender : undefined,
      groupFolder: containerInput.groupFolder,
      agentName: AGENT_NAME,
      timestamp: new Date().toISOString(),
    });
    return 'message sent';
  }

  if (fnName === 'schedule_task') {
    const killed = await isSwarmKilled(redis);
    if (killed) return `error: swarm kill switch is active: ${killed}`;
    await redis.pushTask({
      type: 'schedule_task',
      taskId: `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      prompt: String(args.prompt ?? ''),
      schedule_type: 'once',
      schedule_value: new Date().toISOString().replace(/\..*$/, ''),
      context_mode: 'isolated',
      targetJid: containerInput.chatJid,
      targetAgent: String(args.target_agent ?? ''),
      provider: typeof args.provider === 'string' ? args.provider : undefined,
      model: typeof args.model === 'string' ? args.model : undefined,
      resource_limits:
        typeof args.resource_limits === 'object' && args.resource_limits !== null
          ? args.resource_limits
          : undefined,
      createdBy: containerInput.groupFolder,
      agentName: AGENT_NAME,
      timestamp: new Date().toISOString(),
    });
    return `task dispatched to ${args.target_agent}`;
  }

  return `error: unknown tool ${fnName}`;
}

async function main(): Promise<void> {
  if (!MODEL) throw new Error('N184_MODEL not set — JobManager should have populated it');
  if (!BASE_URL) throw new Error('N184_PROVIDER_BASE_URL not set');

  const redis = new RedisIPC(REDIS_URL, AGENT_NAME);
  await redis.connect();
  log(`Started (provider=${PROVIDER} model=${MODEL} base_url=${BASE_URL})`);

  const containerInput = await readContainerInput(redis);
  log(`ContainerInput loaded for group ${containerInput.groupFolder}`);

  const apiKey = resolveApiKey();
  const client = new OpenAI({
    apiKey: apiKey || 'sk-no-key-required', // OpenAI SDK refuses empty string
    baseURL: BASE_URL,
  });

  const soul = readSoul();
  const systemMessages: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [];
  if (soul) systemMessages.push({ role: 'system', content: soul });

  const messages: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [
    ...systemMessages,
    {
      role: 'user',
      content: containerInput.isScheduledTask
        ? `[SCHEDULED TASK]\n\n${containerInput.prompt}`
        : containerInput.prompt,
    },
  ];

  const maxTurns = getMaxTurns(containerInput);
  for (let turn = 0; turn < maxTurns; turn++) {
    const killed = await isSwarmKilled(redis);
    if (killed) {
      log(`Stopping because swarm kill switch is active: ${killed}`);
      break;
    }
    log(`Turn ${turn + 1}/${maxTurns}`);
    const response = await client.chat.completions.create({
      model: MODEL,
      messages,
      tools: TOOLS,
    });

    const choice = response.choices[0];
    const assistantMsg = choice.message;
    messages.push(assistantMsg);

    if (!assistantMsg.tool_calls || assistantMsg.tool_calls.length === 0) {
      // Final answer. If we have textual content and no send_message went
      // out yet, deliver it as a parting message so the user sees something.
      if (assistantMsg.content) {
        await redis.sendMessage({
          type: 'message',
          chatJid: containerInput.chatJid,
          text: assistantMsg.content,
          sender: containerInput.assistantName || AGENT_NAME,
          groupFolder: containerInput.groupFolder,
          agentName: AGENT_NAME,
          timestamp: new Date().toISOString(),
        });
      }
      log('Done (no further tool calls)');
      break;
    }

    for (const call of assistantMsg.tool_calls) {
      // Type narrowing: only function tool calls have .function on them.
      if (call.type !== 'function') continue;
      const result = await handleToolCall(redis, containerInput, call);
      messages.push({ role: 'tool', tool_call_id: call.id, content: result });
    }
  }

  await redis.close();
  log('Exit');
}

main().catch((err) => {
  console.error(`[openai-entrypoint:${AGENT_NAME}] FATAL:`, err);
  process.exit(1);
});
