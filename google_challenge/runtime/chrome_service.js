#!/usr/bin/env node
'use strict';
// Dedicated Chrome process, isolated BrowserContext per job, no external npm packages.
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { randomUUID, timingSafeEqual } = require('node:crypto');

const settings = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const HOST = '127.0.0.1'; // Deliberately not configurable to a public interface.
const PORT = Number(settings.port);
const DEBUG_PORT = Number(settings.cdp_port);
const TOKEN = settings.token;
const LIMIT = settings.max_body_bytes;
const MAX_JOBS = Number(settings.max_jobs);
const UA =
  settings.user_agent;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let chrome,
  browser,
  profile,
  stopping = false,
  active = 0,
  completed = 0;
let chromeLog = '';
const contexts = new Set();
const jobs = new Set();
// Unhandled popup/worker/OOPIF HTTP traffic must not bypass the page interceptor.
const offlineProxy = http.createServer((req, res) => {
  res.writeHead(502);
  res.end('Offline execution context');
});
offlineProxy.on('connect', (req, socket) =>
  socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n'),
);
offlineProxy.on('upgrade', (req, socket) => socket.destroy());
class RpcError extends Error {
  constructor(code, message, status = 500) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
class CDP {
  constructor(url) {
    this.url = url;
    this.next = 0;
    this.pending = new Map();
    this.listeners = [];
  }
  async connect() {
    this.ws = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.ws.close();
        reject(new Error('CDP connection timeout'));
      }, 4000);
      this.ws.onopen = () => {
        clearTimeout(timer);
        resolve();
      };
      this.ws.onerror = () => {
        clearTimeout(timer);
        reject(new Error('CDP connection failed'));
      };
    });
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id) {
        const item = this.pending.get(msg.id);
        if (!item) return;
        this.pending.delete(msg.id);
        clearTimeout(item.timer);
        msg.error ? item.reject(new Error(msg.error.message)) : item.resolve(msg.result || {});
      } else for (const fn of this.listeners) Promise.resolve(fn(msg)).catch(() => {});
    };
    this.ws.onclose = () => {
      for (const p of this.pending.values()) {
        clearTimeout(p.timer);
        p.reject(new Error('CDP connection closed'));
      }
      this.pending.clear();
    };
  }
  on(fn) {
    this.listeners.push(fn);
  }
  send(method, params = {}, timeout = 4000) {
    return new Promise((resolve, reject) => {
      const id = ++this.next;
      const timer = setTimeout(
        () => {
          this.pending.delete(id);
          reject(new RpcError('timeout', `CDP timeout: ${method}`, 504));
        },
        Math.max(1, timeout),
      );
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.ws.send(JSON.stringify({ id, method, params }));
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e);
      }
    });
  }
  close() {
    try {
      this.ws?.close();
    } catch {}
  }
}
function chromePath() { return settings.chrome_path; }
function validate(data) {
  if (!data || typeof data !== 'object' || Array.isArray(data))
    throw new RpcError('invalid_input', 'JSON object required', 400);
  if (typeof data.html !== 'string' || !data.html)
    throw new RpcError('invalid_input', 'Nonempty html required', 400);
  let u;
  try {
    u = new URL(data.url);
  } catch {
    throw new RpcError('invalid_input', 'Valid page url required', 400);
  }
  if (!['http:', 'https:'].includes(u.protocol) || u.username || u.password)
    throw new RpcError(
      'invalid_input',
      'Only HTTP(S) page origins without credentials are allowed',
      400,
    );
  const timeoutMs = data.timeoutMs ?? 10000;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 30000)
    throw new RpcError('invalid_input', 'timeoutMs must be 100..30000', 400);
  if (data.cookies !== undefined && (!Array.isArray(data.cookies) || data.cookies.length > 100))
    throw new RpcError('invalid_input', 'cookies must contain at most 100 entries', 400);
  const cookies = (data.cookies || []).map((c) => {
    if (!c || typeof c.name !== 'string' || typeof c.value !== 'string')
      throw new RpcError('invalid_input', 'Cookie name/value must be strings', 400);
    const out = { name: c.name, value: c.value };
    if (c.domain) {
      const domain = String(c.domain).replace(/^\./, '').toLowerCase();
      if (u.hostname !== domain && !u.hostname.endsWith('.' + domain))
        throw new RpcError('invalid_input', 'Cookie domain does not match page URL', 400);
      out.domain = String(c.domain);
      out.path = c.path || '/';
    } else out.url = u.origin + (c.path || '/');
    for (const key of ['secure', 'httpOnly']) if (key in c) out[key] = Boolean(c[key]);
    if (c.sameSite && ['Strict', 'Lax', 'None'].includes(c.sameSite)) out.sameSite = c.sameSite;
    if (Number.isFinite(c.expires) && c.expires > 0) out.expires = c.expires;
    return out;
  });
  return { html: data.html, url: u.href, timeoutMs, cookies };
}
async function execute(input) {
  const jobId = randomUUID(),
    started = Date.now(),
    deadline = started + input.timeoutMs;
  let contextId,
    tab,
    source = '',
    value = '',
    diagnostics = [],
    blockedRequests = 0,
    mainFulfilled = false;
  let outcome;
  const remaining = () => {
    const left = deadline - Date.now();
    if (left <= 0) throw new RpcError('timeout', 'Execution deadline exceeded', 504);
    return left;
  };
  try {
    ({ browserContextId: contextId } = await browser.send(
      'Target.createBrowserContext',
      {
        proxyServer: `http://${HOST}:${offlineProxy.address().port}`,
        proxyBypassList: '<-loopback>',
      },
      remaining(),
    ));
    contexts.add(contextId);
    const { targetId } = await browser.send(
      'Target.createTarget',
      { url: 'about:blank', browserContextId: contextId },
      remaining(),
    );
    const targets = await (
      await fetch(`http://${HOST}:${DEBUG_PORT}/json/list`, {
        signal: AbortSignal.timeout(remaining()),
      })
    ).json();
    const target = targets.find((t) => t.id === targetId);
    if (!target) throw new Error('New target not found');
    tab = new CDP(target.webSocketDebuggerUrl);
    await tab.connect();
    const send = (method, params = {}) => tab.send(method, params, remaining());
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Network.enable');
    const { frameTree } = await send('Page.getFrameTree');
    const mainFrame = frameTree.frame.id;
    tab.on(async (msg) => {
      if (msg.method === 'Fetch.requestPaused') {
        const p = msg.params;
        if (!mainFulfilled && p.resourceType === 'Document' && p.frameId === mainFrame) {
          mainFulfilled = true;
          await send('Fetch.fulfillRequest', {
            requestId: p.requestId,
            responseCode: 200,
            responseHeaders: [{ name: 'content-type', value: 'text/html; charset=utf-8' }],
            body: Buffer.from(input.html).toString('base64'),
          });
        } else {
          blockedRequests++;
          await send('Fetch.fulfillRequest', {
            requestId: p.requestId,
            responseCode: 204,
            body: '',
          });
        }
      }
      if (msg.method === 'Runtime.consoleAPICalled') {
        const text = (msg.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ');
        if (text.startsWith('SGSS_RESULT ')) {
          value = text.slice(12);
          source = 'console';
        }
      }
      if (msg.method === 'Runtime.exceptionThrown' && diagnostics.length < 10) {
        const e = msg.params.exceptionDetails;
        diagnostics.push({
          text: e.text,
          line: e.lineNumber,
          description: e.exception?.description?.slice(0, 500),
        });
      }
    });
    // Exact same navigation interception model as the working baseline, not setContent/eval.
    await send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    await send('Emulation.setUserAgentOverride', {
      userAgent: UA,
      platform: 'macOS',
      userAgentMetadata: {
        brands: [
          { brand: 'Google Chrome', version: '155' },
          { brand: 'Chromium', version: '155' },
          { brand: 'Not)A;Brand', version: '24' },
        ],
        fullVersionList: [
          { brand: 'Google Chrome', version: '155.0.8059.41' },
          { brand: 'Chromium', version: '155.0.8059.41' },
          { brand: 'Not)A;Brand', version: '24.0.0.0' },
        ],
        fullVersion: '155.0.8059.41',
        platform: 'macOS',
        platformVersion: '14.6.0',
        architecture: 'arm',
        model: '',
        mobile: false,
        bitness: '64',
        wow64: false,
      },
    });
    if (input.cookies.length) await send('Network.setCookies', { cookies: input.cookies });
    await send('Page.navigate', { url: input.url });
    let state = {};
    while (Date.now() < deadline) {
      const result = await send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(()=>{const cookie=document.cookie;const m=cookie.match(/(?:^|;\\s*)SG_SS=([^;]*)/);return {url:location.href,cookie,sg_ss:m?m[1]:'',readyState:document.readyState}})()`,
      });
      if (result.result?.value) state = result.result.value;
      if (!value && state.sg_ss) {
        value = state.sg_ss;
        source = 'document.cookie';
      }
      if (value) {
        outcome = {
          ok: true,
          jobId,
          sg_ss: value,
          source,
          url: state.url,
          readyState: state.readyState,
          cookie: state.cookie,
          sg_ss_len: value.length,
          ms: Date.now() - started,
          browser_pid: chrome.pid,
          diagnostics,
          blocked_requests: blockedRequests,
        };
        break;
      }
      await sleep(Math.min(30, remaining()));
    }
    if (!outcome) throw new RpcError('timeout', 'No SG_SS before execution deadline', 504);
  } catch (error) {
    outcome = {
      ok: false,
      jobId,
      error: error.code || 'execution_error',
      message: error.message,
      ms: Date.now() - started,
      browser_pid: chrome.pid,
      diagnostics,
      blocked_requests: blockedRequests,
      _status: error.status || 500,
    };
  } finally {
    if (!outcome?.ok && tab) await tab.send('Runtime.terminateExecution', {}, 1000).catch(() => {});
    tab?.close();
    if (contextId) {
      try {
        await browser.send('Target.disposeBrowserContext', { browserContextId: contextId }, 4000);
        contexts.delete(contextId);
      } catch (error) {
        outcome = {
          ...outcome,
          ok: false,
          error: 'cleanup_failed',
          message: error.message,
          _status: 500,
        };
      }
    }
  }
  completed++;
  return outcome;
}
function authorized(req) {
  if (!TOKEN) return true;
  const provided = Buffer.from(req.headers.authorization || '');
  const expected = Buffer.from('Bearer ' + TOKEN);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}
function reply(res, status, data) {
  if (res.destroyed) return;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(JSON.stringify(data));
}
function body(req) {
  return new Promise((resolve, reject) => {
    let size = 0,
      chunks = [],
      tooBig = false;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > LIMIT) {
        if (!tooBig) {
          tooBig = true;
          chunks = [];
          reject(new RpcError('too_large', 'Maximum JSON body is 8 MiB', 413));
        }
      } else if (!tooBig) chunks.push(chunk);
    });
    req.on('end', () => {
      if (tooBig) return;
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        reject(new RpcError('invalid_json', 'Invalid JSON', 400));
      }
    });
    req.on('error', reject);
  });
}
const server = http.createServer(async (req, res) => {
  try {
    // Prevent cross-origin browser callers; intended clients are local Python processes.
    if (req.headers.origin)
      throw new RpcError('origin_forbidden', 'Browser Origin requests are not accepted', 403);
    if (!authorized(req)) throw new RpcError('unauthorized', 'Bearer token required', 401);
    if (req.method === 'POST' && req.url === '/shutdown') {
      reply(res, 200, {ok: true});
      setImmediate(() => shutdown().finally(() => process.exit(0)));
      return;
    }
    if (req.method === 'GET' && req.url === '/health')
      return reply(res, 200, {
        ok: !!browser && chrome?.exitCode === null && !stopping,
        service: 'browser-rpc',
        service_pid: process.pid,
        browser_pid: chrome?.pid,
        active_jobs: active,
        active_contexts: contexts.size,
        completed_jobs: completed,
        port: PORT,
        cdp_port: DEBUG_PORT,
      });
    if (req.method !== 'POST' || req.url !== '/sgss')
      return reply(res, 404, { ok: false, error: 'not_found' });
    if (stopping || !browser || chrome.exitCode !== null)
      throw new RpcError('unavailable', 'Browser unavailable', 503);
    if (!/^application\/json(?:;|$)/i.test(req.headers['content-type'] || ''))
      throw new RpcError('invalid_content_type', 'application/json required', 415);
    const input = validate(await body(req));
    if (active >= MAX_JOBS)
      throw new RpcError('busy', 'Browser concurrency limit reached; retry later', 503);
    active++;
    const job = execute(input);
    jobs.add(job);
    let out;
    try {
      out = await job;
    } finally {
      active--;
      jobs.delete(job);
    }
    const status = out._status || 200;
    delete out._status;
    reply(res, status, out);
  } catch (error) {
    reply(res, error.status || 500, {
      ok: false,
      error: error.code || 'internal_error',
      message: error.message,
    });
  }
});
server.requestTimeout = 40000;
server.headersTimeout = 5000;
async function shutdown() {
  if (stopping) return;
  stopping = true;
  server.close();
  await Promise.race([Promise.allSettled([...jobs]), sleep(3000)]);
  browser?.close();
  if (chrome && chrome.exitCode === null) {
    chrome.kill('SIGTERM');
    await Promise.race([new Promise((r) => chrome.once('exit', r)), sleep(2000)]);
    if (chrome.exitCode === null) {
      chrome.kill('SIGKILL');
      await sleep(100);
    }
  }
  offlineProxy.close();
  if (profile) fs.rmSync(profile, { recursive: true, force: true });
}
async function main() {
  if (!Number.isInteger(MAX_JOBS) || MAX_JOBS < 1 || MAX_JOBS > 4)
    throw new Error('BROWSER_RPC_MAX_JOBS must be 1..4');
  // Fail on port collision before creating a Chrome profile.
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(PORT, HOST, resolve);
  });
  await new Promise((resolve, reject) => {
    offlineProxy.once('error', reject);
    offlineProxy.listen(0, HOST, resolve);
  });
  profile = fs.mkdtempSync(path.join(os.tmpdir(), 'sgss-browser-rpc-'));
  const args = [
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${profile}`,
    '--headless=new',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-sync',
    '--disable-features=Translate,OptimizationHints,MediaRouter',
    '--window-size=1365,900',
    'about:blank',
  ];
  chrome = spawn(chromePath(), args, { stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', (b) => {
    chromeLog = (chromeLog + b.toString()).slice(-2000);
  });
  let spawnError;
  chrome.on('error', (e) => {
    spawnError = e;
  });
  const end = Date.now() + 10000;
  let version;
  while (Date.now() < end) {
    if (spawnError) throw spawnError;
    if (chrome.exitCode !== null) throw new Error('Chrome exited: ' + chromeLog);
    try {
      version = await (
        await fetch(`http://${HOST}:${DEBUG_PORT}/json/version`, {
          signal: AbortSignal.timeout(500),
        })
      ).json();
      break;
    } catch {
      await sleep(100);
    }
  }
  if (!version) throw new Error('Chrome startup timeout');
  browser = new CDP(version.webSocketDebuggerUrl);
  await browser.connect();
  const { commandLine } = await browser.send('SystemInfo.getInfo');
  if (!commandLine.includes(profile))
    throw new Error('Debug port belongs to another browser; refusing to use it');
  console.log(
    JSON.stringify({
      event: 'ready',
      url: `http://${HOST}:${PORT}/sgss`,
      browser_pid: chrome.pid,
      browser: version.Browser,
    }),
  );
}
process.on('SIGTERM', () => shutdown().finally(() => process.exit(0)));
process.on('SIGINT', () => shutdown().finally(() => process.exit(0)));
main().catch(async (error) => {
  console.error(error.message);
  await shutdown();
  process.exitCode = 1;
});
