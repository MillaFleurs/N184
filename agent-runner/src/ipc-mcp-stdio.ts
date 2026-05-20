/**
 * Stdio MCP Server for N184
 * Standalone process that agent teams subagents can inherit.
 *
 * Supports two IPC backends:
 *   - "file": Write JSON files to /workspace/ipc/ (NanoClaw compat)
 *   - "redis": Publish/push to Redis channels/lists (k8s native)
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { CronExpressionParser } from 'cron-parser';
import { Redis as IORedis } from 'ioredis';
import { getRegistry, type Provider } from './providers.js';

const IPC_BACKEND = process.env.IPC_BACKEND || 'file';
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const AGENT_NAME = process.env.N184_AGENT_NAME || 'agent';

// File-based IPC paths
const IPC_DIR = '/workspace/ipc';
const MESSAGES_DIR = path.join(IPC_DIR, 'messages');
const TASKS_DIR = path.join(IPC_DIR, 'tasks');

// Context from environment variables
const chatJid = process.env.NANOCLAW_CHAT_JID!;
const groupFolder = process.env.NANOCLAW_GROUP_FOLDER!;
const isMain = process.env.NANOCLAW_IS_MAIN === '1';
const contextMode = process.env.N184_CONTEXT_MODE || 'group';

const SWARM_KILL_KEY = 'n184:swarm:kill';
const SWARM_LEDGER_TTL_SECONDS = 24 * 60 * 60;

// Redis client (lazy, only created if backend is redis)
let redis: IORedis | null = null;
function getRedis(): IORedis {
  if (!redis) {
    redis = new IORedis(REDIS_URL, { lazyConnect: false, maxRetriesPerRequest: 3 });
  }
  return redis;
}

// ── IPC Dispatch ─────────────────────────────────────────────────────

function writeIpcFile(dir: string, data: object): string {
  fs.mkdirSync(dir, { recursive: true });
  const filename = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`;
  const filepath = path.join(dir, filename);
  const tempPath = `${filepath}.tmp`;
  fs.writeFileSync(tempPath, JSON.stringify(data, null, 2));
  fs.renameSync(tempPath, filepath);
  return filename;
}

async function dispatchMessage(data: object): Promise<void> {
  if (IPC_BACKEND === 'redis') {
    const target = (data as Record<string, string>).chatJid || 'broadcast';
    await getRedis().publish(`n184:messages:${target}`, JSON.stringify(data));
  } else {
    writeIpcFile(MESSAGES_DIR, data);
  }
}

async function dispatchTask(data: object): Promise<void> {
  if (IPC_BACKEND === 'redis') {
    await getRedis().lpush('n184:tasks', JSON.stringify(data));
  } else {
    writeIpcFile(TASKS_DIR, data);
  }
}

function parsePositiveInt(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function inferScanId(prompt: string): string {
  const match = prompt.match(/\bscan[_-]?id\s*[:=]\s*([A-Za-z0-9_.:-]+)/i);
  return match?.[1] || groupFolder || 'default';
}

function dispatchFingerprint(scanId: string, targetAgent: string, prompt: string): string {
  return crypto
    .createHash('sha256')
    .update(`${scanId}\0${targetAgent}\0${prompt}`)
    .digest('hex')
    .slice(0, 24);
}

async function enforceDispatchBudget(
  scanId: string,
  targetAgent: string,
  prompt: string,
): Promise<string | null> {
  if (IPC_BACKEND !== 'redis') return null;
  const client = getRedis();
  const kill = await client.get(SWARM_KILL_KEY);
  if (kill && process.env.N184_RECOVERY_ALLOW_DISPATCH !== '1') {
    return `Swarm dispatch is paused by kill switch: ${kill}`;
  }
  if (contextMode === 'recovery' && process.env.N184_RECOVERY_ALLOW_DISPATCH !== '1') {
    return 'Honoré is in restart recovery mode. Inspect state and ask the operator before dispatching new agents.';
  }

  const totalLimit = parsePositiveInt('N184_MAX_DISPATCHES_PER_SCAN', 20);
  const agentLimit = parsePositiveInt('N184_MAX_DISPATCHES_PER_AGENT', 10);
  const vautrinLimit = parsePositiveInt('N184_MAX_VAUTRIN_DISPATCHES_PER_SCAN', 10);
  const root = `n184:swarm:${scanId}`;
  const fingerprint = dispatchFingerprint(scanId, targetAgent, prompt);
  const dedupeKey = `${root}:dedupe:${fingerprint}`;
  const totalKey = `${root}:dispatch_total`;
  const agentKey = `${root}:dispatch_agent:${targetAgent}`;

  const dedupeSet = await client.set(dedupeKey, '1', 'EX', SWARM_LEDGER_TTL_SECONDS, 'NX');
  if (!dedupeSet) {
    return `Duplicate dispatch blocked for scan_id=${scanId} target_agent=${targetAgent}.`;
  }

  const [total, agentCount] = await Promise.all([
    client.incr(totalKey),
    client.incr(agentKey),
  ]);
  await Promise.all([
    client.expire(totalKey, SWARM_LEDGER_TTL_SECONDS),
    client.expire(agentKey, SWARM_LEDGER_TTL_SECONDS),
  ]);

  if (total > totalLimit) {
    return `Dispatch budget exceeded for scan_id=${scanId}: ${total}/${totalLimit} total dispatches.`;
  }
  if (agentCount > agentLimit) {
    return `Dispatch budget exceeded for scan_id=${scanId} target_agent=${targetAgent}: ${agentCount}/${agentLimit}.`;
  }
  if (targetAgent === 'vautrin' && agentCount > vautrinLimit) {
    return `Vautrin dispatch budget exceeded for scan_id=${scanId}: ${agentCount}/${vautrinLimit}.`;
  }
  return null;
}

// ── MCP Server ───────────────────────────────────────────────────────

const serverName = IPC_BACKEND === 'redis' ? 'n184' : 'nanoclaw';

const server = new McpServer({
  name: serverName,
  version: '1.0.0',
});

server.tool(
  'send_message',
  "Send a message to the user or group immediately while you're still running. Use this for progress updates or to send multiple messages.",
  {
    text: z.string().describe('The message text to send'),
    sender: z
      .string()
      .optional()
      .describe('Your role/identity name (e.g. "Researcher")'),
  },
  async (args) => {
    const data = {
      type: 'message',
      chatJid,
      text: args.text,
      sender: args.sender || undefined,
      groupFolder,
      agentName: AGENT_NAME,
      timestamp: new Date().toISOString(),
    };

    await dispatchMessage(data);
    return { content: [{ type: 'text' as const, text: 'Message sent.' }] };
  },
);

server.tool(
  'schedule_task',
  `Schedule a recurring or one-time task. The task will run as a full agent with access to all tools.

CONTEXT MODE:
- "group": Task runs with chat history and memory
- "isolated": Fresh session (include all context in prompt)

SCHEDULE VALUE FORMAT (all times are LOCAL timezone):
- cron: "*/5 * * * *" for every 5 min, "0 9 * * *" for daily at 9am
- interval: Milliseconds (e.g., "300000" for 5 min)
- once: Local time "2026-02-01T15:30:00" (no Z suffix)`,
  {
    prompt: z.string().describe('What the agent should do'),
    schedule_type: z.enum(['cron', 'interval', 'once']),
    schedule_value: z.string(),
    context_mode: z.enum(['group', 'isolated']).default('group'),
    target_group_jid: z.string().optional().describe('(Main only) Target group JID'),
    target_agent: z.string().optional().describe('Target agent name (e.g., "rastignac", "vautrin", "bianchon", "lousteau", "fil-de-soie")'),
    provider: z
      .string()
      .optional()
      .describe(
        'AI provider to dispatch this agent to. Must match a name registered in providers/registry.yaml ' +
          '(default: "anthropic", "openai", "deepseek"; users may add more, e.g. "ollama"). ' +
          'Use list_providers to see what is available. Omit to use the registry default.',
      ),
    model: z
      .string()
      .optional()
      .describe(
        'Model name within the chosen provider (e.g., "claude-opus-4-7", "gpt-4o", "deepseek-chat"). ' +
          'Passed through opaquely — newly-released models work without code changes. ' +
          'Omit to use the provider\'s default_model.',
      ),
    scan_id: z.string().optional().describe('Scan identifier used for dispatch budgeting and restart recovery.'),
    resource_limits: z
      .object({
        max_turns: z.number().int().positive().optional(),
        max_budget_usd: z.number().positive().optional(),
        timeout_ms: z.number().int().positive().optional(),
      })
      .optional()
      .describe('Optional per-agent leash set by Honoré: max turns, USD budget, and wall-clock timeout.'),
    script: z.string().optional().describe('Optional bash script to run before waking agent'),
  },
  async (args) => {
    // Validate schedule_value
    if (args.schedule_type === 'cron') {
      try {
        CronExpressionParser.parse(args.schedule_value);
      } catch {
        return {
          content: [{ type: 'text' as const, text: `Invalid cron: "${args.schedule_value}".` }],
          isError: true,
        };
      }
    } else if (args.schedule_type === 'interval') {
      const ms = parseInt(args.schedule_value, 10);
      if (isNaN(ms) || ms <= 0) {
        return {
          content: [{ type: 'text' as const, text: `Invalid interval: "${args.schedule_value}".` }],
          isError: true,
        };
      }
    } else if (args.schedule_type === 'once') {
      if (/[Zz]$/.test(args.schedule_value) || /[+-]\d{2}:\d{2}$/.test(args.schedule_value)) {
        return {
          content: [{ type: 'text' as const, text: `Use local time without timezone. Got "${args.schedule_value}".` }],
          isError: true,
        };
      }
      const date = new Date(args.schedule_value);
      if (isNaN(date.getTime())) {
        return {
          content: [{ type: 'text' as const, text: `Invalid timestamp: "${args.schedule_value}".` }],
          isError: true,
        };
      }
    }

    // Validate provider against the registry. Model strings are passed
    // through opaquely (no allowlist) so future model releases work.
    if (args.provider) {
      try {
        const reg = getRegistry();
        if (!reg.get(args.provider)) {
          return {
            content: [
              {
                type: 'text' as const,
                text:
                  `Unknown provider "${args.provider}". Registered providers: ${reg.names().join(', ')}. ` +
                  'Use list_providers to inspect, or register_provider to add one.',
              },
            ],
            isError: true,
          };
        }
      } catch (err) {
        return {
          content: [
            {
              type: 'text' as const,
              text: `Provider registry error: ${err instanceof Error ? err.message : String(err)}`,
            },
          ],
          isError: true,
        };
      }
    }

    const targetJid = isMain && args.target_group_jid ? args.target_group_jid : chatJid;
    const taskId = `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const targetAgent = args.target_agent || 'generic';
    const scanId = args.scan_id || inferScanId(args.prompt);

    const budgetError = await enforceDispatchBudget(scanId, targetAgent, args.prompt);
    if (budgetError) {
      return {
        content: [{ type: 'text' as const, text: budgetError }],
        isError: true,
      };
    }

    const data = {
      type: 'schedule_task',
      taskId,
      prompt: args.prompt,
      script: args.script || undefined,
      schedule_type: args.schedule_type,
      schedule_value: args.schedule_value,
      context_mode: args.context_mode || 'group',
      targetJid,
      targetAgent: args.target_agent || undefined,
      provider: args.provider || undefined,
      model: args.model || undefined,
      scan_id: scanId,
      resource_limits: args.resource_limits || undefined,
      createdBy: groupFolder,
      agentName: AGENT_NAME,
      timestamp: new Date().toISOString(),
    };

    await dispatchTask(data);

    return {
      content: [{ type: 'text' as const, text: `Task ${taskId} scheduled: ${args.schedule_type} - ${args.schedule_value}` }],
    };
  },
);

