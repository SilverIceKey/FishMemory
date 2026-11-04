import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  CONFIG_DEFAULTS,
  apply,
  createArchiver,
  extractText,
  resolveConfig,
} from '../index.js';

const TOKEN = 'test-token-123';
const TOPIC = '会话';
const EVENT_TIME = Date.UTC(2026, 9, 30, 12, 0, 0);

test('archive origin rejects remote hosts and ambiguous URLs', () => {
  for (const serviceUrl of ['https://example.com', 'http://localhost.evil.test',
    'http://user:pass@localhost', 'http://localhost/path', 'http://localhost/?x=1',
    'http://localhost/#fragment']) {
    assert.throws(() => resolveConfig({ tokenFile: 'test', serviceUrl }), /serviceUrl/);
  }
  for (const serviceUrl of ['http://127.0.0.1:8765', 'http://localhost:8765', 'http://[::1]:8765']) {
    assert.equal(resolveConfig({ tokenFile: 'test', serviceUrl }).serviceUrl, serviceUrl);
  }
});

test('Unicode truncation preserves supplementary characters and redirects are disabled', async (t) => {
  const h = createHarness(t, { maxTextChars: 2 });
  h.archiver.handleEvent({ id: 'emoji' }, userEvent(1, '中😀文'));
  await h.archiver.flushNow();
  assert.equal(h.fetchState.eventBodies[0].events[0].text, '中😀');
  assert.equal(h.fetchState.calls[0].init.redirect, 'error');
  await h.archiver.start();
  assert.equal(h.fetchState.calls.at(-1).init.redirect, 'error');
});

function tokenFile(t, content = `${TOKEN}\n`) {
  const dir = mkdtempSync(join(tmpdir(), 'fishmemory-dsh-'));
  const file = join(dir, 'service.token');
  writeFileSync(file, content, 'utf8');
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return file;
}

function createLogger() {
  const warnings = [];
  const infos = [];
  return {
    warnings,
    infos,
    warn: (message) => warnings.push(String(message)),
    info: (message) => infos.push(String(message)),
  };
}

/** 手动计时器：不真正等待，由测试显式推进（覆盖防抖与指数退避）。 */
function createManualTimers() {
  let nextId = 1;
  const pending = new Map();
  return {
    setTimeout(fn, delay) {
      const id = nextId++;
      pending.set(id, { fn, delay });
      return id;
    },
    clearTimeout(id) {
      pending.delete(id);
    },
    pendingCount() {
      return pending.size;
    },
    pendingDelays() {
      return [...pending.values()].map((entry) => entry.delay);
    },
    advanceAll() {
      const entries = [...pending.entries()];
      pending.clear();
      for (const [, entry] of entries) entry.fn();
      return entries.length;
    },
  };
}

