/**
 * Unit tests for budget-guard. Runs against an in-memory KVStore fake — no
 * Redis required. Run with: `npm test` (compiles, then `node --test`).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  checkBudget,
  recordUsage,
  tokensFromUsage,
  recordRestart,
  isTripped,
  resetBreaker,
  loadBudgetConfig,
  loadBreakerConfig,
  resetToken,
  resolveScanId,
  type KVStore,
} from './budget-guard.js';

/** Faithful-enough in-memory KVStore. Tracks TTLs so we can assert windowing. */
class FakeKV implements KVStore {
  store = new Map<string, string>();
  ttls = new Map<string, number>();

  async get(k: string): Promise<string | null> {
    return this.store.has(k) ? this.store.get(k)! : null;
  }
  async set(k: string, v: string): Promise<void> {
    this.store.set(k, v);
  }
  async del(k: string): Promise<void> {
    this.store.delete(k);
    this.ttls.delete(k);
  }
  async incr(k: string): Promise<number> {
    const n = (this.store.has(k) ? Number.parseInt(this.store.get(k)!, 10) : 0) + 1;
    this.store.set(k, String(n));
    return n;
  }
  async incrByFloat(k: string, d: number): Promise<number> {
    const n = (this.store.has(k) ? Number.parseFloat(this.store.get(k)!) : 0) + d;
    this.store.set(k, String(n));
    return n;
  }
  async expireIfNew(k: string, sec: number): Promise<void> {
    if (!this.ttls.has(k)) this.ttls.set(k, sec);
  }
}

const NOW = new Date('2026-05-22T12:00:00Z');

// ── Token usage helper ───────────────────────────────────────────────

test('tokensFromUsage sums all token classes; null-safe', () => {
  assert.equal(tokensFromUsage(undefined), 0);
  assert.equal(tokensFromUsage(null), 0);
  assert.equal(tokensFromUsage({ input_tokens: 100, output_tokens: 50 }), 150);
  assert.equal(
    tokensFromUsage({
      input_tokens: 100,
      output_tokens: 50,
      cache_creation_input_tokens: 200,
      cache_read_input_tokens: 1000,
    }),
    1350,
  );
});

// ── Budget cap (token-primary) ───────────────────────────────────────

test('daily token cap: allows under cap, blocks at/over cap', async () => {
  const kv = new FakeKV();
  const config = { dailyTokenCap: 1000 };

  await recordUsage(kv, { tokens: 600, now: NOW });
  let s = await checkBudget(kv, { config, now: NOW });
  assert.equal(s.allowed, true);
  assert.equal(s.dailyTokens, 600);

  await recordUsage(kv, { tokens: 400, now: NOW }); // total 1000 → at cap
  s = await checkBudget(kv, { config, now: NOW });
  assert.equal(s.allowed, false);
  assert.match(s.reason!, /daily token cap/);
});

test('scan token cap: keyed by scan id, independent across scans', async () => {
  const kv = new FakeKV();
  const config = { scanTokenCap: 500 };

  await recordUsage(kv, { tokens: 500, scanId: 'scanA', now: NOW });
  const blocked = await checkBudget(kv, { config, scanId: 'scanA', now: NOW });
  assert.equal(blocked.allowed, false);
  assert.match(blocked.reason!, /scanA/);

  const other = await checkBudget(kv, { config, scanId: 'scanB', now: NOW });
  assert.equal(other.allowed, true);
});

test('dollar cap still works when configured (API-key providers)', async () => {
  const kv = new FakeKV();
  const config = { dailyCapUsd: 5 };
  await recordUsage(kv, { tokens: 100, usd: 5, now: NOW });
  const s = await checkBudget(kv, { config, now: NOW });
  assert.equal(s.allowed, false);
  assert.match(s.reason!, /daily budget cap/);
  assert.equal(s.dailyUsd, 5);
});

test('zero-dollar usage (OAuth) still accrues tokens and can trip the cap', async () => {
  const kv = new FakeKV();
  // total_cost_usd is 0 under subscription auth — the token axis must catch it.
  await recordUsage(kv, { tokens: 1000, usd: 0, now: NOW });
  const s = await checkBudget(kv, { config: { dailyTokenCap: 1000 }, now: NOW });
  assert.equal(s.allowed, false);
  assert.equal(s.dailyTokens, 1000);
  assert.equal(s.dailyUsd, 0);
});