server.tool(
  'list_tasks',
  "List all scheduled tasks.",
  {},
  async () => {
    if (IPC_BACKEND === 'redis') {
      const tasks = await getRedis().get('n184:current_tasks');
      if (!tasks) {
        return { content: [{ type: 'text' as const, text: 'No scheduled tasks found.' }] };
      }
      return { content: [{ type: 'text' as const, text: `Scheduled tasks:\n${tasks}` }] };
    }

    // File-based fallback
    const tasksFile = path.join(IPC_DIR, 'current_tasks.json');
    try {
      if (!fs.existsSync(tasksFile)) {
        return { content: [{ type: 'text' as const, text: 'No scheduled tasks found.' }] };
      }
      const allTasks = JSON.parse(fs.readFileSync(tasksFile, 'utf-8'));
      const tasks = isMain
        ? allTasks
        : allTasks.filter((t: { groupFolder: string }) => t.groupFolder === groupFolder);
      if (tasks.length === 0) {
        return { content: [{ type: 'text' as const, text: 'No scheduled tasks found.' }] };
      }
      const formatted = tasks
        .map(
          (t: { id: string; prompt: string; schedule_type: string; schedule_value: string; status: string; next_run: string }) =>
            `- [${t.id}] ${t.prompt.slice(0, 50)}... (${t.schedule_type}: ${t.schedule_value}) - ${t.status}`,
        )
        .join('\n');
      return { content: [{ type: 'text' as const, text: `Scheduled tasks:\n${formatted}` }] };
    } catch (err) {
      return {
        content: [{ type: 'text' as const, text: `Error: ${err instanceof Error ? err.message : String(err)}` }],
      };
    }
  },
);

