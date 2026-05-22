/**
 * N184 Agent Runner
 * Forked from NanoClaw agent-runner for Kubernetes-native deployment.
 *
 * Supports two IPC backends (toggled by IPC_BACKEND env var):
 *   - "file": Original NanoClaw file-based IPC (backward compatible)
 *   - "redis": Redis pub/sub for k8s deployment
 *
 * Input protocol:
 *   Stdin: Full ContainerInput JSON (file mode, or piped from k8s-entrypoint.sh)
 *   Redis: ContainerInput fetched from n184:job-input:{JOB_NAME} key
 *
 * Stdout protocol:
 *   Each result is wrapped in OUTPUT_START_MARKER / OUTPUT_END_MARKER pairs.
 */

import fs from 'fs';
import path from 'path';
import { execFile } from 'child_process';
import {
  query,
  HookCallback,
  PreCompactHookInput,
} from '@anthropic-ai/claude-agent-sdk';
import { fileURLToPath } from 'url';
import { RedisIPC } from './redis-ipc.js';
import {
  checkBudget,
  recordUsage,
  tokensFromUsage,
  loadBudgetConfig,
  resolveScanId,
  type Usage,
} from './budget-guard.js';

// ── IPC Backend Selection ────────────────────────────────────────────

const IPC_BACKEND = process.env.IPC_BACKEND || 'file';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const AGENT_NAME = process.env.N184_AGENT_NAME || 'agent';

// ── Types ────────────────────────────────────────────────────────────

interface ContainerInput {
  prompt: string;
  sessionId?: string;
  groupFolder: string;
  chatJid: string;
  isMain: boolean;
  isScheduledTask?: boolean;
  assistantName?: string;
  contextMode?: ContextMode;
  context_mode?: ContextMode;
  script?: string;
  // Provider routing (set by JobManager from the registry).
  // For the claude-sdk runtime we read `model`; the rest is for diagnostics.
  provider?: string;
  model?: string;
  resourceLimits?: ResourceLimits;
  resource_limits?: ResourceLimits;
}

type ContextMode = 'group' | 'isolated' | 'recovery';

interface ResourceLimits {
  maxTurns?: number;
  max_turns?: number;
  maxBudgetUsd?: number;
  max_budget_usd?: number;
  timeoutMs?: number;
  timeout_ms?: number;
}

interface ContainerOutput {
  status: 'success' | 'error';
  result: string | null;
  newSessionId?: string;
  error?: string;
}

interface SessionEntry {
  sessionId: string;
  fullPath: string;
  summary: string;
  firstPrompt: string;
}

interface SessionsIndex {
  entries: SessionEntry[];
}

interface SDKUserMessage {
  type: 'user';
  message: { role: 'user'; content: string };
  parent_tool_use_id: null;
  session_id: string;
}

// ── File-based IPC (NanoClaw compatibility) ──────────────────────────

const IPC_INPUT_DIR = '/workspace/ipc/input';
const IPC_INPUT_CLOSE_SENTINEL = path.join(IPC_INPUT_DIR, '_close');
const IPC_POLL_MS = 500;

function shouldClose(): boolean {
  if (fs.existsSync(IPC_INPUT_CLOSE_SENTINEL)) {
    try {
      fs.unlinkSync(IPC_INPUT_CLOSE_SENTINEL);
    } catch {
      /* ignore */
    }
    return true;
  }
  return false;
}

function drainIpcInput(): string[] {
  try {
    fs.mkdirSync(IPC_INPUT_DIR, { recursive: true });
    const files = fs
      .readdirSync(IPC_INPUT_DIR)
      .filter((f) => f.endsWith('.json'))
      .sort();

    const messages: string[] = [];
    for (const file of files) {
      const filePath = path.join(IPC_INPUT_DIR, file);
      try {
        const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
        fs.unlinkSync(filePath);
        if (data.type === 'message' && data.text) {
          messages.push(data.text);
        }
      } catch (err) {
        log(
          `Failed to process input file ${file}: ${err instanceof Error ? err.message : String(err)}`,
        );
        try {
          fs.unlinkSync(filePath);
        } catch {
          /* ignore */
        }
      }
    }
    return messages;
  } catch (err) {
    log(`IPC drain error: ${err instanceof Error ? err.message : String(err)}`);
    return [];
  }
}

