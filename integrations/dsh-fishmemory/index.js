/**
 * FishMemory DSH 原生归档插件（Host plugin）。
 *
 * 职责（docs/16-DSH原生插件集成.md §6）：订阅**已提交**的 `session/event`，把用户消息、
 * 主模型消息（可选工具结果）通过 HTTP 归档到本机 FishMemory 服务。不注册工具、不注入
 * 上下文、不 append Session 事件、不写 DSH 数据。读路径由 MCP `memory_recall` 负责。
 *
 * 已核实的 DSH 0.2.1-rc.3 API 证据（仓库副本 reports/dsh-source/extracted/）：
 * - Host 插件形式 `export function apply(ctx, config)`，资源用 `ctx.effect`/`ctx.on` 注册，
 *   清理由 effect disposer 承担：
 *   @deepseek-ai/dsh-agent-preset/skills/cordis-plugin-development/references/host-plugin.md:57-64
 * - `ctx.on(name, listener)` 返回 disposer，listener 自动绑定到当前 fiber：
 *   @deepseek-ai/cordis/lib/index.js:336-352（注册为 effect）、:371-378（on）；:1169（effect）；
 *   :1142-1146（disposer/生成器清理）
 * - `ctx.effect(() => cleanup)` 的 cleanup 可以是函数或 Promise：
 *   @deepseek-ai/cordis/lib/index.js:1142-1146、:1169-1260
 * - 事件在提交后通知，参数为 `(session, event)`，事件含 `type/seq/time/data`：
 *   @deepseek-ai/dsh-session-persistence-jsonl/lib/index.js:409-423（:421 订阅处）；
 *   @deepseek-ai/dsh-session/lib/index.js:1579-1598（commit 与 session/event 派发）、:1522-1534（信封）
 * - 事件类型与消息形状（`user/message` 的 `data` 即消息；`assistant/message`、`tool/result`
 *   的消息在 `data.message`；assistant source.kind 只能是 `model`，tool 只能是 `tool`）：
 *   @deepseek-ai/dsh-session/lib/index.js:1194-1219、:217-230；
 *   用户消息 source.kind='user'：@deepseek-ai/dsh-session/README.md:45、@deepseek-ai/dsh-agent/README.md:52
 * - 插件注入上下文/工具产生的 user-message 以别的 source.kind 出现（如 ptc-mode），必须排除：
 *   @deepseek-ai/dsh-tools/README.md:134
 *
 * 约束：零 npm 运行时依赖，只用 `node:` 内置模块与全局 `fetch`（Node 24）。不 import
 * @deepseek-ai/schemastery 或任何 Harness 包：linked 包从工作区解析这些依赖会失败；
 * 配置校验在本文件手写，非法配置抛出带字段名的 Error。因此本包**不导出** `Config`：
 * cordis 对声明了 Config 的插件会调用 `runtime.Config["~standard"].validate(config)`，
 * 导出手写普通对象会在激活时抛 TypeError（@deepseek-ai/cordis/lib/index.js:956-961）。
 */

import { readFileSync } from 'node:fs';

/** 包名（插件行 name 必须与 cordis.patch.yml 一致）。 */
export const PACKAGE_NAME = '@fishmemory/dsh-fishmemory';

/** 本地服务端点（docs/16 §5）。 */
export const HEALTH_PATH = '/healthz';
export const EVENTS_PATH = '/v1/events';

/** /v1/events 逐条结果中视为「已完成」的状态，其余状态都按失败重试。 */
export const DONE_STATUSES = Object.freeze(['stored', 'duplicate', 'rejected']);

/** 插件配置默认值，与 docs/16-DSH原生插件集成.md §6 的 bundle 默认 patch 一致。 */
export const CONFIG_DEFAULTS = Object.freeze({
  serviceUrl: 'http://127.0.0.1:8765',
  tokenFile: '',
  // 保留字段：本轮 scope 只由本地服务配置决定，插件不向请求体发送身份（单用户本机模式）。
  userId: 'local',
  agentId: 'dsh',
  topic: '会话',
  archiveUser: false,
  archiveAssistant: false,
  archiveToolResults: false,
  maxTextChars: 4000,
  batchSize: 16,
  flushDelayMs: 800,
  requestTimeoutMs: 5000,
  maxRetries: 5,
  maxQueue: 512,
  // 保留字段：docs/16 §6 列出但未定义写入语义；本实现不写任何心跳文件（只读 token）。
  heartbeatFile: '',
});