server.tool(
  'swarm_status',
  'Inspect Honoré swarm leash state: dispatch budgets, kill switch, queued Vautrin work, and claimed-but-unacked Vautrin work.',
  {
    scan_id: z.string().optional().describe('Scan identifier to inspect. Omit to use the current group folder.'),
  },
  async (args) => {
    if (IPC_BACKEND !== 'redis') {
      return { content: [{ type: 'text' as const, text: 'Swarm status is only available with Redis IPC.' }] };
    }
    const client = getRedis();
    const scanId = args.scan_id || groupFolder || 'default';
    const root = `n184:swarm:${scanId}`;
    const [kill, queued, processing, total, vautrin] = await Promise.all([
      client.get(SWARM_KILL_KEY),
      client.llen('n184:vautrin-queue'),
      client.llen('n184:vautrin-processing'),
      client.get(`${root}:dispatch_total`),
      client.get(`${root}:dispatch_agent:vautrin`),
    ]);
    const text = [
      `scan_id: ${scanId}`,
      `kill_switch: ${kill || 'off'}`,
      `vautrin_queue: ${queued}`,
      `vautrin_processing: ${processing}`,
      `dispatch_total_24h: ${total || '0'}`,
      `vautrin_dispatches_24h: ${vautrin || '0'}`,
      `limits: total=${parsePositiveInt('N184_MAX_DISPATCHES_PER_SCAN', 20)}, per_agent=${parsePositiveInt('N184_MAX_DISPATCHES_PER_AGENT', 10)}, vautrin=${parsePositiveInt('N184_MAX_VAUTRIN_DISPATCHES_PER_SCAN', 10)}`,
    ].join('\n');
    return { content: [{ type: 'text' as const, text }] };
  },
);

