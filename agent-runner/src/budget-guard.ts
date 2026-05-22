/**
 * Loop-safe budget cap + restart circuit-breaker for N184 agents.
 *
 * Why this exists: Honoré runs as a k8s Deployment, so a crash (e.g. OOM —
 * see the "Squashing bugs for OOM runs" history) restarts it forever. The
 * per-query `N184_MAX_BUDGET_USD` ceiling the SDK enforces resets on every
 * restart, so it cannot stop a crash loop from burning the operator's Claude
 * capacity. The accounting here lives in Redis, which outlives the pod, so a
 * cumulative cap and a restart count survive restarts.
 *
 * Unit of measure: TOKENS, not dollars. Honoré authenticates with a Claude
 * subscription OAuth token (CLAUDE_CODE_OAUTH_TOKEN), under which the SDK's
 * `total_cost_usd` is computed from API list prices and is effectively
 * meaningless (often 0) — the thing actually being consumed is plan capacity,
 * i.e. tokens. So the cap is primarily token-based (the SDK `usage` is always
 * populated regardless of auth). Dollar caps remain available as an optional
 * secondary axis for API-key-billed providers (DeepSeek/OpenAI, later).
 *
 * All logic is pure and depends only on the small `KVStore` interface below,
 * so it unit-tests against an in-memory fake with no Redis running. `RedisIPC`
 * implements `KVStore`; tests pass a fake.
 */

// ── Storage abstraction ──────────────────────────────────────────────

/**
 * The minimal Redis surface this module needs. `RedisIPC` satisfies it.
 * `expireIfNew` sets a TTL only when the key currently has none, so a rolling
 * counter's window is fixed at first write rather than extended on every bump.
 */
export interface KVStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  del(key: string): Promise<void>;
  incr(key: string): Promise<number>;
  incrByFloat(key: string, delta: number): Promise<number>;
  expireIfNew(key: string, seconds: number): Promise<void>;
}

// ── Keys + TTLs ──────────────────────────────────────────────────────

// Daily key lingers a little past midnight UTC so a run straddling midnight
// still sees the spend it just made; the scan key covers a long single scan.
const DAY_TTL_SEC = 60 * 60 * 36; // 36h
const SCAN_TTL_SEC = 60 * 60 * 24; // 24h

export function dayKey(now: Date): string {
  return `n184:budget:day:${now.toISOString().slice(0, 10)}`;
}
export function scanKey(scanId: string): string {
  return `n184:budget:scan:${scanId}`;
}
function restartKey(agent: string): string {
  return `n184:restart:${agent}`;
}
function circuitKey(agent: string): string {
  return `n184:circuit:${agent}`;
}

// ── Budget cap ───────────────────────────────────────────────────────

export interface BudgetConfig {
  /** Cumulative token/day cap across all queries this Redis sees. Primary cap. */
  dailyTokenCap?: number;
  /** Cumulative token cap for a single scan_id. */
  scanTokenCap?: number;
  /** Optional USD/day cap — only meaningful for API-key-billed providers. */
  dailyCapUsd?: number;
  /** Optional USD cap for a single scan_id. */
  scanCapUsd?: number;
}

export interface BudgetStatus {
  allowed: boolean;
  reason?: string;
  dailyTokens: number;
  scanTokens: number;
  dailyUsd: number;
  scanUsd: number;
}

/** Shape of the SDK result message's `usage` we care about. */
export interface Usage {
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
}

/**
 * Total tokens a query consumed, summed across all token classes the SDK
 * reports. Cache reads are weighted the same as fresh input here — a
 * deliberate over-estimate, since the cap should err on the side of stopping
 * sooner rather than overshooting the operator's plan capacity.
 */
export function tokensFromUsage(usage: Usage | null | undefined): number {
  if (!usage) return 0;
  return (
    (usage.input_tokens ?? 0) +
    (usage.output_tokens ?? 0) +
    (usage.cache_creation_input_tokens ?? 0) +
    (usage.cache_read_input_tokens ?? 0)
  );
}