/** 退避重试基数与上限（指数：500ms, 1s, 2s, 4s, 8s…）。 */
const RETRY_BASE_MS = 500;
const RETRY_MAX_MS = 8_000;
/** 队列溢出 warning 的最小间隔，避免每条事件刷屏；丢弃计数始终累加。 */
const OVERFLOW_WARN_INTERVAL_MS = 5_000;

/** 数值字段的取值区间（闭区间，整数字段）。 */
const INTEGER_FIELDS = Object.freeze({
  maxTextChars: [1, 1_000_000],
  batchSize: [1, 256],
  flushDelayMs: [0, 600_000],
  requestTimeoutMs: [100, 120_000],
  maxRetries: [0, 20],
  maxQueue: [1, 100_000],
});

/** 字符串字段（非空，去空白后判断）。 */
const REQUIRED_STRINGS = Object.freeze(['userId', 'agentId', 'topic', 'tokenFile']);

/** 构造带字段名的配置错误。 */
function configError(field, requirement) {
  const error = new Error(`[fishmemory-archive] invalid config: field "${field}" ${requirement}`);
  error.field = field;
  return error;
}

/**
 * 手写配置校验/归一化（不依赖 schemastery）。
 * @param {unknown} raw cordis.patch.yml 中的 config
 * @returns {typeof CONFIG_DEFAULTS} 归一化后的配置
 * @throws {Error} 非法配置，错误信息含字段名，`error.field` 指向该字段
 */
export function resolveConfig(raw) {
  if (raw === undefined || raw === null) raw = {};
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('[fishmemory-archive] invalid config: config must be an object');
  }
  for (const key of Object.keys(raw)) {
    if (!Object.prototype.hasOwnProperty.call(CONFIG_DEFAULTS, key)) {
      throw configError(key, 'is not a known field');
    }
  }

  const config = { ...CONFIG_DEFAULTS, ...raw };

  for (const field of REQUIRED_STRINGS) {
    if (typeof config[field] !== 'string' || config[field].trim() === '') {
      throw configError(field, 'must be a non-empty string');
    }
  }
  if (typeof config.heartbeatFile !== 'string') {
    throw configError('heartbeatFile', 'must be a string');
  }

  let parsedUrl;
  try {
    parsedUrl = new URL(config.serviceUrl);
  } catch {
    throw configError('serviceUrl', 'must be an absolute http(s) URL');
  }
  if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
    throw configError('serviceUrl', 'must use the http: or https: scheme');
  }
  // Team decision: archive credentials and raw history must remain on loopback.
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(parsedUrl.hostname) ||
      parsedUrl.username || parsedUrl.password || parsedUrl.search || parsedUrl.hash ||
      parsedUrl.pathname !== '/') {
    throw configError('serviceUrl', 'must be a loopback origin without credentials, path, query or fragment');
  }
  config.serviceUrl = config.serviceUrl.replace(/\/+$/, '');

  for (const field of ['archiveUser', 'archiveAssistant', 'archiveToolResults']) {
    if (typeof config[field] !== 'boolean') throw configError(field, 'must be a boolean');
  }

  for (const [field, [min, max]] of Object.entries(INTEGER_FIELDS)) {
    const value = config[field];
    if (!Number.isInteger(value) || value < min || value > max) {
      throw configError(field, `must be an integer between ${min} and ${max}`);
    }
  }

  return config;
}

/** 从消息 content 数组中按顺序拼接 type==='text' 的文本块。 */
export function extractText(content) {
  if (!Array.isArray(content)) return '';
  const parts = [];
  for (const block of content) {
    if (block !== null && typeof block === 'object' && block.type === 'text' && typeof block.text === 'string') {
      parts.push(block.text);
    }
  }
  return parts.join('');
}

/** 事件类型 -> 消息所在位置与允许的 source.kind（docs/16 §6 第 2 条）。 */
const ARCHIVE_RULES = Object.freeze({
  'user/message': { location: 'data', sourceKind: 'user', toggle: 'archiveUser' },
  'assistant/message': { location: 'message', sourceKind: 'model', toggle: 'archiveAssistant' },
  'tool/result': { location: 'message', sourceKind: 'tool', toggle: 'archiveToolResults' },
});