/** 让已 resolve 的 promise 链跑完（不依赖真实计时）。 */
async function settle(rounds = 5) {
  for (let i = 0; i < rounds; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/** 反复「跑完微任务 + 推进到期计时器」，直到没有待处理计时器。 */
async function drain(timers, rounds = 30) {
  let advanced = 0;
  for (let i = 0; i < rounds; i += 1) {
    await settle();
    if (timers.pendingCount() === 0) return advanced;
    advanced += timers.advanceAll();
  }
  throw new Error('timers did not settle');
}

function okJson(body, status = 200) {
  return {
    ok: true,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function failedResponse(status) {
  return { ok: false, status, json: async () => ({ status: 'error' }), text: async () => '' };
}

/**
 * fake fetch：只接受 /healthz 与 /v1/events；记录请求并解析请求体。
 * 默认行为：healthz 200、/v1/events 全量 stored。
 */
function createFetch({ onHealth, onEvents } = {}) {
  const calls = [];
  const eventBodies = [];
  const fetchImpl = async (url, init = {}) => {
    const target = String(url);
    calls.push({ url: target, init });
    if (target.endsWith('/healthz')) {
      if (onHealth) return onHealth({ url: target, init });
      return okJson({ status: 'ok', version: 1 });
    }
    if (target.endsWith('/v1/events')) {
      const body = JSON.parse(init.body);
      eventBodies.push(body);
      if (onEvents) return onEvents(body, eventBodies.length);
      return okJson({ results: body.events.map((event) => ({ id: event.id, status: 'stored' })) });
    }
    throw new Error(`unexpected fetch: ${target}`);
  };
  return { fetch: fetchImpl, calls, eventBodies };
}

function createHarness(t, configOverrides = {}, fetchOptions = {}, tokenContent = `${TOKEN}\n`) {
  const logger = createLogger();
  const timers = createManualTimers();
  const fetchState = createFetch(fetchOptions);
  const file = tokenFile(t, tokenContent);
  const config = {
    serviceUrl: 'http://127.0.0.1:8765',
    tokenFile: file,
    topic: TOPIC,
    archiveUser: true,
    archiveAssistant: true,
    batchSize: 16,
    flushDelayMs: 50,
    ...configOverrides,
  };
  const archiver = createArchiver(config, {
    logger,
    fetch: fetchState.fetch,
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
  });
  return { archiver, logger, timers, fetchState, file, config };
}

function userEvent(seq, text, { sourceKind = 'user', content, time = EVENT_TIME } = {}) {
  return {
    type: 'user/message',
    seq,
    time,
    data: {
      id: `user-${seq}`,
      role: 'user',
      content: content ?? [{ type: 'text', text }],
      source: { kind: sourceKind },
    },
  };
}

function assistantEvent(seq, text, { sourceKind = 'model', time = EVENT_TIME } = {}) {
  return {
    type: 'assistant/message',
    seq,
    time,
    data: {
      message: {
        id: `assistant-${seq}`,
        role: 'assistant',
        content: [{ type: 'text', text }],
        source: sourceKind === 'model' ? { kind: 'model', provider: 'fake', model: 'fake-model' } : { kind: sourceKind },
      },
    },
  };
}

function toolEvent(seq, text, { sourceKind = 'tool', time = EVENT_TIME } = {}) {
  return {
    type: 'tool/result',
    seq,
    time,
    data: {
      message: {
        id: `tool-${seq}`,
        role: 'tool',
        toolCallId: 'call-1',
        content: [{ type: 'text', text }],
        source: sourceKind === 'tool' ? { kind: 'tool', callId: 'call-1' } : { kind: sourceKind },
      },
    },
  };
}

function createFakeCtx(logger) {
  const handlers = new Map();
  const effects = [];
  const ctx = {
    logger,
    on(name, listener) {
      handlers.set(name, listener);
      return () => handlers.delete(name);
    },
    effect(fn, label) {
      const cleanup = fn();
      effects.push({ cleanup, label });
      return cleanup;
    },
  };
  return { ctx, handlers, effects };
}

// ---------------------------------------------------------------------------
// 配置校验
// ---------------------------------------------------------------------------

test('resolveConfig: 默认值、serviceUrl 归一化、未知字段与非法字段带字段名报错', () => {
  const token = 'placeholder.token';
  const resolved = resolveConfig({ tokenFile: token, serviceUrl: 'http://127.0.0.1:8765/' });
  assert.equal(resolved.serviceUrl, 'http://127.0.0.1:8765');
  assert.equal(resolved.batchSize, CONFIG_DEFAULTS.batchSize);
  assert.equal(resolved.topic, CONFIG_DEFAULTS.topic);
  assert.equal(resolved.heartbeatFile, '');

  assert.throws(() => resolveConfig({ tokenFile: token, batchSize: '16' }), /batchSize/);
  assert.throws(() => resolveConfig({ tokenFile: token, archiveToolResults: 'yes' }), /archiveToolResults/);
  assert.throws(() => resolveConfig({ tokenFile: token, unknownField: 1 }), /unknownField/);
  assert.throws(() => resolveConfig({ tokenFile: '' }), /tokenFile/);
  assert.throws(() => resolveConfig({ tokenFile: token, serviceUrl: 'ftp://127.0.0.1:8765' }), /serviceUrl/);
  assert.throws(() => resolveConfig({ tokenFile: token, maxQueue: 0 }), /maxQueue/);
  assert.throws(() => resolveConfig({ tokenFile: token, requestTimeoutMs: 1.5 }), /requestTimeoutMs/);
});

test('createArchiver/apply: 非法配置抛错并带字段名', () => {
  assert.throws(
    () => createArchiver({ tokenFile: 'x.token', batchSize: 0 }, { fetch: async () => okJson({}) }),
    /batchSize/,
  );
  assert.throws(
    () => createArchiver({ tokenFile: 'x.token', topic: '' }, { fetch: async () => okJson({}) }),
    /topic/,
  );

  const { ctx } = createFakeCtx(createLogger());
  assert.throws(() => apply(ctx, { tokenFile: 'x.token', flushDelayMs: -1 }), /flushDelayMs/);
});

// ---------------------------------------------------------------------------
// 文本抽取与事件映射
// ---------------------------------------------------------------------------

test('extractText: 只按序拼接 text 块', () => {
  assert.equal(
    extractText([
      { type: 'text', text: 'a' },
      { type: 'image', data: 'ignored' },
      { type: 'reasoning', text: 'ignored too' },
      { type: 'text', text: 'b' },
    ]),
    'ab',
  );
  assert.equal(extractText(undefined), '');
  assert.equal(extractText('not-an-array'), '');
});

test('映射：user/message 生成 docs/16 §6 第 4 条的事件形状', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000 });
  const accepted = h.archiver.handleEvent(
    { id: 'sess-A' },
    userEvent(3, 'hello', {
      content: [{ type: 'text', text: 'hello ' }, { type: 'image', data: 'x' }, { type: 'text', text: 'world' }],
    }),
  );
  assert.equal(accepted, true);

  await h.archiver.flushNow();
  assert.equal(h.fetchState.eventBodies.length, 1);
  assert.deepEqual(h.fetchState.eventBodies[0].events, [
    {
      id: 'dsh:sess-A:3',
      session: 'sess-A',
      topic: TOPIC,
      text: 'hello world',
      time: new Date(EVENT_TIME).toISOString(),
      state: 'current',
    },
  ]);
  assert.deepEqual(h.archiver.stats(), {
    queued: 0,
    retrying: 0,
    inFlight: false,
    sent: 1,
    droppedOverflow: 0,
    droppedFailed: 0,
  });
});

test('映射：assistant/message 取 data.message 的文本；maxTextChars 截断', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000, maxTextChars: 5 });
  assert.equal(h.archiver.handleEvent({ id: 'sess-B' }, assistantEvent(7, 'abcdefg')), true);
  await h.archiver.flushNow();
  assert.deepEqual(h.fetchState.eventBodies[0].events.map((event) => event.text), ['abcde']);
});