server.tool(
  'kill_swarm',
  'Emergency stop for runaway subagents. Pauses new dispatch, signals running subagents to close, and optionally drains pending Vautrin queue items.',
  {
    reason: z.string().optional(),
    drain_vautrin_queue: z.boolean().default(true),
  },
  async (args) => {
    if (IPC_BACKEND !== 'redis') {
      return { content: [{ type: 'text' as const, text: 'Kill switch is only available with Redis IPC.' }], isError: true };
    }
    const client = getRedis();
    const payload = JSON.stringify({
      reason: args.reason || 'operator_requested',
      by: AGENT_NAME,
      timestamp: new Date().toISOString(),
    });
    await client.set(SWARM_KILL_KEY, payload);
    const targets = ['vautrin', 'rastignac', 'bianchon', 'lousteau', 'fil-de-soie'];
    await Promise.all(targets.map((agent) => client.publish(`n184:close:${agent}`, 'close')));
    if (args.drain_vautrin_queue) {
      await client.del('n184:vautrin-queue');
    }
    return {
      content: [
        {
          type: 'text' as const,
          text: `Swarm kill switch engaged. Signaled: ${targets.join(', ')}. Vautrin queue drained: ${args.drain_vautrin_queue}.`,
        },
      ],
    };
  },
);

