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
 *   4. Expose tools that mirror the MCP server (send_message, schedule_task)
 *      PLUS read-only code-scanning tools (list_dir, read_file, grep,
 *      find_files) so a non-Anthropic Vautrin can actually navigate and
 *      read the target repo at /workspace/shared — the openai-compat
 *      equivalent of the claude-sdk runtime's Read/Grep. They publish to the
 *      same Redis channels so Honoré can't tell a Claude pod from a
 *      DeepSeek/Ollama pod.
 *
 * What it does NOT do (yet, can be extended):
 *   - Write/Bash (the scan tools are read-only by design — a Vautrin
 *     analyzes and reports, it does not mutate the target).
 *   - Session resumption.
 *   - The full claude-agent-sdk MCP tool surface.
 */

import fs from 'fs';
import path from 'path';
import { execFileSync } from 'child_process';
import OpenAI from 'openai';
import { RedisIPC } from './redis-ipc.js';
import {
  checkBudget,
  recordUsage,
  loadBudgetConfig,
  resolveScanId,
} from './budget-guard.js';

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
  scan_id?: string;
  scanId?: string;
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

// ── Read-only filesystem tools for code scanning ─────────────────────
//
// Give a non-Anthropic agent the ability to actually navigate and read the
// target repo (mounted at /workspace/shared). The claude-sdk runtime has
// Read/Grep/Bash; this is the openai-compat equivalent — read-only and
// sandboxed to /workspace so a model can't wander outside the workspace.

const WORKSPACE_ROOT = '/workspace';
const SCAN_BASE = '/workspace/shared';

function resolveSafe(p: string): string {
  const abs = path.isAbsolute(p) ? path.resolve(p) : path.resolve(SCAN_BASE, p || '.');
  if (abs !== WORKSPACE_ROOT && !abs.startsWith(WORKSPACE_ROOT + path.sep)) {
    throw new Error(`path "${p}" escapes the workspace sandbox`);
  }
  return abs;
}

function relToBase(p: string): string {
  return p.startsWith(SCAN_BASE + '/') ? p.slice(SCAN_BASE.length + 1) : p;
}

function runCapture(cmd: string, args: string[]): string {
  try {
    return execFileSync(cmd, args, { encoding: 'utf-8', maxBuffer: 16 * 1024 * 1024 });
  } catch (e) {
    const err = e as { status?: number; stdout?: string; message?: string };
    if (err.status === 1 && !err.stdout) return ''; // grep/find: no matches
    if (err.stdout) return err.stdout; // partial output (e.g. hit a perms error)
    throw new Error(err.message || String(e));
  }
}

function toolListDir(p: string): string {
  const dir = resolveSafe(p);
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const shown = entries
    .slice(0, 500)
    .map((e) => `${e.isDirectory() ? 'd' : '-'} ${e.name}`)
    .sort();
  let out = `${relToBase(dir) || '.'}/  (${entries.length} entries)\n${shown.join('\n')}`;
  if (entries.length > 500) out += `\n... (${entries.length - 500} more)`;
  return out;
}

function toolReadFile(p: string, offset = 0, limit = 400): string {
  const file = resolveSafe(p);
  const stat = fs.statSync(file);
  if (stat.isDirectory()) return `error: "${p}" is a directory — use list_dir`;
  const lines = fs.readFileSync(file, 'utf-8').split('\n');
  const start = Math.max(0, offset);
  const end = Math.min(lines.length, start + Math.max(1, limit));
  const body = lines
    .slice(start, end)
    .map((l, i) => `${start + i + 1}\t${l}`)
    .join('\n');
  let out = body || '(empty)';
  if (end < lines.length) {
    out += `\n... (${lines.length - end} more lines — read_file offset=${end} to continue)`;
  }
  return out;
}

function toolGrep(pattern: string, p = '.', glob?: string, maxResults = 100): string {
  const base = resolveSafe(p);
  const args = ['-rnI', '--color=never', '--exclude-dir=node_modules', '--exclude-dir=.git'];
  if (glob) args.push(`--include=${glob}`);
  args.push('-e', pattern, base);
  let out: string;
  try {
    out = runCapture('grep', args);
  } catch (e) {
    return `grep error: ${e instanceof Error ? e.message : String(e)}`;
  }
  const lines = out.split('\n').filter(Boolean);
  if (lines.length === 0) return `no matches for /${pattern}/`;
  let res = lines.slice(0, maxResults).map((l) => relToBase(l)).join('\n');
  if (lines.length > maxResults) {
    res += `\n... (${lines.length - maxResults} more — narrow the pattern or pass a glob)`;
  }
  return res;
}