test('过滤：空文本、无文本块、非白名单事件类型不归档', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000 });
  const session = { id: 'sess-C' };

  assert.equal(h.archiver.handleEvent(session, userEvent(0, '', { content: [] })), false);
  assert.equal(h.archiver.handleEvent(session, userEvent(1, '', { content: [{ type: 'image', data: 'x' }] })), false);
  assert.equal(h.archiver.handleEvent(session, userEvent(2, '   ')), false);
  assert.equal(h.archiver.handleEvent(session, { type: 'system/message', seq: 3, time: EVENT_TIME, data: {} }), false);
  assert.equal(h.archiver.handleEvent(session, { type: 'developer/message', seq: 4, time: EVENT_TIME, data: {} }), false);
  assert.equal(h.archiver.handleEvent(session, { type: 'turn/end', seq: 5, time: EVENT_TIME, data: {} }), false);
  assert.equal(h.archiver.handleEvent(session, { ...userEvent(6, 'x'), time: 'not-a-number' }), false);
  assert.equal(h.archiver.handleEvent({}, userEvent(7, 'x')), false);

  await h.archiver.flushNow();
  assert.equal(h.fetchState.eventBodies.length, 0);
  assert.equal(h.archiver.stats().queued, 0);
});

test('过滤：插件注入/系统消息排除（source.kind 不是 user/model/tool）', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000, archiveToolResults: true });
  const session = { id: 'sess-D' };

  // 工具产生的附加 user-message 是 ptc-mode（dsh-tools README:134），不是用户输入。
  assert.equal(h.archiver.handleEvent(session, userEvent(1, 'injected', { sourceKind: 'ptc-mode' })), false);
  assert.equal(h.archiver.handleEvent(session, assistantEvent(2, 'x', { sourceKind: 'user' })), false);
  assert.equal(h.archiver.handleEvent(session, toolEvent(3, 'x', { sourceKind: 'model' })), false);
  // 白名单内的事件仍然归档，证明过滤不是一刀切。
  assert.equal(h.archiver.handleEvent(session, userEvent(4, 'real user')), true);
  assert.equal(h.archiver.handleEvent(session, assistantEvent(5, 'real model')), true);
  assert.equal(h.archiver.handleEvent(session, toolEvent(6, 'real tool')), true);

  await h.archiver.flushNow();
  assert.deepEqual(h.fetchState.eventBodies[0].events.map((event) => event.id), [
    'dsh:sess-D:4',
    'dsh:sess-D:5',
    'dsh:sess-D:6',
  ]);
});