server.tool(
  'resume_swarm',
  'Clear the swarm kill switch so Honoré can dispatch subagents again.',
  {},
  async () => {
    if (IPC_BACKEND !== 'redis') {
      return { content: [{ type: 'text' as const, text: 'Resume is only available with Redis IPC.' }], isError: true };
    }
    await getRedis().del(SWARM_KILL_KEY);
    return { content: [{ type: 'text' as const, text: 'Swarm kill switch cleared.' }] };
  },
);

server.tool(
  'pause_task',
  'Pause a scheduled task.',
  { task_id: z.string() },
  async (args) => {
    await dispatchTask({
      type: 'pause_task',
      taskId: args.task_id,
      groupFolder,
      isMain,
      timestamp: new Date().toISOString(),
    });
    return { content: [{ type: 'text' as const, text: `Task ${args.task_id} pause requested.` }] };
  },
);

server.tool(
  'resume_task',
  'Resume a paused task.',
  { task_id: z.string() },
  async (args) => {
    await dispatchTask({
      type: 'resume_task',
      taskId: args.task_id,
      groupFolder,
      isMain,
      timestamp: new Date().toISOString(),
    });
    return { content: [{ type: 'text' as const, text: `Task ${args.task_id} resume requested.` }] };
  },
);

server.tool(
  'cancel_task',
  'Cancel and delete a scheduled task.',
  { task_id: z.string() },
  async (args) => {
    await dispatchTask({
      type: 'cancel_task',
      taskId: args.task_id,
      groupFolder,
      isMain,
      timestamp: new Date().toISOString(),
    });
    return { content: [{ type: 'text' as const, text: `Task ${args.task_id} cancellation requested.` }] };
  },
);

server.tool(
  'update_task',
  'Update an existing scheduled task.',
  {
    task_id: z.string(),
    prompt: z.string().optional(),
    schedule_type: z.enum(['cron', 'interval', 'once']).optional(),
    schedule_value: z.string().optional(),
    script: z.string().optional(),
  },
  async (args) => {
    const data: Record<string, string | undefined> = {
      type: 'update_task',
      taskId: args.task_id,
      groupFolder,
      isMain: String(isMain),
      timestamp: new Date().toISOString(),
    };
    if (args.prompt !== undefined) data.prompt = args.prompt;
    if (args.script !== undefined) data.script = args.script;
    if (args.schedule_type !== undefined) data.schedule_type = args.schedule_type;
    if (args.schedule_value !== undefined) data.schedule_value = args.schedule_value;

    await dispatchTask(data);
    return { content: [{ type: 'text' as const, text: `Task ${args.task_id} update requested.` }] };
  },
);

server.tool(
  'register_group',
  'Register a new chat/group. Main group only.',
  {
    jid: z.string(),
    name: z.string(),
    folder: z.string(),
    trigger: z.string(),
  },
  async (args) => {
    if (!isMain) {
      return { content: [{ type: 'text' as const, text: 'Only the main group can register new groups.' }], isError: true };
    }
    await dispatchTask({
      type: 'register_group',
      jid: args.jid,
      name: args.name,
      folder: args.folder,
      trigger: args.trigger,
      timestamp: new Date().toISOString(),
    });
    return { content: [{ type: 'text' as const, text: `Group "${args.name}" registered.` }] };
  },
);