test('no caps configured → always allowed', async () => {
  const kv = new FakeKV();
  await recordUsage(kv, { tokens: 1_000_000, usd: 1000, scanId: 's', now: NOW });
  const s = await checkBudget(kv, { config: {}, scanId: 's', now: NOW });
  assert.equal(s.allowed, true);
});

test('recordUsage ignores non-positive usage', async () => {
  const kv = new FakeKV();
  await recordUsage(kv, { tokens: 0, now: NOW });
  await recordUsage(kv, { tokens: -5, usd: -1, now: NOW });
  const s = await checkBudget(kv, { config: { dailyTokenCap: 1 }, now: NOW });
  assert.equal(s.dailyTokens, 0);
  assert.equal(s.allowed, true);
});

// ── Restart circuit-breaker ──────────────────────────────────────────

test('restart breaker trips after maxRestarts within window', async () => {
  const kv = new FakeKV();
  const config = { maxRestarts: 2, windowSec: 600 };

  assert.equal((await recordRestart(kv, 'honore', config)).tripped, false); // 1
  assert.equal((await recordRestart(kv, 'honore', config)).tripped, false); // 2
  const r = await recordRestart(kv, 'honore', config); // 3 > 2
  assert.equal(r.tripped, true);
  assert.equal(r.count, 3);

  const state = await isTripped(kv, 'honore');
  assert.ok(state);
  assert.equal(state!.count, 3);
});

test('resetBreaker clears state and restart counter', async () => {
  const kv = new FakeKV();
  const config = { maxRestarts: 1, windowSec: 600 };

  await recordRestart(kv, 'honore', config); // 1
  await recordRestart(kv, 'honore', config); // 2 → trips
  assert.ok(await isTripped(kv, 'honore'));

  await resetBreaker(kv, 'honore');
  assert.equal(await isTripped(kv, 'honore'), null);

  const r = await recordRestart(kv, 'honore', config); // back to #1
  assert.equal(r.count, 1);
  assert.equal(r.tripped, false);
});

test('restart counter window is fixed at first increment', async () => {
  const kv = new FakeKV();
  const config = { maxRestarts: 5, windowSec: 600 };
  await recordRestart(kv, 'honore', config);
  await recordRestart(kv, 'honore', config);
  assert.equal(kv.ttls.get('n184:restart:honore'), 600);
});

// ── Config parsing ───────────────────────────────────────────────────

test('loadBudgetConfig parses env (token caps primary) and ignores invalid', () => {
  assert.deepEqual(loadBudgetConfig({ N184_DAILY_TOKEN_CAP: '20000000' }), {
    dailyTokenCap: 20000000,
    scanTokenCap: undefined,
    dailyCapUsd: undefined,
    scanCapUsd: undefined,
  });
  assert.deepEqual(loadBudgetConfig({}), {
    dailyTokenCap: undefined,
    scanTokenCap: undefined,
    dailyCapUsd: undefined,
    scanCapUsd: undefined,
  });
  assert.deepEqual(loadBudgetConfig({ N184_DAILY_TOKEN_CAP: 'nope' }), {
    dailyTokenCap: undefined,
    scanTokenCap: undefined,
    dailyCapUsd: undefined,
    scanCapUsd: undefined,
  });
});

test('loadBreakerConfig applies defaults', () => {
  assert.deepEqual(loadBreakerConfig({}), { maxRestarts: 5, windowSec: 600 });
  assert.deepEqual(loadBreakerConfig({ N184_MAX_RESTARTS: '3', N184_RESTART_WINDOW_SEC: '120' }), {
    maxRestarts: 3,
    windowSec: 120,
  });
});

test('resetToken default and override', () => {
  assert.equal(resetToken({}), '/resume');
  assert.equal(resetToken({ N184_BREAKER_RESET_TOKEN: '/go' }), '/go');
});

test('resolveScanId precedence: env > scan_id > scanId', () => {
  assert.equal(resolveScanId({ scan_id: 'a' }, {}), 'a');
  assert.equal(resolveScanId({ scanId: 'b' }, {}), 'b');
  assert.equal(resolveScanId({ scan_id: 'a' }, { N184_SCAN_ID: 'env' }), 'env');
  assert.equal(resolveScanId(undefined, {}), undefined);
});