test('tool/result 默认关闭，archiveToolResults=true 才归档', async (t) => {
  const off = createHarness(t, { batchSize: 16, flushDelayMs: 10_000 });
  assert.equal(off.archiver.handleEvent({ id: 'sess-E' }, toolEvent(1, 'tool output')), false);
  await off.archiver.flushNow();
  assert.equal(off.fetchState.eventBodies.length, 0);

  const on = createHarness(t, { batchSize: 16, flushDelayMs: 10_000, archiveToolResults: true });
  assert.equal(on.archiver.handleEvent({ id: 'sess-E' }, toolEvent(2, 'tool output')), true);
  await on.archiver.flushNow();
  assert.equal(on.fetchState.eventBodies[0].events[0].text, 'tool output');
});

// ---------------------------------------------------------------------------
// 批量、逐条结果、重试、队列上限、卸载
// ---------------------------------------------------------------------------

test('批量：达到 batchSize 立即发送，一次请求携带整批', async (t) => {
  const h = createHarness(t, { batchSize: 3, flushDelayMs: 10_000 });
  const session = { id: 'sess-F' };
  h.archiver.handleEvent(session, userEvent(1, 'one'));
  h.archiver.handleEvent(session, userEvent(2, 'two'));
  assert.equal(h.fetchState.eventBodies.length, 0);
  h.archiver.handleEvent(session, userEvent(3, 'three'));

  await settle();
  assert.equal(h.fetchState.eventBodies.length, 1);
  assert.equal(h.fetchState.eventBodies[0].events.length, 3);
  assert.equal(h.timers.pendingCount(), 0);
});

test('批量：不足一批时按 flushDelayMs 防抖发送', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 250 });
  h.archiver.handleEvent({ id: 'sess-G' }, userEvent(1, 'one'));

  await settle();
  assert.equal(h.fetchState.eventBodies.length, 0);
  assert.deepEqual(h.timers.pendingDelays(), [250]);

  h.timers.advanceAll();
  await settle();
  assert.equal(h.fetchState.eventBodies.length, 1);
  assert.equal(h.fetchState.eventBodies[0].events.length, 1);
});

test('逐条结果：stored/duplicate/rejected 完成，error 只重发未处理事件', async (t) => {
  const h = createHarness(
    t,
    { batchSize: 2, flushDelayMs: 10_000 },
    {
      onEvents: (body, round) => {
        if (round === 1) {
          return okJson({
            results: [
              { id: body.events[0].id, status: 'stored' },
              { id: body.events[1].id, status: 'error' },
            ],
          });
        }
        return okJson({ results: [{ id: body.events[0].id, status: 'duplicate' }] });
      },
    },
  );
  const session = { id: 'sess-H' };
  h.archiver.handleEvent(session, userEvent(1, 'kept'));
  h.archiver.handleEvent(session, userEvent(2, 'retried'));

  await settle();
  assert.equal(h.fetchState.eventBodies.length, 1);
  assert.deepEqual(h.timers.pendingDelays(), [500]);
  assert.equal(h.archiver.stats().sent, 1);

  h.timers.advanceAll();
  await settle();
  assert.equal(h.fetchState.eventBodies.length, 2);
  assert.deepEqual(h.fetchState.eventBodies[1].events.map((event) => event.id), ['dsh:sess-H:2']);
  assert.equal(h.archiver.stats().sent, 2);
  assert.equal(h.archiver.stats().retrying, 0);
});

test('重试：401 后指数退避，下一次成功', async (t) => {
  const h = createHarness(
    t,
    { batchSize: 16, flushDelayMs: 10_000, maxRetries: 2 },
    {
      onEvents: (body, round) =>
        round === 1 ? failedResponse(401) : okJson({ results: body.events.map((event) => ({ id: event.id, status: 'stored' })) }),
    },
  );
  h.archiver.handleEvent({ id: 'sess-I' }, userEvent(1, 'retry me'));

  await h.archiver.flushNow();
  assert.equal(h.fetchState.eventBodies.length, 1);
  assert.deepEqual(h.timers.pendingDelays(), [500]);

  h.timers.advanceAll();
  await settle();
  assert.equal(h.fetchState.eventBodies.length, 2);
  assert.equal(h.archiver.stats().sent, 1);
  assert.equal(h.archiver.stats().droppedFailed, 0);
});