// ── Provider Registry Tools ──────────────────────────────────────────
//
// These let Honoré introspect which AI backends are available and add
// new ones at runtime (e.g., when a user spins up a local Ollama).
// Runtime additions are in-memory only — they don't get written back
// to providers/registry.yaml.

server.tool(
  'list_providers',
  'List AI providers available for dispatching sub-agents (anthropic, openai, deepseek by default; users may add more like ollama). Returns provider name, type, default model, and runtime kind.',
  {},
  async () => {
    try {
      const reg = getRegistry();
      const lines: string[] = [];
      for (const p of reg.list()) {
        const keyHint = p.api_key_env ? `key:${p.api_key_env}` : 'key:none';
        lines.push(
          `- ${p.name} (type=${p.type}, runtime=${p.runtime}, default_model=${p.default_model}, ${keyHint})${p.notes ? ` — ${p.notes}` : ''}`,
        );
      }
      return {
        content: [
          { type: 'text' as const, text: `Registered providers:\n${lines.join('\n')}` },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: 'text' as const,
            text: `Provider registry error: ${err instanceof Error ? err.message : String(err)}`,
          },
        ],
        isError: true,
      };
    }
  },
);

server.tool(
  'register_provider',
  `Register a new AI provider at runtime (in-memory only — not persisted to registry.yaml).
Use this to hot-add a backend Honoré can dispatch agents against (e.g., a freshly-started
Ollama service). To make a provider permanent, edit providers/registry.local.yaml in the
repo and re-deploy.

The api_key_env field is the NAME of the environment variable holding the key
(e.g., "OPENAI_API_KEY"). The actual key value is never passed through this tool —
it must already be present in the pod's environment (mounted from the n184-api-keys
Secret). For providers that don't need a key (e.g., local Ollama), pass an empty string.`,
  {
    name: z.string().describe('Provider identifier (e.g., "ollama")'),
    type: z
      .enum(['anthropic', 'openai', 'openai-compat'])
      .describe('Wire protocol family'),
    base_url: z.string().describe('HTTP endpoint, e.g., "http://ollama.n184.svc.cluster.local:11434/v1"'),
    api_key_env: z
      .string()
      .describe('NAME of the env var holding the API key, or "" if not needed. NEVER pass an actual key here.'),
    default_model: z.string().describe('Model used when caller doesn\'t specify one'),
    runtime: z.enum(['claude-sdk', 'openai-sdk']).describe('Which runtime entrypoint to launch'),
    notes: z.string().optional(),
  },
  async (args) => {
    // Refuse anything that looks like an API key was pasted into api_key_env.
    // Real env-var names are uppercase identifiers; keys are typically long random strings.
    if (args.api_key_env && (args.api_key_env.length > 64 || /[^A-Za-z0-9_]/.test(args.api_key_env))) {
      return {
        content: [
          {
            type: 'text' as const,
            text:
              `api_key_env looks like a value, not an env-var name. ` +
              `Pass the NAME of the env var (e.g., "OPENAI_API_KEY"), not the secret itself.`,
          },
        ],
        isError: true,
      };
    }
    try {
      const reg = getRegistry();
      const provider: Provider = {
        name: args.name,
        type: args.type,
        base_url: args.base_url,
        api_key_env: args.api_key_env,
        default_model: args.default_model,
        runtime: args.runtime,
        notes: args.notes ?? '',
      };
      reg.registerOverlay(provider);
      return {
        content: [
          {
            type: 'text' as const,
            text:
              `Provider "${args.name}" registered (in-memory). ` +
              `To persist across restarts, add it to providers/registry.local.yaml.`,
          },
        ],
      };
    } catch (err) {
      return {
        content: [
          {
            type: 'text' as const,
            text: `Failed to register provider: ${err instanceof Error ? err.message : String(err)}`,
          },
        ],
        isError: true,
      };
    }
  },
);

// Start the stdio transport
const transport = new StdioServerTransport();
await server.connect(transport);