/**
 * 把一条已提交的 session/event 映射为 FishMemory `/v1/events` 的单条事件。
 * 过滤：非白名单事件类型、source.kind 不匹配（插件注入/system/developer/ptc-mode 等）、
 * 开关关闭、空文本、缺少 session.id/seq/time 时返回 null（不归档）。
 * @returns {{id:string,session:string,topic:string,text:string,time:string,state:'current'}|null}
 */
export function buildArchiveEvent(config, session, event) {
  if (!event || typeof event !== 'object') return null;
  const rule = ARCHIVE_RULES[event.type];
  if (!rule) return null;
  if (!config[rule.toggle]) return null;

  const data = event.data;
  const message = rule.location === 'data' ? data : data && typeof data === 'object' ? data.message : undefined;
  if (!message || typeof message !== 'object') return null;
  if (!message.source || message.source.kind !== rule.sourceKind) return null;

  const sessionId = session && typeof session.id === 'string' ? session.id : '';
  if (sessionId === '') return null;
  if (!Number.isSafeInteger(event.seq) || event.seq < 0) return null;
  if (typeof event.time !== 'number' || !Number.isFinite(event.time)) return null;

  const text = Array.from(extractText(message.content)).slice(0, config.maxTextChars).join('');
  if (text.trim() === '') return null;

  let time;
  try {
    time = new Date(event.time).toISOString();
  } catch {
    return null;
  }

  return {
    id: `dsh:${sessionId}:${event.seq}`,
    session: sessionId,
    topic: config.topic,
    text,
    time,
    state: 'current',
  };
}

/**
 * 创建归档器（测试注入点：docs/16 §6 第 9 条）。
 * @param {object} rawConfig 插件配置（见 CONFIG_DEFAULTS）
 * @param {object} [deps] 可替换依赖
 * @param {Function} [deps.fetch] 默认 globalThis.fetch
 * @param {{warn:Function, info:Function}} [deps.logger] 默认 console
 * @param {Function} [deps.setTimeout] 默认 globalThis.setTimeout
 * @param {Function} [deps.clearTimeout] 默认 globalThis.clearTimeout
 * @param {Function} [deps.now] 默认 Date.now
 * @param {Function} [deps.readFile] 默认同步读取（file, 'utf8'），用于 tokenFile
 * @returns {object} { start, handleEvent, flushNow, dispose, stats, config }
 */