async function readFloat(store: KVStore, key: string): Promise<number> {
  const v = await store.get(key);
  if (v == null) return 0;
  const n = Number.parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Add a query's usage to the cumulative day (and scan, if known) counters.
 * `tokens` is the primary axis; `usd` is recorded only when > 0 (i.e. an
 * API-key provider that reports real cost). No-op when both are zero.
 * Call once per SDK `result` message.
 */
export async function recordUsage(
  store: KVStore,
  opts: { tokens: number; usd?: number; scanId?: string; now?: Date },
): Promise<void> {
  const now = opts.now ?? new Date();
  const tokens = opts.tokens > 0 ? opts.tokens : 0;
  const usd = opts.usd && opts.usd > 0 ? opts.usd : 0;
  if (tokens === 0 && usd === 0) return;

  const bump = async (base: string, ttl: number) => {
    if (tokens > 0) {
      await store.incrByFloat(`${base}:tokens`, tokens);
      await store.expireIfNew(`${base}:tokens`, ttl);
    }
    if (usd > 0) {
      await store.incrByFloat(`${base}:usd`, usd);
      await store.expireIfNew(`${base}:usd`, ttl);
    }
  };

  await bump(dayKey(now), DAY_TTL_SEC);
  if (opts.scanId) await bump(scanKey(opts.scanId), SCAN_TTL_SEC);
}

/**
 * Decide whether another query may run given cumulative usage so far. A cap of
 * `undefined` means "no limit on that axis". Checked *before* a query starts,
 * so the cap holds even after a restart that reset the SDK's per-query budget.
 */
export async function checkBudget(
  store: KVStore,
  opts: { config: BudgetConfig; scanId?: string; now?: Date },
): Promise<BudgetStatus> {
  const now = opts.now ?? new Date();
  const dk = dayKey(now);
  const sk = opts.scanId ? scanKey(opts.scanId) : null;

  const dailyTokens = await readFloat(store, `${dk}:tokens`);
  const scanTokens = sk ? await readFloat(store, `${sk}:tokens`) : 0;
  const dailyUsd = await readFloat(store, `${dk}:usd`);
  const scanUsd = sk ? await readFloat(store, `${sk}:usd`) : 0;

  const { dailyTokenCap, scanTokenCap, dailyCapUsd, scanCapUsd } = opts.config;
  const totals = { dailyTokens, scanTokens, dailyUsd, scanUsd };

  if (dailyTokenCap != null && dailyTokens >= dailyTokenCap) {
    return {
      allowed: false,
      reason: `daily token cap reached (${fmt(dailyTokens)} / ${fmt(dailyTokenCap)} tokens)`,
      ...totals,
    };
  }
  if (scanTokenCap != null && sk && scanTokens >= scanTokenCap) {
    return {
      allowed: false,
      reason: `scan token cap reached (${fmt(scanTokens)} / ${fmt(scanTokenCap)} tokens) for ${opts.scanId}`,
      ...totals,
    };
  }
  if (dailyCapUsd != null && dailyUsd >= dailyCapUsd) {
    return {
      allowed: false,
      reason: `daily budget cap reached ($${dailyUsd.toFixed(2)} / $${dailyCapUsd.toFixed(2)})`,
      ...totals,
    };
  }
  if (scanCapUsd != null && sk && scanUsd >= scanCapUsd) {
    return {
      allowed: false,
      reason: `scan budget cap reached ($${scanUsd.toFixed(2)} / $${scanCapUsd.toFixed(2)}) for ${opts.scanId}`,
      ...totals,
    };
  }
  return { allowed: true, ...totals };
}

function fmt(n: number): string {
  return Math.round(n).toLocaleString('en-US');
}

// ── Restart circuit-breaker ──────────────────────────────────────────

export interface BreakerConfig {
  /** Restarts within `windowSec` beyond this many trips the breaker. */
  maxRestarts: number;
  windowSec: number;
}

export interface BreakerState {
  reason: string;
  since: string; // ISO 8601
  count: number;
}

/** Returns the tripped state, or null if the breaker is closed. */
export async function isTripped(store: KVStore, agent: string): Promise<BreakerState | null> {
  const v = await store.get(circuitKey(agent));
  if (!v) return null;
  try {
    return JSON.parse(v) as BreakerState;
  } catch {
    return { reason: v, since: '', count: 0 };
  }
}

export async function tripBreaker(
  store: KVStore,
  agent: string,
  reason: string,
  count: number,
  now?: Date,
): Promise<BreakerState> {
  const state: BreakerState = { reason, since: (now ?? new Date()).toISOString(), count };
  await store.set(circuitKey(agent), JSON.stringify(state));
  return state;
}

/** Operator override: clear the breaker and the restart counter. */
export async function resetBreaker(store: KVStore, agent: string): Promise<void> {
  await store.del(circuitKey(agent));
  await store.del(restartKey(agent));
}

/**
 * Record one process start and decide whether the restart rate trips the
 * breaker. Call once at agent startup. The counter has a fixed window (TTL set
 * on first increment), so a healthy long-running pod increments once and the
 * key expires; only rapid repeated restarts accumulate enough to trip.
 */
export async function recordRestart(
  store: KVStore,
  agent: string,
  config: BreakerConfig,
  now?: Date,
): Promise<{ count: number; tripped: boolean; state?: BreakerState }> {
  const k = restartKey(agent);
  const count = await store.incr(k);
  await store.expireIfNew(k, config.windowSec);

  if (count > config.maxRestarts) {
    const reason = `${count} restarts within ${config.windowSec}s (limit ${config.maxRestarts})`;
    const state = await tripBreaker(store, agent, reason, count, now);
    return { count, tripped: true, state };
  }
  return { count, tripped: false };
}

// ── Config from environment ──────────────────────────────────────────

type Env = Record<string, string | undefined>;

function envFloat(env: Env, name: string): number | undefined {
  const raw = env[name];
  if (!raw) return undefined;
  const n = Number.parseFloat(raw);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

function envInt(env: Env, name: string, dflt: number): number {
  const raw = env[name];
  if (!raw) return dflt;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : dflt;
}

export function loadBudgetConfig(env: Env = process.env): BudgetConfig {
  return {
    dailyTokenCap: envFloat(env, 'N184_DAILY_TOKEN_CAP'),
    scanTokenCap: envFloat(env, 'N184_SCAN_TOKEN_CAP'),
    dailyCapUsd: envFloat(env, 'N184_DAILY_BUDGET_CAP_USD'),
    scanCapUsd: envFloat(env, 'N184_SCAN_BUDGET_CAP_USD'),
  };
}

export function loadBreakerConfig(env: Env = process.env): BreakerConfig {
  return {
    maxRestarts: envInt(env, 'N184_MAX_RESTARTS', 5),
    windowSec: envInt(env, 'N184_RESTART_WINDOW_SEC', 600),
  };
}

/** Message text that clears a tripped breaker when the operator sends it. */
export function resetToken(env: Env = process.env): string {
  return env.N184_BREAKER_RESET_TOKEN || '/resume';
}

/** Scan id for the current run, if any (env wins; falls back to input fields). */
export function resolveScanId(
  input: { scan_id?: string; scanId?: string } | undefined,
  env: Env = process.env,
): string | undefined {
  return env.N184_SCAN_ID || input?.scan_id || input?.scanId || undefined;
}