function toolFindFiles(name: string, p = '.', maxResults = 200): string {
  const base = resolveSafe(p);
  let out: string;
  try {
    out = runCapture('find', [
      base, '-type', 'f', '-name', name,
      '-not', '-path', '*/node_modules/*', '-not', '-path', '*/.git/*',
    ]);
  } catch (e) {
    return `find error: ${e instanceof Error ? e.message : String(e)}`;
  }
  const lines = out.split('\n').filter(Boolean).map((l) => relToBase(l));
  if (lines.length === 0) return `no files matching "${name}"`;
  let res = lines.slice(0, maxResults).join('\n');
  if (lines.length > maxResults) res += `\n... (${lines.length - maxResults} more)`;
  return res;
}

// Persist a findings report to the scan cache (the one place a Vautrin SHOULD
// write — its analysis, not the target repo). Sandboxed to ~/.n184/scan-cache.
const SCAN_CACHE = '/home/node/.n184/scan-cache';

function toolSaveReport(filename: string, content: string): string {
  const safe = (filename || 'report').replace(/[^A-Za-z0-9._-]/g, '_');
  const name = safe.endsWith('.md') ? safe : `${safe}.md`;
  try {
    fs.mkdirSync(SCAN_CACHE, { recursive: true });
    fs.writeFileSync(path.join(SCAN_CACHE, name), content, 'utf-8');
    return `report saved to ~/.n184/scan-cache/${name} (${content.length} chars)`;
  } catch (e) {
    return `error saving report: ${e instanceof Error ? e.message : String(e)}`;
  }
}