export function createArchiver(rawConfig, deps = {}) {
  const config = resolveConfig(rawConfig);

  const logger = deps.logger ?? console;
  const fetchImpl = deps.fetch ?? globalThis.fetch;
  const setTimeoutFn = deps.setTimeout ?? globalThis.setTimeout;
  const clearTimeoutFn = deps.clearTimeout ?? globalThis.clearTimeout;
  const now = deps.now ?? Date.now;
  const readFile = deps.readFile ?? ((file) => readFileSync(file, 'utf8'));

  const logWarn = (message) => {
    try {
      logger.warn?.(message);
    } catch {
      /* 日志失败不得影响归档主流程 */
    }
  };
  const logInfo = (message) => {
    try {
      logger.info?.(message);
    } catch {
      /* 同上 */
    }
  };
  /** 触发后台任务并兜底其异常，避免 unhandledRejection。 */
  const fireAndForget = (task, label) => {
    Promise.resolve(task).catch((error) => {
      logWarn(`[fishmemory-archive] background task "${label}" failed: ${error?.message ?? error}`);
    });
  };

  let disposed = false;
  let disposePromise = null;
  /** 待发送事件（FIFO，超出 maxQueue 丢最旧）。 */
  const queue = [];
  /** 等待退避重试的事件批次（同一时间最多一批）。 */
  let retryBatch = null;
  let retryAttempt = 0;
  let flushTimer = null;
  let retryTimer = null;
  /** 当前请求的 Promise（同一时间最多一个在飞请求）。 */
  let inFlight = null;

  let sentCount = 0;
  let droppedOverflow = 0;
  let droppedFailed = 0;
  let lastOverflowWarnAt = Number.NEGATIVE_INFINITY;

  /** 读取 tokenFile：去 BOM 与首尾空白（docs/16 §4 第 4 条：插件只读取）。 */
  function readToken() {
    const raw = readFile(config.tokenFile);
    const token = String(raw).replace(/^\uFEFF/, '').trim();
    if (token === '') throw new Error(`tokenFile is empty: ${config.tokenFile}`);
    return token;
  }

  function bodyFor(events) {
    return JSON.stringify({ events });
  }

  /**
   * 单次 POST /v1/events。
   * @returns {Promise<{ok:true,retry:Array}|{ok:false}>} ok 表示服务端返回了可解析的逐条结果
   */
  async function trySend(batch) {
    let token;
    try {
      token = readToken();
    } catch (error) {
      logWarn(`[fishmemory-archive] cannot read tokenFile "${config.tokenFile}" for ${batch.length} event(s): ${error?.message ?? error}`);
      return { ok: false };
    }

    let response;
    try {
      response = await fetchImpl(`${config.serviceUrl}${EVENTS_PATH}`, {
        method: 'POST',
        redirect: 'error',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: bodyFor(batch),
        signal: AbortSignal.timeout(config.requestTimeoutMs),
      });
    } catch (error) {
      logWarn(`[fishmemory-archive] POST ${config.serviceUrl}${EVENTS_PATH} failed for ${batch.length} event(s): ${error?.message ?? error}`);
      return { ok: false };
    }

    if (!response || response.ok !== true) {
      logWarn(`[fishmemory-archive] POST ${config.serviceUrl}${EVENTS_PATH} returned HTTP ${response?.status ?? 'unknown'} for ${batch.length} event(s)`);
      return { ok: false };
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      logWarn(`[fishmemory-archive] response from ${EVENTS_PATH} is not JSON: ${error?.message ?? error}`);
      return { ok: false };
    }

    const results = payload && typeof payload === 'object' ? payload.results : undefined;
    if (!Array.isArray(results)) {
      logWarn(`[fishmemory-archive] response from ${EVENTS_PATH} has no results array`);
      return { ok: false };
    }

    const statusById = new Map();
    for (const result of results) {
      if (result !== null && typeof result === 'object' && typeof result.id === 'string') {
        statusById.set(result.id, result.status);
      }
    }
    // 逐条结果：stored/duplicate/rejected 视为已处理，其余状态（error/缺失）进入重试。
    const retry = batch.filter((event) => !DONE_STATUSES.includes(statusById.get(event.id)));
    return { ok: true, retry };
  }

  function scheduleRetry(pending, attempt) {
    if (disposed) {
      dropBatch(pending, 'plugin unloaded');
      return;
    }
    retryBatch = pending;
    retryAttempt = attempt;
    if (retryTimer !== null) clearTimeoutFn(retryTimer);
    const delay = Math.min(RETRY_BASE_MS * 2 ** (attempt - 1), RETRY_MAX_MS);
    retryTimer = setTimeoutFn(() => {
      retryTimer = null;
      fireAndForget(retryNow(), 'retry');
    }, delay);
  }

  function dropBatch(pending, reason) {
    retryBatch = null;
    droppedFailed += pending.length;
    logWarn(`[fishmemory-archive] dropped ${pending.length} event(s) after ${reason}; dropped total=${droppedFailed}`);
  }

  /** 执行一批并决定：完成 / 退避重试 / 耗尽丢弃。 */
  async function runBatch(batch, attempt) {
    const outcome = await trySend(batch);
    let pending;
    if (outcome.ok) {
      sentCount += batch.length - outcome.retry.length;
      pending = outcome.retry;
    } else {
      pending = batch;
    }
    if (pending.length === 0) return;
    if (disposed) {
      dropBatch(pending, 'plugin unloaded');
      return;
    }
    const nextAttempt = attempt + 1;
    if (nextAttempt > config.maxRetries) {
      dropBatch(pending, `${config.maxRetries} retries`);
      return;
    }
    scheduleRetry(pending, nextAttempt);
  }

  function scheduleFlush(delay) {
    if (disposed || retryBatch || retryTimer !== null || inFlight) return;
    if (flushTimer !== null) return;
    flushTimer = setTimeoutFn(() => {
      flushTimer = null;
      fireAndForget(flush(), 'flush');
    }, delay);
  }

  /** 发送一轮：从 FIFO 队列取最多 batchSize 条。 */
  async function flush() {
    if (disposed) return;
    // 手动 flushNow / 批量路径已经决定立即发送，丢弃尚未到期的防抖计时器，避免重复调度。
    if (flushTimer !== null) {
      clearTimeoutFn(flushTimer);
      flushTimer = null;
    }
    if (inFlight || retryBatch) return;
    if (queue.length === 0) return;

    const batch = queue.splice(0, config.batchSize);
    const task = runBatch(batch, 0);
    inFlight = task;
    try {
      await task;
    } finally {
      if (inFlight === task) inFlight = null;
    }

    if (disposed || retryBatch) return;
    if (queue.length === 0) return;
    if (queue.length >= config.batchSize) fireAndForget(flush(), 'flush');
    else scheduleFlush(config.flushDelayMs);
  }

  /** 退避计时器到期后的重试。 */
  async function retryNow() {
    if (disposed || inFlight) return;
    if (flushTimer !== null) {
      clearTimeoutFn(flushTimer);
      flushTimer = null;
    }
    const pending = retryBatch;
    retryBatch = null;
    if (!pending || pending.length === 0) return;

    const task = runBatch(pending, retryAttempt);
    inFlight = task;
    try {
      await task;
    } finally {
      if (inFlight === task) inFlight = null;
    }

    if (disposed || retryBatch) return;
    if (queue.length === 0) return;
    if (queue.length >= config.batchSize) fireAndForget(flush(), 'flush');
    else scheduleFlush(config.flushDelayMs);
  }

  /** enqueue 一条映射后的事件，并维护队列上限与 flush 调度。 */
  function enqueue(event) {
    if (queue.length >= config.maxQueue) {
      queue.shift();
      droppedOverflow += 1;
      const timestamp = now();
      if (timestamp - lastOverflowWarnAt >= OVERFLOW_WARN_INTERVAL_MS) {
        lastOverflowWarnAt = timestamp;
        logWarn(`[fishmemory-archive] queue is full (maxQueue=${config.maxQueue}); dropping oldest event; dropped total=${droppedOverflow}`);
      }
    }
    queue.push(event);

    if (queue.length >= config.batchSize) {
      if (flushTimer !== null) {
        clearTimeoutFn(flushTimer);
        flushTimer = null;
      }
      fireAndForget(flush(), 'flush');
    } else {
      scheduleFlush(config.flushDelayMs);
    }
  }

  /** 激活时的健康探测：失败只 warning，不阻断 DSH 启动。 */
  async function start() {
    if (disposed) return false;
    try {
      if (typeof fetchImpl !== 'function') {
        logWarn('[fishmemory-archive] global fetch is unavailable; archival is disabled until the runtime provides it');
        return false;
      }
      const response = await fetchImpl(`${config.serviceUrl}${HEALTH_PATH}`, {
        method: 'GET',
        redirect: 'error',
        signal: AbortSignal.timeout(config.requestTimeoutMs),
      });
      if (!response || response.ok !== true) {
        logWarn(
          `[fishmemory-archive] health probe GET ${config.serviceUrl}${HEALTH_PATH} returned HTTP ${response?.status ?? 'unknown'}; ` +
            'DSH startup is not blocked and events stay queued for retry',
        );
        return false;
      }
      logInfo(
        `[fishmemory-archive] FishMemory service reachable at ${config.serviceUrl} ` +
          `(userId=${config.userId}, agentId=${config.agentId})`,
      );
      return true;
    } catch (error) {
      logWarn(
        `[fishmemory-archive] health probe GET ${config.serviceUrl}${HEALTH_PATH} failed: ${error?.message ?? error}; ` +
          'DSH startup is not blocked and events stay queued for retry',
      );
      return false;
    }
  }

  /** 处理一条已提交的 session/event；不是归档目标时返回 false。 */
  function handleEvent(session, event) {
    if (disposed) return false;
    try {
      const archiveEvent = buildArchiveEvent(config, session, event);
      if (archiveEvent === null) return false;
      enqueue(archiveEvent);
      return true;
    } catch (error) {
      logWarn(`[fishmemory-archive] failed to map session event: ${error?.message ?? error}`);
      return false;
    }
  }

  /** 用有界 Promise.race 等待卸载期间的工作（不超过一个 requestTimeoutMs）。 */
  function withRequestBound(promise) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeoutFn(timer);
        resolve();
      };
      const timer = setTimeoutFn(() => finish(), config.requestTimeoutMs);
      timer?.unref?.();
      Promise.resolve(promise).then(finish, finish);
    });
  }

  /** 卸载时的最后一次发送：单次尝试，不重试、不抛异常。 */
  async function sendFinal(batch) {
    try {
      const outcome = await trySend(batch);
      if (!outcome.ok) {
        droppedFailed += batch.length;
        logWarn(`[fishmemory-archive] final flush on unload failed; dropped ${batch.length} event(s)`);
        return;
      }
      sentCount += batch.length - outcome.retry.length;
      if (outcome.retry.length > 0) {
        droppedFailed += outcome.retry.length;
        logWarn(`[fishmemory-archive] final flush on unload left ${outcome.retry.length} unprocessed event(s); dropped`);
      }
    } catch (error) {
      droppedFailed += batch.length;
      logWarn(`[fishmemory-archive] final flush on unload threw: ${error?.message ?? error}`);
    }
  }

  /** 卸载：停表、退订（订阅由 apply 的 effect 负责）、尽力发送最后一批，有界且不抛异常。 */
  function dispose() {
    if (disposePromise !== null) return disposePromise;
    disposed = true;

    if (flushTimer !== null) {
      clearTimeoutFn(flushTimer);
      flushTimer = null;
    }
    if (retryTimer !== null) {
      clearTimeoutFn(retryTimer);
      retryTimer = null;
    }

    const pending = [...(retryBatch ?? []), ...queue.splice(0)];
    retryBatch = null;

    const finalBatch = pending.slice(0, config.batchSize);

    disposePromise = withRequestBound(
      (async () => {
        if (inFlight) {
          try {
            await inFlight;
          } catch {
            /* runBatch 内部已捕获；这里只保证卸载不抛 */
          }
        }
        if (finalBatch.length > 0) await sendFinal(finalBatch);
        if (pending.length > finalBatch.length) {
          droppedFailed += pending.length - finalBatch.length;
          logWarn(`[fishmemory-archive] dropped ${pending.length - finalBatch.length} event(s) on unload (only one bounded batch is flushed)`);
        }
      })(),
    );

    return disposePromise;
  }

  return {
    config,
    start,
    handleEvent,
    dispose,
    /** 立即冲刷当前队列（测试辅助；正常路径由定时器/批量触发）。 */
    async flushNow() {
      if (inFlight) {
        try {
          await inFlight;
        } catch {
          /* ignore */
        }
      }
      await flush();
    },
    stats() {
      return {
        queued: queue.length,
        retrying: retryBatch ? retryBatch.length : 0,
        inFlight: inFlight !== null,
        sent: sentCount,
        droppedOverflow,
        droppedFailed,
      };
    },
  };
}

