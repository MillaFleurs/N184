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
import { CronExpressionParser } from 'cron-parser';
import { Redis as IORedis } from 'ioredis';

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
    target_agent: z.string().optional().describe('Target agent name (e.g., "rastignac", "vautrin", "bianchon")'),
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

    const targetJid = isMain && args.target_group_jid ? args.target_group_jid : chatJid;
    const taskId = `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

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

// Start the stdio transport
const transport = new StdioServerTransport();
await server.connect(transport);