test('重试：5xx 后指数退避，下一次成功', async (t) => {
  const h = createHarness(
    t,
    { batchSize: 16, flushDelayMs: 10_000, maxRetries: 3 },
    {
      onEvents: (body, round) =>
        round <= 2 ? failedResponse(500) : okJson({ results: body.events.map((event) => ({ id: event.id, status: 'stored' })) }),
    },
  );
  h.archiver.handleEvent({ id: 'sess-J' }, userEvent(1, 'retry me twice'));

  await h.archiver.flushNow();
  assert.deepEqual(h.timers.pendingDelays(), [500]);
  h.timers.advanceAll();
  await settle();
  assert.deepEqual(h.timers.pendingDelays(), [1000]);
  h.timers.advanceAll();
  await settle();

  assert.equal(h.fetchState.eventBodies.length, 3);
  assert.equal(h.archiver.stats().sent, 1);
});

test('重试耗尽：丢弃该批并 warning，不伪造成功', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000, maxRetries: 2 }, { onEvents: () => failedResponse(500) });
  h.archiver.handleEvent({ id: 'sess-K' }, userEvent(1, 'never stored'));

  await h.archiver.flushNow();
  assert.deepEqual(h.timers.pendingDelays(), [500]);
  h.timers.advanceAll();
  await settle();
  assert.deepEqual(h.timers.pendingDelays(), [1000]);
  h.timers.advanceAll();
  await settle();

  assert.equal(h.fetchState.eventBodies.length, 3); // 首次 + 2 次重试
  const stats = h.archiver.stats();
  assert.equal(stats.sent, 0);
  assert.equal(stats.droppedFailed, 1);
  assert.ok(h.logger.warnings.some((message) => message.includes('dropped')));
  assert.equal(h.timers.pendingCount(), 0);
});

test('队列上限：超限丢最旧并 warning + 计数', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000, maxQueue: 2 });
  const session = { id: 'sess-L' };
  h.archiver.handleEvent(session, userEvent(1, 'oldest'));
  h.archiver.handleEvent(session, userEvent(2, 'middle'));
  h.archiver.handleEvent(session, userEvent(3, 'newest'));

  const stats = h.archiver.stats();
  assert.equal(stats.queued, 2);
  assert.equal(stats.droppedOverflow, 1);
  assert.ok(h.logger.warnings.some((message) => message.includes('maxQueue')));

  await h.archiver.flushNow();
  assert.deepEqual(
    h.fetchState.eventBodies[0].events.map((event) => event.id),
    ['dsh:sess-L:2', 'dsh:sess-L:3'],
  );
});

test('卸载：停止计时器、尽力发送最后一批、退订后不再接收', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000 });
  const session = { id: 'sess-M' };
  h.archiver.handleEvent(session, userEvent(1, 'last one'));
  h.archiver.handleEvent(session, userEvent(2, 'last two'));

  await h.archiver.dispose();
  assert.equal(h.fetchState.eventBodies.length, 1);
  assert.deepEqual(
    h.fetchState.eventBodies[0].events.map((event) => event.text),
    ['last one', 'last two'],
  );
  assert.equal(h.timers.pendingCount(), 0);
  assert.equal(h.archiver.handleEvent(session, userEvent(3, 'after dispose')), false);
  assert.equal(h.archiver.stats().queued, 0);

  // 幂等：再次 dispose 不抛错。
  await h.archiver.dispose();
  assert.equal(h.fetchState.eventBodies.length, 1);
});

test('卸载：最终 flush 失败只 warning，不抛未处理异常', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000 }, { onEvents: () => failedResponse(503) });
  h.archiver.handleEvent({ id: 'sess-N' }, userEvent(1, 'cannot send'));
  await h.archiver.dispose();
  assert.equal(h.archiver.stats().droppedFailed, 1);
  assert.ok(h.logger.warnings.some((message) => message.includes('unload')));
  assert.equal(h.timers.pendingCount(), 0);
});