/**
 * DSH Host 插件入口：激活时注册 `session/event` 订阅与健康探测，并使用 `ctx.effect`
 * 返回清理函数（卸载时停表、退订、尽力发送最后一批）。
 * @param {object} ctx Cordis context（只用 ctx.on / ctx.effect / ctx.logger）
 * @param {object} config cordis.patch.yml 插件行的 config
 */
export function apply(ctx, config) {
  if (!ctx || typeof ctx.effect !== 'function' || typeof ctx.on !== 'function') {
    throw new Error('[fishmemory-archive] apply(ctx, config) requires a Cordis context with effect() and on()');
  }

  const archiver = createArchiver(config, { logger: ctx.logger });

  ctx.effect(() => {
    const disposeSubscription = ctx.on('session/event', (session, event) => {
      archiver.handleEvent(session, event);
    });
    // 健康探测不 await：失败只 warning，不阻断 DSH 启动（docs/16 §6 第 1 条）。
    fireAndForgetStart(archiver, ctx);

    return async () => {
      try {
        if (typeof disposeSubscription === 'function') disposeSubscription();
      } catch (error) {
        try {
          ctx.logger?.warn?.(`[fishmemory-archive] failed to remove session/event subscription: ${error?.message ?? error}`);
        } catch {
          /* ignore */
        }
      }
      await archiver.dispose();
    };
  }, 'fishmemory-archive');
}

/** apply 内的 start() 兜底：start 自身已捕获全部失败，这里只防极端同步异常。 */
function fireAndForgetStart(archiver, ctx) {
  Promise.resolve(archiver.start()).catch((error) => {
    try {
      ctx.logger?.warn?.(`[fishmemory-archive] health probe failed: ${error?.message ?? error}`);
    } catch {
      /* ignore */
    }
  });
}