// Build a compact repo-layout overview so the model uses real paths from turn 1
// instead of burning its turn budget guessing (the gpt-4o "no citations" failure).
function buildWorkspaceOverview(): string {
  try {
    const entries = fs.readdirSync(SCAN_BASE, { withFileTypes: true })
      .filter((e) => !e.name.startsWith('.') && e.name !== 'node_modules');
    const lines: string[] = [];
    for (const e of entries.slice(0, 20)) {
      if (e.isDirectory()) {
        lines.push(`${SCAN_BASE}/${e.name}/`);
        try {
          const sub = fs.readdirSync(path.join(SCAN_BASE, e.name), { withFileTypes: true })
            .filter((s) => !s.name.startsWith('.') && s.name !== 'node_modules')
            .slice(0, 24)
            .map((s) => `  ${e.name}/${s.name}${s.isDirectory() ? '/' : ''}`);
          lines.push(...sub);
        } catch {
          /* ignore */
        }
      } else {
        lines.push(`${SCAN_BASE}/${e.name}`);
      }
    }
    return lines.join('\n');
  } catch {
    return '';
  }
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
  {
    type: 'function',
    function: {
      name: 'list_dir',
      description:
        'List files and subdirectories of the target repo. Paths are relative to the repo root (/workspace/shared) unless absolute under /workspace. Start here to explore the codebase.',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'Directory path (default: repo root)' },
        },
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'read_file',
      description:
        'Read a source file (returned line-numbered). Use offset/limit to page through large files. Paths are relative to the repo root (/workspace/shared).',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'File path' },
          offset: { type: 'integer', description: '0-based start line (default 0)' },
          limit: { type: 'integer', description: 'Max lines to return (default 400)' },
        },
        required: ['path'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'grep',
      description:
        'Search file contents with a regex, recursively (skips node_modules/.git). Returns file:line:match. Use this to locate dangerous sinks, calls, and patterns in the target repo.',
      parameters: {
        type: 'object',
        properties: {
          pattern: { type: 'string', description: 'Search regex (grep -e semantics)' },
          path: { type: 'string', description: 'Directory or file to search (default: repo root)' },
          glob: { type: 'string', description: 'Only search files matching this glob, e.g. "*.ts"' },
          max_results: { type: 'integer', description: 'Max matching lines (default 100)' },
        },
        required: ['pattern'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'find_files',
      description:
        'Find files by name/glob, recursively (skips node_modules/.git). e.g. name="*.config.js".',
      parameters: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'Filename glob, e.g. "*.ts"' },
          path: { type: 'string', description: 'Directory to search (default: repo root)' },
          max_results: { type: 'integer', description: 'Max files (default 200)' },
        },
        required: ['name'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'save_report',
      description:
        'Persist your FULL findings report to the scan cache so Honoré and the Devil\'s Advocate pass can read it from disk. Write complete markdown with file:line citations. Call this once you have evidence, then also send_message a short summary. Filename like "<scan_id>-vautrin-<name>.md".',
      parameters: {
        type: 'object',
        properties: {
          filename: { type: 'string', description: 'Report filename, e.g. "vercel-ai-20260524-vautrin-deepseek-1.md"' },
          content: { type: 'string', description: 'Full markdown report (with file:line citations)' },
        },
        required: ['filename', 'content'],
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

  if (fnName === 'list_dir') {
    try {
      return toolListDir(String(args.path ?? '.'));
    } catch (e) {
      return `error: ${e instanceof Error ? e.message : String(e)}`;
    }
  }

  if (fnName === 'read_file') {
    try {
      return toolReadFile(
        String(args.path ?? ''),
        typeof args.offset === 'number' ? args.offset : 0,
        typeof args.limit === 'number' ? args.limit : 400,
      );
    } catch (e) {
      return `error: ${e instanceof Error ? e.message : String(e)}`;
    }
  }

  if (fnName === 'grep') {
    return toolGrep(
      String(args.pattern ?? ''),
      typeof args.path === 'string' ? args.path : '.',
      typeof args.glob === 'string' ? args.glob : undefined,
      typeof args.max_results === 'number' ? args.max_results : 100,
    );
  }

  if (fnName === 'find_files') {
    return toolFindFiles(
      String(args.name ?? '*'),
      typeof args.path === 'string' ? args.path : '.',
      typeof args.max_results === 'number' ? args.max_results : 200,
    );
  }

  if (fnName === 'save_report') {
    return toolSaveReport(String(args.filename ?? 'report.md'), String(args.content ?? ''));
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
  const overview = buildWorkspaceOverview();
  if (overview) {
    systemMessages.push({
      role: 'system',
      content:
        'Repository layout under /workspace/shared (use these EXACT absolute paths — ' +
        'do NOT guess relative paths, that wastes your turn budget):\n\n' +
        overview,
    });
  }
  systemMessages.push({
    role: 'system',
    content:
      'You have read-only access to the code via list_dir, read_file, grep, find_files. ' +
      'Investigate the ACTUAL code with absolute paths from the layout above before drawing ' +
      'any conclusion — never invent file paths, line numbers, or vulnerabilities. ' +
      'Every finding MUST cite file:line you actually read. When done: (1) call save_report ' +
      'with your FULL markdown report (so Honoré and the DA pass can read it from disk), then ' +
      '(2) send_message a short summary. If the evidence is not there, say so plainly rather ' +
      'than speculating — an unsubstantiated finding is worse than none.',
  });

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
  const scanId = resolveScanId(containerInput);
  const budgetConfig = loadBudgetConfig();
  for (let turn = 0; turn < maxTurns; turn++) {
    const killed = await isSwarmKilled(redis);
    if (killed) {
      log(`Stopping because swarm kill switch is active: ${killed}`);
      break;
    }
    // Loop-safe budget gate (token-based, persisted in Redis) — the same cap
    // the Claude path honors, so DeepSeek/Ollama spend counts toward it too.
    const budget = await checkBudget(redis, { config: budgetConfig, scanId });
    if (!budget.allowed) {
      log(`Budget gate: ${budget.reason} — stopping`);
      break;
    }
    log(`Turn ${turn + 1}/${maxTurns}`);
    const response = await client.chat.completions.create({
      model: MODEL,
      messages,
      tools: TOOLS,
    });
    // Meter token usage so the cumulative cap sees this provider's spend.
    if (response.usage) {
      const u = response.usage;
      const tokens = u.total_tokens ?? ((u.prompt_tokens ?? 0) + (u.completion_tokens ?? 0));
      try {
        await recordUsage(redis, { tokens, scanId });
      } catch (e) {
        log(`usage record failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    }

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
      log(`tool ${call.function.name}(${(call.function.arguments || '').slice(0, 160)})`);
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