// ---------------------------------------------------------------------------
// token / HTTP 细节
// ---------------------------------------------------------------------------

test('tokenFile：去 BOM 与空白；请求头与 Content-Type / AbortSignal 符合契约', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000 }, {}, `\uFEFF  ${TOKEN}  \r\n`);
  h.archiver.handleEvent({ id: 'sess-O' }, userEvent(1, 'auth'));

  await h.archiver.flushNow();
  assert.equal(h.fetchState.calls.length, 1);
  const { init } = h.fetchState.calls[0];
  assert.equal(init.method, 'POST');
  assert.equal(init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(init.headers['Content-Type'], 'application/json');
  assert.ok(init.signal instanceof AbortSignal);
  assert.equal(h.fetchState.calls[0].url, 'http://127.0.0.1:8765/v1/events');
});

test('tokenFile 缺失：发送失败按网络错误重试，最终丢弃并 warning', async (t) => {
  const h = createHarness(t, { batchSize: 16, flushDelayMs: 10_000, maxRetries: 1 });
  h.archiver.handleEvent({ id: 'sess-P' }, userEvent(1, 'no token'));
  // 指向不存在的文件
  h.archiver.config.tokenFile = join(tmpdir(), 'definitely-missing-fishmemory-token');

  await h.archiver.flushNow();
  assert.deepEqual(h.timers.pendingDelays(), [500]);
  h.timers.advanceAll();
  await settle();

  assert.equal(h.fetchState.eventBodies.length, 0);
  assert.equal(h.archiver.stats().droppedFailed, 1);
  assert.ok(h.logger.warnings.some((message) => message.includes('tokenFile')));
});

// ---------------------------------------------------------------------------
// 健康探测与 apply 装配
// ---------------------------------------------------------------------------

test('健康探测：成功后 info；失败只 warning 且返回 false，不抛错', async (t) => {
  const ok = createHarness(t);
  assert.equal(await ok.archiver.start(), true);
  assert.ok(ok.logger.infos.some((message) => message.includes('reachable')));

  const bad = createHarness(t, {}, { onHealth: () => failedResponse(503) });
  assert.equal(await bad.archiver.start(), false);
  assert.ok(bad.logger.warnings.some((message) => message.includes('health probe')));

  const down = createHarness(t, {}, { onHealth: () => { throw new Error('ECONNREFUSED'); } });
  assert.equal(await down.archiver.start(), false);
  assert.ok(down.logger.warnings.some((message) => message.includes('ECONNREFUSED')));
});

test('apply：只订阅 session/event，healthz 失败不阻断激活，卸载清理订阅', async (t) => {
  const logger = createLogger();
  const { ctx, handlers, effects } = createFakeCtx(logger);
  const file = tokenFile(t);
  const fetchState = createFetch({ onHealth: () => { throw new Error('service down'); } });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchState.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const config = {
    serviceUrl: 'http://127.0.0.1:8765',
    tokenFile: file,
    topic: TOPIC,
    archiveUser: true,
    archiveAssistant: true,
    batchSize: 1,
    flushDelayMs: 1000,
  };
  assert.doesNotThrow(() => apply(ctx, config));
  assert.equal(handlers.size, 1);
  assert.ok(handlers.has('session/event'));
  assert.equal(effects.length, 1);
  assert.equal(effects[0].label, 'fishmemory-archive');

  handlers.get('session/event')({ id: 'sess-Q' }, userEvent(1, 'through apply'));
  await settle();
  assert.ok(logger.warnings.some((message) => message.includes('health probe')));
  assert.equal(fetchState.eventBodies.length, 1);
  assert.equal(fetchState.eventBodies[0].events[0].id, 'dsh:sess-Q:1');

  await effects[0].cleanup();
  assert.equal(handlers.size, 0);
  assert.ok(fetchState.calls.every((call) => call.url.startsWith('http://127.0.0.1:8765/')));
});

test('apply：健康探测成功时 info 日志包含 serviceUrl', async (t) => {
  const logger = createLogger();
  const { ctx, effects } = createFakeCtx(logger);
  const file = tokenFile(t);
  const fetchState = createFetch();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchState.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  apply(ctx, { serviceUrl: 'http://127.0.0.1:8765', tokenFile: file });
  await settle();
  assert.ok(logger.infos.some((message) => message.includes('http://127.0.0.1:8765')));
  await effects[0].cleanup();
});