function waitForIpcMessage(): Promise<string | null> {
  return new Promise((resolve) => {
    const poll = () => {
      if (shouldClose()) {
        resolve(null);
        return;
      }
      const messages = drainIpcInput();
      if (messages.length > 0) {
        resolve(messages.join('\n'));
        return;
      }
      setTimeout(poll, IPC_POLL_MS);
    };
    poll();
  });
}

// ── MessageStream ────────────────────────────────────────────────────

class MessageStream {
  private queue: SDKUserMessage[] = [];
  private waiting: (() => void) | null = null;
  private done = false;

  push(text: string): void {
    this.queue.push({
      type: 'user',
      message: { role: 'user', content: text },
      parent_tool_use_id: null,
      session_id: '',
    });
    this.waiting?.();
  }

  end(): void {
    this.done = true;
    this.waiting?.();
  }

  async *[Symbol.asyncIterator](): AsyncGenerator<SDKUserMessage> {
    while (true) {
      while (this.queue.length > 0) {
        yield this.queue.shift()!;
      }
      if (this.done) return;
      await new Promise<void>((r) => {
        this.waiting = r;
      });
      this.waiting = null;
    }
  }
}

// ── Utilities ────────────────────────────────────────────────────────

async function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      data += chunk;
    });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

const OUTPUT_START_MARKER = '---NANOCLAW_OUTPUT_START---';
const OUTPUT_END_MARKER = '---NANOCLAW_OUTPUT_END---';

function writeOutput(output: ContainerOutput): void {
  console.log(OUTPUT_START_MARKER);
  console.log(JSON.stringify(output));
  console.log(OUTPUT_END_MARKER);
}

function log(message: string): void {
  console.error(`[agent-runner] ${message}`);
}

function parsePositiveInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parsePositiveFloat(name: string): number | undefined {
  const raw = process.env[name];
  if (!raw) return undefined;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function getContextMode(input: ContainerInput): ContextMode {
  return input.contextMode || input.context_mode || 'group';
}

function getResourceLimits(input: ContainerInput, contextMode: ContextMode): {
  maxTurns: number;
  maxBudgetUsd?: number;
  timeoutMs: number;
} {
  const raw = input.resourceLimits || input.resource_limits || {};
  const defaultTurns = contextMode === 'recovery'
    ? parsePositiveInt('N184_RECOVERY_MAX_TURNS', 12)
    : parsePositiveInt('N184_MAX_TURNS', 40);
  const defaultTimeout = contextMode === 'recovery'
    ? parsePositiveInt('N184_RECOVERY_QUERY_TIMEOUT_MS', 600_000)
    : parsePositiveInt('N184_QUERY_TIMEOUT_MS', 1_800_000);

  return {
    maxTurns: raw.maxTurns || raw.max_turns || defaultTurns,
    maxBudgetUsd:
      raw.maxBudgetUsd ||
      raw.max_budget_usd ||
      parsePositiveFloat('N184_MAX_BUDGET_USD'),
    timeoutMs: raw.timeoutMs || raw.timeout_ms || defaultTimeout,
  };
}

function getAllowedTools(contextMode: ContextMode, mcpServerName: string): string[] {
  const common = [
    'Bash',
    'Read',
    'Glob',
    'Grep',
    'SendMessage',
    'TodoWrite',
    `mcp__${mcpServerName}__*`,
  ];

  if (contextMode === 'recovery') {
    return common;
  }

  return [
    ...common,
    'Write',
    'Edit',
    'WebSearch',
    'WebFetch',
    'Task',
    'TaskOutput',
    'TaskStop',
    'TeamCreate',
    'TeamDelete',
    'ToolSearch',
    'Skill',
    'NotebookEdit',
  ];
}

function createPreToolUseGuard(contextMode: ContextMode): HookCallback {
  return async (input) => {
    const event = input as {
      hook_event_name?: string;
      tool_name?: string;
      tool_input?: unknown;
    };
    if (event.hook_event_name !== 'PreToolUse') return {};

    if (contextMode === 'recovery' && event.tool_name?.startsWith('mcp__')) {
      const tool = event.tool_name.toLowerCase();
      if (tool.includes('schedule_task') || tool.includes('register_provider')) {
        return {
          hookSpecificOutput: {
            hookEventName: 'PreToolUse',
            permissionDecision: 'deny',
            permissionDecisionReason:
              'Honoré is in restart recovery mode. Inspect state and ask the operator before dispatching new agents.',
          },
        };
      }
    }

    if (event.tool_name === 'Bash') {
      const command = String((event.tool_input as { command?: unknown })?.command ?? '');
      const maxCommandChars = parsePositiveInt('N184_MAX_BASH_COMMAND_CHARS', 4000);
      const runawayPatterns = [
        /find\s+\S+\s+.*-exec\s+cat\b/s,
        /cat\s+\$\(find\b/s,
        /grep\s+-R\s+["']?\./s,
      ];
      if (command.length > maxCommandChars || runawayPatterns.some((p) => p.test(command))) {
        return {
          hookSpecificOutput: {
            hookEventName: 'PreToolUse',
            permissionDecision: 'deny',
            permissionDecisionReason:
              'Command blocked by N184 runtime guardrail because it can dump too much repository context.',
          },
        };
      }
    }

    return {};
  };
}

// ── Transcript Archiving ─────────────────────────────────────────────

function getSessionSummary(
  sessionId: string,
  transcriptPath: string,
): string | null {
  const projectDir = path.dirname(transcriptPath);
  const indexPath = path.join(projectDir, 'sessions-index.json');
  if (!fs.existsSync(indexPath)) return null;
  try {
    const index: SessionsIndex = JSON.parse(
      fs.readFileSync(indexPath, 'utf-8'),
    );
    const entry = index.entries.find((e) => e.sessionId === sessionId);
    return entry?.summary || null;
  } catch {
    return null;
  }
}

function createPreCompactHook(assistantName?: string): HookCallback {
  return async (input) => {
    const preCompact = input as PreCompactHookInput;
    const transcriptPath = preCompact.transcript_path;
    const sessionId = preCompact.session_id;

    if (!transcriptPath || !fs.existsSync(transcriptPath)) return {};

    try {
      const content = fs.readFileSync(transcriptPath, 'utf-8');
      const messages = parseTranscript(content);
      if (messages.length === 0) return {};

      const summary = getSessionSummary(sessionId, transcriptPath);
      const name = summary ? sanitizeFilename(summary) : generateFallbackName();

      const conversationsDir = '/workspace/group/conversations';
      fs.mkdirSync(conversationsDir, { recursive: true });

      const date = new Date().toISOString().split('T')[0];
      const filename = `${date}-${name}.md`;
      const filePath = path.join(conversationsDir, filename);

      const markdown = formatTranscriptMarkdown(messages, summary, assistantName);
      fs.writeFileSync(filePath, markdown);
      log(`Archived conversation to ${filePath}`);
    } catch (err) {
      log(`Failed to archive: ${err instanceof Error ? err.message : String(err)}`);
    }
    return {};
  };
}

function sanitizeFilename(summary: string): string {
  return summary
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 50);
}

function generateFallbackName(): string {
  const time = new Date();
  return `conversation-${time.getHours().toString().padStart(2, '0')}${time.getMinutes().toString().padStart(2, '0')}`;
}

interface ParsedMessage {
  role: 'user' | 'assistant';
  content: string;
}

function parseTranscript(content: string): ParsedMessage[] {
  const messages: ParsedMessage[] = [];
  for (const line of content.split('\n')) {
    if (!line.trim()) continue;
    try {
      const entry = JSON.parse(line);
      if (entry.type === 'user' && entry.message?.content) {
        const text =
          typeof entry.message.content === 'string'
            ? entry.message.content
            : entry.message.content
                .map((c: { text?: string }) => c.text || '')
                .join('');
        if (text) messages.push({ role: 'user', content: text });
      } else if (entry.type === 'assistant' && entry.message?.content) {
        const textParts = entry.message.content
          .filter((c: { type: string }) => c.type === 'text')
          .map((c: { text: string }) => c.text);
        const text = textParts.join('');
        if (text) messages.push({ role: 'assistant', content: text });
      }
    } catch {}
  }
  return messages;
}

function formatTranscriptMarkdown(
  messages: ParsedMessage[],
  title?: string | null,
  assistantName?: string,
): string {
  const now = new Date();
  const formatDateTime = (d: Date) =>
    d.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  const lines: string[] = [];
  lines.push(`# ${title || 'Conversation'}`);
  lines.push('');
  lines.push(`Archived: ${formatDateTime(now)}`);
  lines.push('');
  lines.push('---');
  lines.push('');
  for (const msg of messages) {
    const sender = msg.role === 'user' ? 'User' : assistantName || 'Assistant';
    const content =
      msg.content.length > 2000
        ? msg.content.slice(0, 2000) + '...'
        : msg.content;
    lines.push(`**${sender}**: ${content}`);
    lines.push('');
  }
  return lines.join('\n');
}

// ── Script Execution ─────────────────────────────────────────────────

interface ScriptResult {
  wakeAgent: boolean;
  data?: unknown;
}

const SCRIPT_TIMEOUT_MS = 30_000;

async function runScript(script: string): Promise<ScriptResult | null> {
  const scriptPath = '/tmp/task-script.sh';
  fs.writeFileSync(scriptPath, script, { mode: 0o755 });

  return new Promise((resolve) => {
    execFile(
      'bash',
      [scriptPath],
      { timeout: SCRIPT_TIMEOUT_MS, maxBuffer: 1024 * 1024, env: process.env },
      (error, stdout, stderr) => {
        if (stderr) log(`Script stderr: ${stderr.slice(0, 500)}`);
        if (error) {
          log(`Script error: ${error.message}`);
          return resolve(null);
        }
        const lines = stdout.trim().split('\n');
        const lastLine = lines[lines.length - 1];
        if (!lastLine) return resolve(null);
        try {
          const result = JSON.parse(lastLine);
          if (typeof result.wakeAgent !== 'boolean') return resolve(null);
          resolve(result as ScriptResult);
        } catch {
          resolve(null);
        }
      },
    );
  });
}

// ── Core Query ───────────────────────────────────────────────────────

async function runQuery(
  prompt: string,
  sessionId: string | undefined,
  mcpServerPath: string,
  containerInput: ContainerInput,
  sdkEnv: Record<string, string | undefined>,
  redisIpc: RedisIPC | null,
  resumeAt?: string,
): Promise<{
  newSessionId?: string;
  lastAssistantUuid?: string;
  closedDuringQuery: boolean;
}> {
  const stream = new MessageStream();
  stream.push(prompt);

  let ipcPolling = true;
  let closedDuringQuery = false;

  if (IPC_BACKEND === 'redis' && redisIpc) {
    // Redis-based IPC: subscribe for follow-up messages during query
    (async () => {
      for await (const msg of redisIpc.subscribe()) {
        if (!ipcPolling) break;
        if (msg === null) {
          log('Close signal received via Redis during query');
          closedDuringQuery = true;
          stream.end();
          break;
        }
        log(`Redis message piped into active query (${msg.length} chars)`);
        stream.push(msg);
      }
    })();
  } else {
    // File-based IPC: poll /workspace/ipc/input/
    const pollIpcDuringQuery = () => {
      if (!ipcPolling) return;
      if (shouldClose()) {
        closedDuringQuery = true;
        stream.end();
        ipcPolling = false;
        return;
      }
      const messages = drainIpcInput();
      for (const text of messages) {
        log(`Piping IPC message into active query (${text.length} chars)`);
        stream.push(text);
      }
      setTimeout(pollIpcDuringQuery, IPC_POLL_MS);
    };
    setTimeout(pollIpcDuringQuery, IPC_POLL_MS);
  }

  let newSessionId: string | undefined;
  let lastAssistantUuid: string | undefined;
  let messageCount = 0;
  let resultCount = 0;

  // Load global CLAUDE.md as additional system context
  const globalClaudeMdPath = '/workspace/global/CLAUDE.md';
  let globalClaudeMd: string | undefined;
  if (!containerInput.isMain && fs.existsSync(globalClaudeMdPath)) {
    globalClaudeMd = fs.readFileSync(globalClaudeMdPath, 'utf-8');
  }

  // Discover additional directories at /workspace/extra/*
  const extraDirs: string[] = [];
  const extraBase = '/workspace/extra';
  if (fs.existsSync(extraBase)) {
    for (const entry of fs.readdirSync(extraBase)) {
      const fullPath = path.join(extraBase, entry);
      if (fs.statSync(fullPath).isDirectory()) {
        extraDirs.push(fullPath);
      }
    }
  }

  // MCP server name: n184 for k8s, nanoclaw for file-based compat
  const mcpServerName = IPC_BACKEND === 'redis' ? 'n184' : 'nanoclaw';
  const contextMode = getContextMode(containerInput);
  const resourceLimits = getResourceLimits(containerInput, contextMode);

  // Loop-safe budget gate: refuse to start a query once cumulative spend
  // (persisted in Redis, so it survives restarts) has hit the cap. This is
  // what the per-query SDK budget cannot do — that ceiling resets every
  // restart. Covers sub-agent Jobs too, since they all run through here.
  const scanId = resolveScanId(containerInput as { scan_id?: string; scanId?: string });
  if (redisIpc) {
    const budget = await checkBudget(redisIpc, {
      config: loadBudgetConfig(),
      scanId,
    });
    if (!budget.allowed) {
      log(`Budget gate: ${budget.reason} — skipping query`);
      writeOutput({
        status: 'error',
        result: null,
        error: `budget_exceeded: ${budget.reason}`,
      });
      return { closedDuringQuery: false };
    }
  }

  const abortController = new AbortController();
  const timeoutHandle = setTimeout(() => {
    log(`Aborting query after ${resourceLimits.timeoutMs}ms timeout`);
    abortController.abort();
  }, resourceLimits.timeoutMs);

  // Honoré dispatched this Job with an explicit provider+model. We only
  // honor the model here; provider routing for non-Anthropic backends
  // is handled by openai-entrypoint.ts (which the JobManager selects via
  // the registry's runtime field). For claude-sdk runtime jobs the
  // ANTHROPIC_BASE_URL env var (set by JobManager when the registry
  // points at a proxy) is read by the SDK directly.
  const dispatchedModel = (containerInput as { model?: string }).model
    || process.env.N184_MODEL
    || undefined;

  try {
    for await (const message of query({
      prompt: stream,
      options: {
        cwd: '/workspace/group',
        model: dispatchedModel,
        additionalDirectories: extraDirs.length > 0 ? extraDirs : undefined,
        abortController,
        maxTurns: resourceLimits.maxTurns,
        maxBudgetUsd: resourceLimits.maxBudgetUsd,
        persistSession: contextMode === 'group',
        resume: contextMode === 'group' ? sessionId : undefined,
        resumeSessionAt: contextMode === 'group' ? resumeAt : undefined,
        systemPrompt: globalClaudeMd
          ? {
              type: 'preset' as const,
              preset: 'claude_code' as const,
              append: globalClaudeMd,
            }
          : undefined,
        allowedTools: getAllowedTools(contextMode, mcpServerName),
        env: sdkEnv,
        permissionMode: 'bypassPermissions',
        allowDangerouslySkipPermissions: true,
        settingSources: ['project', 'user'],
        mcpServers: {
          [mcpServerName]: {
            command: 'node',
            args: [mcpServerPath],
            env: {
              NANOCLAW_CHAT_JID: containerInput.chatJid,
              NANOCLAW_GROUP_FOLDER: containerInput.groupFolder,
              NANOCLAW_IS_MAIN: containerInput.isMain ? '1' : '0',
              IPC_BACKEND,
              REDIS_URL,
              N184_AGENT_NAME: AGENT_NAME,
              N184_CONTEXT_MODE: contextMode,
              N184_MAX_TURNS: String(resourceLimits.maxTurns),
              N184_MAX_BUDGET_USD: resourceLimits.maxBudgetUsd
                ? String(resourceLimits.maxBudgetUsd)
                : '',
              N184_QUERY_TIMEOUT_MS: String(resourceLimits.timeoutMs),
              N184_MAX_DISPATCHES_PER_SCAN: process.env.N184_MAX_DISPATCHES_PER_SCAN || '',
              N184_MAX_DISPATCHES_PER_AGENT: process.env.N184_MAX_DISPATCHES_PER_AGENT || '',
              N184_MAX_VAUTRIN_DISPATCHES_PER_SCAN:
                process.env.N184_MAX_VAUTRIN_DISPATCHES_PER_SCAN || '',
              N184_RECOVERY_ALLOW_DISPATCH: process.env.N184_RECOVERY_ALLOW_DISPATCH || '',
            },
          },
        },
        hooks: {
          PreToolUse: [
            { hooks: [createPreToolUseGuard(contextMode)] },
          ],
          PreCompact: [
            { hooks: [createPreCompactHook(containerInput.assistantName)] },
          ],
        },
      },
    })) {
      messageCount++;
      const msgType =
        message.type === 'system'
          ? `system/${(message as { subtype?: string }).subtype}`
          : message.type;
      log(`[msg #${messageCount}] type=${msgType}`);

      if (message.type === 'assistant' && 'uuid' in message) {
        lastAssistantUuid = (message as { uuid: string }).uuid;
      }

      if (message.type === 'system' && message.subtype === 'init') {
        newSessionId = message.session_id;
        log(`Session initialized: ${newSessionId}`);
      }

      if (message.type === 'result') {
        resultCount++;
        const textResult =
          'result' in message ? (message as { result?: string }).result : null;
        log(
          `Result #${resultCount}: subtype=${message.subtype}${textResult ? ` text=${textResult.slice(0, 200)}` : ''}`,
        );

        // Add this query's usage to the cumulative (Redis-persisted) counters
        // so the budget gate above sees it on the next query — including after a
        // restart. Tokens are the real unit under OAuth auth (total_cost_usd is
        // computed from list prices and is ~meaningless on a subscription); we
        // still record usd when the provider reports a real one.
        const usage = (message as { usage?: Usage }).usage;
        const tokens = tokensFromUsage(usage);
        const costUsd = (message as { total_cost_usd?: number }).total_cost_usd;
        if (redisIpc && (tokens > 0 || (typeof costUsd === 'number' && costUsd > 0))) {
          try {
            await recordUsage(redisIpc, { tokens, usd: costUsd, scanId });
            log(
              `Recorded usage: ${tokens} tokens${costUsd ? `, $${costUsd.toFixed(4)}` : ''} (scan: ${scanId ?? 'none'})`,
            );
          } catch (e) {
            log(`Failed to record usage: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
        writeOutput({
          status: message.subtype === 'success' ? 'success' : 'error',
          result: textResult || null,
          newSessionId,
          error: message.subtype === 'success' ? undefined : message.subtype,
        });
      }
    }
  } finally {
    clearTimeout(timeoutHandle);
  }

  ipcPolling = false;

  // Persist session to Redis if available
  if (newSessionId && redisIpc && contextMode === 'group') {
    await redisIpc.setSessionId(AGENT_NAME, newSessionId);
  }

  log(
    `Query done. Messages: ${messageCount}, results: ${resultCount}, closedDuringQuery: ${closedDuringQuery}`,
  );
  return { newSessionId, lastAssistantUuid, closedDuringQuery };
}

// ── Main ─────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  let containerInput: ContainerInput;
  let redisIpc: RedisIPC | null = null;

  // Initialize Redis if backend is redis
  if (IPC_BACKEND === 'redis') {
    redisIpc = new RedisIPC(REDIS_URL, AGENT_NAME);
    await redisIpc.connect();
    log(`Redis IPC connected (agent: ${AGENT_NAME})`);
  }

  // Read ContainerInput from stdin (piped by entrypoint or k8s-entrypoint.sh)
  try {
    const stdinData = await readStdin();
    containerInput = JSON.parse(stdinData);
    try {
      fs.unlinkSync('/tmp/input.json');
    } catch {
      /* may not exist */
    }
    log(`Received input for group: ${containerInput.groupFolder}`);
  } catch (err) {
    writeOutput({
      status: 'error',
      result: null,
      error: `Failed to parse input: ${err instanceof Error ? err.message : String(err)}`,
    });
    process.exit(1);
  }

  const sdkEnv: Record<string, string | undefined> = { ...process.env };
  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const mcpServerPath = path.join(__dirname, 'ipc-mcp-stdio.js');
  const contextMode = getContextMode(containerInput);

  // Restore session from Redis if available
  let sessionId = containerInput.sessionId;
  if (contextMode !== 'group') {
    sessionId = undefined;
    log(`Context mode ${contextMode}: starting without persisted session`);
  } else if (!sessionId && redisIpc) {
    sessionId = (await redisIpc.getSessionId(AGENT_NAME)) || undefined;
    if (sessionId) {
      log(`Restored session from Redis: ${sessionId}`);
    }
  }

  if (IPC_BACKEND === 'file') {
    fs.mkdirSync(IPC_INPUT_DIR, { recursive: true });
    try {
      fs.unlinkSync(IPC_INPUT_CLOSE_SENTINEL);
    } catch {
      /* ignore */
    }
  }

  // Build initial prompt
  let prompt = containerInput.prompt;
  if (containerInput.isScheduledTask) {
    prompt = `[SCHEDULED TASK]\n\n${prompt}`;
  }
  if (IPC_BACKEND === 'file') {
    const pending = drainIpcInput();
    if (pending.length > 0) {
      prompt += '\n' + pending.join('\n');
    }
  }

  // Script phase
  if (containerInput.script && containerInput.isScheduledTask) {
    log('Running task script...');
    const scriptResult = await runScript(containerInput.script);
    if (!scriptResult || !scriptResult.wakeAgent) {
      writeOutput({ status: 'success', result: null });
      return;
    }
    prompt = `[SCHEDULED TASK]\n\nScript output:\n${JSON.stringify(scriptResult.data, null, 2)}\n\nInstructions:\n${containerInput.prompt}`;
  }

  // Query loop
  let resumeAt: string | undefined;
  try {
    while (true) {
      log(`Starting query (session: ${sessionId || 'new'})...`);

      const queryResult = await runQuery(
        prompt,
        sessionId,
        mcpServerPath,
        containerInput,
        sdkEnv,
        redisIpc,
        resumeAt,
      );
      if (queryResult.newSessionId) {
        sessionId = queryResult.newSessionId;
      }
      if (queryResult.lastAssistantUuid) {
        resumeAt = queryResult.lastAssistantUuid;
      }

      if (queryResult.closedDuringQuery) {
        log('Close signal consumed during query, exiting');
        break;
      }

      writeOutput({ status: 'success', result: null, newSessionId: sessionId });

      log('Query ended, waiting for next message...');

      // Wait for next message
      if (IPC_BACKEND === 'redis' && redisIpc) {
        // Redis: wait for next message on input channel
        const gen = redisIpc.subscribe();
        const { value, done } = await gen.next();
        if (done || value === null) {
          log('Close signal received, exiting');
          break;
        }
        prompt = value;
      } else {
        // File: poll for next IPC message
        const nextMessage = await waitForIpcMessage();
        if (nextMessage === null) {
          log('Close sentinel received, exiting');
          break;
        }
        prompt = nextMessage;
      }

      log(`Got new message (${prompt.length} chars), starting new query`);
    }
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : String(err);
    log(`Agent error: ${errorMessage}`);
    writeOutput({
      status: 'error',
      result: null,
      newSessionId: sessionId,
      error: errorMessage,
    });
    process.exit(1);
  } finally {
    if (redisIpc) {
      await redisIpc.close();
    }
  }
}

main();
