'use strict';

const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const { runEnterpriseInteraction } = require('./enterprise_interaction.js');
const { waitForTileReplacement } = require('./enterprise_tile_wait.js');
const { recordEnterpriseProtocolResponse, enterpriseEvidence, createEnterpriseCompletion, enterpriseProtocolOutcome } = require('./enterprise_evidence.js');
const nodeCrypto = require('node:crypto');
const nodeProcess = process;
const nodeBuffer = Buffer;
const nodeSetTimeout = setTimeout;
const nodeClearTimeout = clearTimeout;

const { installBrowserEnv } = require('./browser_core.js');



function emitJSON(message) {
  nodeProcess.stdout.write(JSON.stringify(message) + '\n');
}

function emitFinalJSON(message) {
  const data = JSON.stringify(message) + '\n';
  return new Promise((resolve, reject) => {
    try {
      nodeProcess.stdout.write(data, 'utf8', (error) => {
        if (error) reject(error);
        else resolve();
      });
    } catch (error) {
      reject(error);
    }
  });
}

function delay(ms) {
  return new Promise((resolve) => nodeSetTimeout(resolve, ms));
}

function normalizeError(error) {
  return {
    name: error && error.name ? String(error.name) : 'Error',
    message: error && error.message ? String(error.message) : String(error),
    stack: error && error.stack ? String(error.stack).split('\n').slice(0, 8).join('\n') : ''
  };
}

function bodyLength(body) {
  if (body === undefined || body === null) return 0;
  if (typeof body === 'string') return body.length;
  if (body && typeof body.byteLength === 'number') return Number(body.byteLength) || 0;
  if (Array.isArray(body)) return body.length;
  return String(body).length;
}

function bodyBase64(body) {
  if (body === undefined || body === null) return '';
  try {
    if (typeof body === 'string') return nodeBuffer.from(body, 'utf8').toString('base64');
    if (body instanceof ArrayBuffer) return nodeBuffer.from(new Uint8Array(body)).toString('base64');
    if (ArrayBuffer.isView(body)) return nodeBuffer.from(body.buffer, body.byteOffset, body.byteLength).toString('base64');
    if (Array.isArray(body)) return nodeBuffer.from(body).toString('base64');
  } catch {
  }
  return nodeBuffer.from(String(body), 'utf8').toString('base64');
}

function utf8Base64(text) {
  return nodeBuffer.from(String(text || ''), 'utf8').toString('base64');
}

function utf8Sha256(text) {
  return nodeCrypto.createHash('sha256').update(String(text || ''), 'utf8').digest('hex');
}

function redactURL(url) {
  try {
    const parsed = new URL(String(url));
    for (const key of ['token', 'c']) {
      const value = parsed.searchParams.get(key);
      if (value && value.length > 32) parsed.searchParams.set(key, value.slice(0, 16) + '...len' + value.length);
    }
    return parsed.href;
  } catch {
    return String(url || '');
  }
}

function isPlainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value);
}

function mergeEnvironment(base, override) {
  const out = Object.assign({}, base || {});
  if (!isPlainObject(override)) return out;
  for (const [key, value] of Object.entries(override)) {
    if (isPlainObject(value) && isPlainObject(out[key])) {
      out[key] = mergeEnvironment(out[key], value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

function summarizeDiagValue(value, depth = 0, seen = new Set()) {
  const type = typeof value;
  const out = { type };
  try {
    if (value == null) {
      out.value = value;
      return out;
    }
    const ctor = value && value.constructor && value.constructor.name;
    if (ctor) out.ctor = String(ctor);
    if (type === 'string') {
      out.length = value.length;
      out.prefix = value.slice(0, 200);
      out.suffix = value.slice(-120);
      return out;
    }
    if (type === 'number' || type === 'boolean' || type === 'bigint') {
      out.value = String(value);
      return out;
    }
    if (type === 'function') {
      out.name = value.name || '';
      out.length = value.length;
      return out;
    }
    if (ArrayBuffer.isView(value)) {
      out.length = value.length;
      out.byteLength = value.byteLength;
      out.prefix = Array.prototype.slice.call(value, 0, 64);
      return out;
    }
    if (value instanceof ArrayBuffer) {
      out.byteLength = value.byteLength;
      out.prefix = Array.prototype.slice.call(new Uint8Array(value), 0, 64);
      return out;
    }
    if (seen.has(value)) {
      out.circular = true;
      return out;
    }
    seen.add(value);
    if (Array.isArray(value)) {
      out.length = value.length;
      if (depth < 3) out.items = value.slice(0, 20).map((item) => summarizeDiagValue(item, depth + 1, seen));
      return out;
    }
    const keys = Object.keys(value);
    out.keys = keys.slice(0, 30);
    if (depth < 2) {
      out.props = {};
      for (const key of keys.slice(0, 12)) out.props[key] = summarizeDiagValue(value[key], depth + 1, seen);
    }
  } catch (error) {
    out.error = String(error && error.message || error);
  }
  return out;
}



function createLineBridge(defaultTimeoutMs = 180000) {
  const rl = readline.createInterface({ input: nodeProcess.stdin });
  const pending = new Map();
  let nextId = 0;

  rl.on('line', (line) => {
    let message;
    try {
      message = JSON.parse(String(line || '{}'));
    } catch {
      return;
    }
    const replyTo = String(message.reply_to || '');
    if (!replyTo || !pending.has(replyTo)) return;
    const item = pending.get(replyTo);
    pending.delete(replyTo);
    if (item.timer) nodeClearTimeout(item.timer);
    item.resolve(message);
  });

  rl.on('close', () => {
    for (const item of pending.values()) {
      if (item.timer) nodeClearTimeout(item.timer);
      item.reject(new Error('Python protocol bridge closed'));
    }
    pending.clear();
  });

  return {
    request(type, payload, timeoutMs) {
      const id = String(++nextId);
      const waitMs = Math.max(
        1000,
        Number(timeoutMs || defaultTimeoutMs || 180000),
      );
      emitJSON(Object.assign({ type, id }, payload || {}));
      return new Promise((resolve, reject) => {
        const timer = nodeSetTimeout(() => {
          pending.delete(id);
          reject(new Error(type + ' bridge timeout'));
        }, waitMs);
        pending.set(id, { resolve, reject, timer });
      });
    },
    close() {
      try {
        rl.close();
      } catch {
      }
    }
  };
}

function installCaptureHooks(state) {
  window.addEventListener('message', (event) => {
    const rawData = event && event.data;
    try {
      let diagPayload = null;
      if (rawData && typeof rawData === 'object') {
        diagPayload = rawData;
      } else if (typeof rawData === 'string' && rawData.includes('__recap')) {
        diagPayload = JSON.parse(rawData);
      }
      if (diagPayload && diagPayload.__recapBase64Diag) {
        state.workerDiagnostics.push(Object.assign({ source: 'window-message', origin: event.origin || '' }, diagPayload.item || {}));
        if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();
        return;
      }
      if (diagPayload && diagPayload.__recapFieldDiag) {
        state.fieldDiagnostics.push(Object.assign({ source: 'window-message', origin: event.origin || '' }, diagPayload.item || {}));
        if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();
        return;
      }
      if (diagPayload && diagPayload.__recapApiTouchDiag) {
        state.apiTouchDiagnostics.push(Object.assign({ source: 'window-message', origin: event.origin || '' }, diagPayload.item || {}));
        if (state.apiTouchDiagnostics.length > 2000) state.apiTouchDiagnostics.shift();
        if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();
        return;
      }
    } catch (_) {
    }
    let data = event && event.data;
    try {
      data = typeof data === 'string' ? data : JSON.stringify(data).slice(0, 300);
    } catch {
      data = String(data);
    }
    state.messages.push({ origin: event.origin, data });
  });
  window.addEventListener('error', (event) => {
    state.asyncErrors.push({ type: 'error', message: String(event && event.message || '') });
  });
  window.addEventListener('unhandledrejection', (event) => {
    state.asyncErrors.push({ type: 'unhandledrejection', reason: String(event && event.reason || '') });
  });

  if (state.enableWorkerDiagnostics && typeof Worker === 'function' && !Worker.__recapWorkerDiagWrapped) {
    const OriginalWorker = Worker;
    const DiagnosticWorker = function RecapDiagnosticWorker() {
      const worker = new OriginalWorker(...arguments);
      try {
        worker.addEventListener('message', (event) => {
          const data = event && event.data;
          if (data && data.__recapBase64Diag) {
            state.workerDiagnostics.push(Object.assign({ source: 'worker-message' }, data.item || {}));
            if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
            if (typeof event.stopPropagation === 'function') event.stopPropagation();
          }
          if (data && data.__recapFieldDiag) {
            state.fieldDiagnostics.push(Object.assign({ source: 'worker-message' }, data.item || {}));
            if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
            if (typeof event.stopPropagation === 'function') event.stopPropagation();
          }
          if (data && data.__recapApiTouchDiag) {
            state.apiTouchDiagnostics.push(Object.assign({ source: 'worker-message' }, data.item || {}));
            if (state.apiTouchDiagnostics.length > 2000) state.apiTouchDiagnostics.shift();
            if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
            if (typeof event.stopPropagation === 'function') event.stopPropagation();
          }
        }, true);
      } catch (_) {
      }
      return worker;
    };
    try {
      Object.setPrototypeOf(DiagnosticWorker, OriginalWorker);
      DiagnosticWorker.prototype = OriginalWorker.prototype;
      Object.defineProperty(DiagnosticWorker, '__recapWorkerDiagWrapped', { value: true });
    } catch (_) {
      DiagnosticWorker.__recapWorkerDiagWrapped = true;
    }
    window.Worker = DiagnosticWorker;
  }

  if (state.enableWorkerDiagnostics && typeof PerformanceObserver === 'function' && !PerformanceObserver.__recapPerfObserverDiagWrapped) {
    try {
      const OriginalPerformanceObserver = PerformanceObserver;
      const originalObserve = OriginalPerformanceObserver.prototype && OriginalPerformanceObserver.prototype.observe;
      const originalDisconnect = OriginalPerformanceObserver.prototype && OriginalPerformanceObserver.prototype.disconnect;
      const originalTakeRecords = OriginalPerformanceObserver.prototype && OriginalPerformanceObserver.prototype.takeRecords;
      const summarizeEntry = (entry) => {
        const out = {};
        for (const key of ['name', 'entryType', 'startTime', 'duration', 'initiatorType', 'responseStatus']) {
          try {
            if (entry && entry[key] !== undefined) out[key] = entry[key];
          } catch (_) {
          }
        }
        return out;
      };
      const DiagnosticPerformanceObserver = function RecapDiagnosticPerformanceObserver(callback) {
        const wrappedCallback = function recapPerformanceObserverCallback(list, observer) {
          try {
            const entries = list && typeof list.getEntries === 'function' ? list.getEntries() : [];
            state.performanceObserverDiagnostics.push({
              kind: 'callback',
              href: String(location.href || ''),
              entries: Array.prototype.slice.call(entries || [], 0, 12).map(summarizeEntry)
            });
          } catch (error) {
            state.performanceObserverDiagnostics.push({ kind: 'callback.error', href: String(location.href || ''), error: String(error) });
          }
          return callback.apply(this, arguments);
        };
        return new OriginalPerformanceObserver(wrappedCallback);
      };
      Object.setPrototypeOf(DiagnosticPerformanceObserver, OriginalPerformanceObserver);
      DiagnosticPerformanceObserver.prototype = OriginalPerformanceObserver.prototype;
      Object.defineProperty(DiagnosticPerformanceObserver, '__recapPerfObserverDiagWrapped', { value: true });
      if (typeof originalObserve === 'function') {
        DiagnosticPerformanceObserver.prototype.observe = function recapPerformanceObserverObserve(options) {
          try {
            state.performanceObserverDiagnostics.push({
              kind: 'observe',
              href: String(location.href || ''),
              options: {
                type: options && options.type,
                entryTypes: options && options.entryTypes ? Array.prototype.slice.call(options.entryTypes) : undefined,
                buffered: !!(options && options.buffered)
              }
            });
          } catch (_) {
          }
          return originalObserve.apply(this, arguments);
        };
      }
      if (typeof originalDisconnect === 'function') DiagnosticPerformanceObserver.prototype.disconnect = originalDisconnect;
      if (typeof originalTakeRecords === 'function') {
        DiagnosticPerformanceObserver.prototype.takeRecords = function recapPerformanceObserverTakeRecords() {
          const records = originalTakeRecords.apply(this, arguments);
          try {
            state.performanceObserverDiagnostics.push({
              kind: 'takeRecords',
              href: String(location.href || ''),
              entries: Array.prototype.slice.call(records || [], 0, 12).map(summarizeEntry)
            });
          } catch (error) {
            state.performanceObserverDiagnostics.push({ kind: 'takeRecords.error', href: String(location.href || ''), error: String(error) });
          }
          return records;
        };
      }
      window.PerformanceObserver = DiagnosticPerformanceObserver;
    } catch (error) {
      state.performanceObserverDiagnostics.push({ kind: 'install.error', href: String(location.href || ''), error: String(error) });
    }
  }

  if (!state.enableNetworkHooks) return;

  const originalFetch = window.fetch;
  window.fetch = function recaptchaCaptureFetch(input, init) {
    const rawUrl = typeof input === 'string' ? input : (input && input.url) || '';
    const url = new URL(String(rawUrl || ''), location.href).href;
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const body = init && init.body != null ? init.body : null;
    state.fetches.push({
      method,
      rawUrl: url,
      url: redactURL(url),
      bodyLength: bodyLength(body),
      bodyBase64: bodyBase64(body)
    });
    return originalFetch.apply(this, arguments);
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function captureOpen(method, url) {
    const recapMethod = String(method || 'GET').toUpperCase();
    // This hook may run cross-realm. The authoritative URL/body is also read
    // from __mini_browser__.network.entries after URL resolution.
    let recapUrl;
    try {
      recapUrl = new URL(String(url || ''), location.href).href;
    } catch {
      recapUrl = String(url || '');
    }
    try { Object.defineProperty(this, '__recapMethod', { configurable: true, writable: true, enumerable: false, value: recapMethod }); } catch { this.__recapMethod = recapMethod; }
    try { Object.defineProperty(this, '__recapUrl', { configurable: true, writable: true, enumerable: false, value: recapUrl }); } catch { this.__recapUrl = recapUrl; }
    try { Object.defineProperty(this, '__recapHeaders', { configurable: true, writable: true, enumerable: false, value: Object.create(null) }); } catch { this.__recapHeaders = Object.create(null); }
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function captureSetRequestHeader(name, value) {
    if (!this.__recapHeaders) { try { Object.defineProperty(this, '__recapHeaders', { configurable: true, writable: true, enumerable: false, value: Object.create(null) }); } catch { this.__recapHeaders = Object.create(null); } }
    this.__recapHeaders[String(name || '').toLowerCase()] = String(value);
    return originalSetRequestHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function captureSend(body) {
    state.xhrs.push({
      method: this.__recapMethod || 'GET',
      rawUrl: this.__recapUrl || '',
      url: redactURL(this.__recapUrl || ''),
      headers: Object.assign({}, this.__recapHeaders || {}),
      bodyLength: bodyLength(body),
      bodyBase64: bodyBase64(body)
    });
    return originalSend.apply(this, arguments);
  };

}

function installTextEncoderHook(state) {
  if (typeof TextEncoder !== 'function' || !TextEncoder.prototype) return;
  const originalEncode = TextEncoder.prototype.encode;
  if (typeof originalEncode !== 'function') return;
  TextEncoder.prototype.encode = function recapDebugEncode(input) {
    const text = input === undefined ? '' : String(input);
    if (text.length >= 80 || text.includes('bgdata') || text.includes('BDAA') || text.includes('examples/v3scores')) {
      let stack = '';
      try { stack = String(new Error().stack || '').split('\n').slice(1, 6).join('\n'); } catch {}
      state.textEncodes.push({
        length: text.length,
        utf8Length: nodeBuffer.byteLength(text, 'utf8'),
        sha256: utf8Sha256(text),
        base64: utf8Base64(text),
        prefix: text.slice(0, 160),
        suffix: text.slice(-80),
        stack
      });
      if (state.textEncodes.length > 300) state.textEncodes.shift();
    }
    return originalEncode.apply(this, arguments);
  };
}

function installMessagePortDiagnostics(state) {
  if (!state.enableWorkerDiagnostics) return;
  if (typeof MessagePort !== 'function' || !MessagePort.prototype) return;
  const originalPostMessage = MessagePort.prototype.postMessage;
  if (typeof originalPostMessage !== 'function' || originalPostMessage.__recapPortDiagWrapped) return;
  function recapPortPostMessage(message, transfer) {
    try {
      state.messagePortDiagnostics.push({
        kind: 'MessagePort.postMessage',
        href: typeof location !== 'undefined' && location && location.href || '',
        data: summarizeDiagValue(message),
        transferLength: transfer && typeof transfer.length === 'number' ? transfer.length : 0
      });
      if (state.messagePortDiagnostics.length > 300) state.messagePortDiagnostics.shift();
    } catch (_) {
    }
    return originalPostMessage.apply(this, arguments);
  }
  try {
    Object.defineProperty(recapPortPostMessage, '__recapPortDiagWrapped', { value: true });
    Object.defineProperty(recapPortPostMessage, 'name', { configurable: true, value: originalPostMessage.name || 'postMessage' });
    Object.defineProperty(recapPortPostMessage, 'length', { configurable: true, value: originalPostMessage.length });
  } catch (_) {
  }
  MessagePort.prototype.postMessage = recapPortPostMessage;
}

function installApiTouchDiagnostics(state) {
  if (!state.enableWorkerDiagnostics) return;
  const maxItems = 1200;
  const seenMethods = new Set();
  function currentTask() {
    try {
      const root = (typeof iN !== 'undefined' && iN) || (typeof window !== 'undefined' && window) || (typeof self !== 'undefined' && self) || globalThis;
      return root && root.__recapCurrentUTask || null;
    } catch (_) {
      return null;
    }
  }
  function record(kind, name, extra) {
    const task = currentTask();
    if (!task) return;
    try {
      state.apiTouchDiagnostics.push(Object.assign({
        kind,
        name,
        href: typeof location !== 'undefined' && location && location.href || '',
        task: {
          idx: task.idx,
          meta: task.meta && typeof task.meta === 'object' && 'value' in task.meta ? task.meta.value : task.meta
        }
      }, extra || {}));
      if (state.apiTouchDiagnostics.length > maxItems) state.apiTouchDiagnostics.shift();
    } catch (_) {
    }
  }
  function wrapMethod(obj, label, prop) {
    try {
      if (!obj || typeof obj[prop] !== 'function') return;
      const key = label + '.' + prop;
      if (seenMethods.has(key) || obj[prop].__recapApiTouchWrapped) return;
      const original = obj[prop];
      const wrapped = function recapApiTouchWrapped() {
        record('method', key, { argc: arguments.length });
        return original.apply(this, arguments);
      };
      try {
        Object.defineProperty(wrapped, 'name', { configurable: true, value: original.name || prop });
        Object.defineProperty(wrapped, 'length', { configurable: true, value: original.length });
        Object.defineProperty(wrapped, '__recapApiTouchWrapped', { value: true });
      } catch (_) {
      }
      obj[prop] = wrapped;
      seenMethods.add(key);
    } catch (_) {
    }
  }
  function wrapGetter(obj, label, prop) {
    try {
      if (!obj) return;
      const desc = Object.getOwnPropertyDescriptor(obj, prop);
      if (!desc || typeof desc.get !== 'function' || desc.get.__recapApiTouchWrapped) return;
      const originalGet = desc.get;
      const originalSet = desc.set;
      const wrappedGet = function recapApiTouchGetter() {
        record('getter', label + '.' + prop);
        return originalGet.call(this);
      };
      try {
        Object.defineProperty(wrappedGet, 'name', { configurable: true, value: originalGet.name || ('get ' + prop) });
        Object.defineProperty(wrappedGet, '__recapApiTouchWrapped', { value: true });
      } catch (_) {
      }
      Object.defineProperty(obj, prop, {
        configurable: desc.configurable,
        enumerable: desc.enumerable,
        get: wrappedGet,
        set: originalSet
      });
    } catch (_) {
    }
  }
  const navProto = typeof Navigator !== 'undefined' && Navigator.prototype;
  for (const prop of ['userAgent', 'appVersion', 'platform', 'language', 'languages', 'hardwareConcurrency', 'deviceMemory', 'maxTouchPoints', 'vendor', 'webdriver', 'cookieEnabled', 'pdfViewerEnabled', 'doNotTrack', 'plugins', 'mimeTypes', 'mediaDevices', 'storage', 'permissions', 'userAgentData', 'onLine']) {
    wrapGetter(navProto, 'Navigator', prop);
  }
  const docProto = typeof Document !== 'undefined' && Document.prototype;
  for (const prop of ['visibilityState', 'hidden', 'cookie', 'currentScript', 'fonts', 'hasFocus']) wrapGetter(docProto, 'Document', prop);
  for (const prop of ['createElement', 'createElementNS', 'getElementsByTagName', 'querySelector', 'querySelectorAll', 'hasStorageAccess', 'requestStorageAccess']) wrapMethod(docProto, 'Document', prop);
  const perfProto = typeof Performance !== 'undefined' && Performance.prototype;
  for (const prop of ['now', 'getEntries', 'getEntriesByName', 'getEntriesByType', 'mark', 'measure']) wrapMethod(perfProto, 'Performance', prop);
  wrapGetter(perfProto, 'Performance', 'memory');
  const canvasProto = typeof HTMLCanvasElement !== 'undefined' && HTMLCanvasElement.prototype;
  for (const prop of ['getContext', 'toDataURL', 'toBlob']) wrapMethod(canvasProto, 'HTMLCanvasElement', prop);
  const ctx2dProto = typeof CanvasRenderingContext2D !== 'undefined' && CanvasRenderingContext2D.prototype;
  for (const prop of ['getImageData', 'measureText', 'fillText', 'strokeText', 'isPointInPath', 'isPointInStroke']) wrapMethod(ctx2dProto, 'CanvasRenderingContext2D', prop);
  for (const ctorName of ['WebGLRenderingContext', 'WebGL2RenderingContext']) {
    const proto = typeof globalThis[ctorName] === 'function' && globalThis[ctorName].prototype;
    for (const prop of ['getParameter', 'getExtension', 'getSupportedExtensions', 'getContextAttributes', 'readPixels', 'getShaderPrecisionFormat']) wrapMethod(proto, ctorName, prop);
  }
  if (navigator && navigator.mediaDevices) {
    for (const prop of ['enumerateDevices', 'getSupportedConstraints']) wrapMethod(navigator.mediaDevices, 'MediaDevices', prop);
  }
  if (navigator && navigator.storage) {
    for (const prop of ['estimate', 'persisted']) wrapMethod(navigator.storage, 'StorageManager', prop);
  }
  if (navigator && navigator.permissions) wrapMethod(navigator.permissions, 'Permissions', 'query');
  const screenProto = typeof Screen !== 'undefined' && Screen.prototype;
  for (const prop of ['width', 'height', 'availWidth', 'availHeight', 'colorDepth', 'pixelDepth', 'availLeft', 'availTop', 'isExtended']) wrapGetter(screenProto, 'Screen', prop);
  const vvProto = typeof VisualViewport !== 'undefined' && VisualViewport.prototype;
  for (const prop of ['width', 'height', 'scale', 'offsetLeft', 'offsetTop', 'pageLeft', 'pageTop']) wrapGetter(vvProto, 'VisualViewport', prop);
  for (const prop of ['innerWidth', 'innerHeight', 'outerWidth', 'outerHeight', 'devicePixelRatio', 'screenX', 'screenY', 'screenLeft', 'screenTop', 'isSecureContext', 'crossOriginIsolated']) {
    wrapGetter(typeof Window !== 'undefined' && Window.prototype, 'Window', prop);
  }
  for (const prop of ['AudioContext', 'OfflineAudioContext', 'RTCPeerConnection', 'Worker', 'SharedWorker', 'indexedDB', 'caches', 'crypto', 'chrome', 'WebAssembly']) {
    try {
      if (prop in window) record('surface-present', 'Window.' + prop);
    } catch (_) {
    }
  }
}

function recaptchaSdkDiagnosticPatchSource() {
  return `
;(function(){try {
  var __recapDiagWindow = (typeof iN !== "undefined" && iN) || (typeof window !== "undefined" && window) || this;
  var __recapDiagHidden = function(root, name, fallback) {
    var value;
    try {
      value = root && root[name];
      if (!value) value = fallback;
      Object.defineProperty(root, name, { configurable: true, writable: true, enumerable: false, value: value });
      return value;
    } catch (__recapHiddenError) {
      try { root[name] = value || fallback; return root[name]; } catch (_) { return fallback; }
    }
  };
  var __recapDiag = __recapDiagHidden(__recapDiagWindow, "__recapSdkDiag", []);
  var __recapDiagClip = function(value, max) {
    value = String(value == null ? "" : value);
    max = max || 160;
    return value.length > max ? value.slice(0, max) + "...<" + value.length + ">" : value;
  };
  var __recapDiagSummary = function(value) {
    var out = { type: typeof value };
    try {
      if (value == null) {
        out.value = value;
      } else if (typeof value === "string") {
        out.length = value.length;
        out.prefix = value.slice(0, 180);
        out.suffix = value.slice(-120);
      } else if (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView && ArrayBuffer.isView(value)) {
        out.ctor = value.constructor && value.constructor.name || "";
        out.length = value.length;
        out.byteLength = value.byteLength;
        out.prefix = Array.prototype.slice.call(value, 0, 48);
      } else if (value instanceof ArrayBuffer) {
        out.ctor = "ArrayBuffer";
        out.byteLength = value.byteLength;
      } else if (typeof value === "object") {
        out.ctor = value.constructor && value.constructor.name || "";
        try { out.keys = Object.keys(value).slice(0, 20); } catch (_) {}
        try { out.string = __recapDiagClip(value && value.toString ? value.toString() : Object.prototype.toString.call(value), 220); } catch (_) {}
        try {
          var ye = value.Ye;
          if (ye && typeof ye === "object") {
            out.yeKeys = Object.keys(ye).slice(0, 40);
            out.ye = {};
            out.yeKeys.slice(0, 12).forEach(function(key) {
              var item = ye[key];
              var itemSummary = { type: typeof item };
              try {
                if (item == null) itemSummary.value = item;
                else if (typeof item === "string") itemSummary.string = __recapDiagClip(item, 180);
                else if (typeof item === "number" || typeof item === "boolean" || typeof item === "bigint") itemSummary.value = String(item);
                else if (Array.isArray(item)) itemSummary.array = item.slice(0, 8).map(function(part) { return __recapDiagClip(part, 80); });
                else {
                  itemSummary.ctor = item.constructor && item.constructor.name || "";
                  itemSummary.keys = Object.keys(item).slice(0, 12);
                  itemSummary.string = __recapDiagClip(item && item.toString ? item.toString() : Object.prototype.toString.call(item), 180);
                }
              } catch (itemError) {
                itemSummary.error = String(itemError);
              }
              out.ye[key] = itemSummary;
            });
          }
        } catch (_) {}
        try {
          var schema = value.A || (value.constructor && value.constructor.prototype && value.constructor.prototype.A);
          if (schema && typeof schema === "object") {
            out.schema = Array.isArray(schema) ? schema.slice(0, 20).map(function(part) { return typeof part === "function" ? ("function " + (part.name || "")) : __recapDiagClip(part, 80); }) : __recapDiagClip(schema, 180);
          }
        } catch (_) {}
      } else {
        out.value = __recapDiagClip(value, 220);
      }
    } catch (error) {
      out.error = String(error);
    }
    return out;
  };
  var __recapDiagPush = function(kind, data) {
    try {
      var item = { kind: kind, t: Date.now(), href: __recapDiagWindow.location && __recapDiagWindow.location.href || "" };
      if (data) for (var key in data) item[key] = data[key];
      __recapDiag.push(item);
      try {
        if (__recapDiagWindow.top && __recapDiagWindow.top !== __recapDiagWindow) {
          __recapDiagWindow.top.postMessage(JSON.stringify({ __recapSdkDiag: true, item: item }), "*");
        }
      } catch (_) {}
    } catch (_) {}
  };
  if (typeof AI !== "undefined" && AI && AI.prototype && !AI.prototype.__recapDiagWrapped) {
    Object.defineProperty(AI.prototype, "__recapDiagWrapped", { value: true });
    var __recapOrigSet = AI.prototype.set;
    var __recapOrigLoad = AI.prototype.load;
    var __recapOrigExecute = AI.prototype.execute;
    AI.prototype.set = function(q) {
      __recapDiagPush("AI.set", { arg: __recapDiagSummary(q) });
      return __recapOrigSet.apply(this, arguments);
    };
    AI.prototype.load = function() {
      __recapDiagPush("AI.load.call", { hasD: !!this.D, D: __recapDiagSummary(this.D) });
      var ret = __recapOrigLoad.apply(this, arguments);
      try {
        if (this.L && typeof this.L.then === "function" && !this.__recapDiagLoadWrapped) {
          try{Object.defineProperty(this,"__recapDiagLoadWrapped",{configurable:true,writable:true,enumerable:false,value:true});}catch(__recapDiagLoadFlagError){this.__recapDiagLoadWrapped=true;}
          this.L = this.L.then(function(bg) {
            __recapDiagPush("AI.load.resolved", { bg: __recapDiagSummary(bg), hasInvoke: !!(bg && bg.invoke) });
            try {
              if (bg && typeof bg.invoke === "function" && !bg.__recapDiagInvokeWrapped) {
                Object.defineProperty(bg, "__recapDiagInvokeWrapped", { value: true });
                var __recapOrigInvoke = bg.invoke;
                bg.invoke = function(callback, flag) {
                  __recapDiagPush("botguard.invoke.call", { flag: flag, callbackType: typeof callback });
                  return __recapOrigInvoke.call(this, function(value) {
                    __recapDiagPush("botguard.invoke.callback", { value: __recapDiagSummary(value) });
                    if (typeof callback === "function") return callback.apply(this, arguments);
                  }, flag);
                };
              }
            } catch (wrapError) {
              __recapDiagPush("AI.load.wrapError", { error: String(wrapError) });
            }
            return bg;
          }, function(error) {
            __recapDiagPush("AI.load.rejected", { error: String(error) });
            throw error;
          });
        }
      } catch (error) {
        __recapDiagPush("AI.load.error", { error: String(error) });
      }
      return ret;
    };
    AI.prototype.execute = function(q) {
      __recapDiagPush("AI.execute.call", { callbackType: typeof q });
      var ret = __recapOrigExecute.apply(this, arguments);
      try {
        if (ret && typeof ret.then === "function") {
          return ret.then(function(value) {
            __recapDiagPush("AI.execute.resolved", { value: __recapDiagSummary(value) });
            return value;
          }, function(error) {
            __recapDiagPush("AI.execute.rejected", { error: String(error) });
            throw error;
          });
        }
      } catch (error) {
        __recapDiagPush("AI.execute.error", { error: String(error) });
      }
      return ret;
    };
    __recapDiagPush("AI.hook.installed", {});
  }
} catch (__recapDiagInstallError) {
  try {
    var __recapDiagFallbackWindow = (typeof window !== "undefined" && window) || this;
    var __recapDiagFallbackArr;
    try{__recapDiagFallbackArr=__recapDiagFallbackWindow.__recapSdkDiag||[];Object.defineProperty(__recapDiagFallbackWindow,"__recapSdkDiag",{configurable:true,writable:true,enumerable:false,value:__recapDiagFallbackArr});}catch(__recapFallbackHiddenError){__recapDiagFallbackArr=__recapDiagFallbackWindow.__recapSdkDiag=__recapDiagFallbackWindow.__recapSdkDiag||[];}
    __recapDiagFallbackArr.push({ kind: "AI.hook.installError", error: String(__recapDiagInstallError) });
  } catch (_) {}
}
})();
`;
}

function patchRecaptchaBase64EncoderSource(source) {
  if (typeof source !== 'string' || source.includes('__recapBase64DiagWrap')) return source;
  const needle = 'N=pG&&!r?iN.btoa(w):S[2](49,64,z[26](x[1],x[0],8,w),r)';
  if (!source.includes(needle)) return source;
  const replacement = `N=(function __recapBase64DiagWrap(__recapInput,__recapUrlSafe){
try{
var __recapOutput=pG&&!__recapUrlSafe?iN.btoa(__recapInput):S[2](49,64,z[26](x[1],x[0],8,__recapInput),__recapUrlSafe);
try{
var __recapWin=(typeof iN!=="undefined"&&iN)||(typeof window!=="undefined"&&window)||this;
var __recapArr;
try{__recapArr=__recapWin.__recapBase64Diag||[];Object.defineProperty(__recapWin,"__recapBase64Diag",{configurable:true,writable:true,enumerable:false,value:__recapArr});}catch(__recapBase64HiddenError){__recapArr=__recapWin.__recapBase64Diag=__recapWin.__recapBase64Diag||[];}
var __recapLen=0,__recapType=typeof __recapInput,__recapHex="",__recapAscii="";
if(__recapInput!=null){
if(typeof __recapInput==="string"){
__recapLen=__recapInput.length;
for(var __recapI=0;__recapI<__recapInput.length&&__recapI<96;__recapI++){
var __recapC=__recapInput.charCodeAt(__recapI)&255;
__recapHex+=(__recapC<16?"0":"")+__recapC.toString(16);
__recapAscii+=__recapC>=32&&__recapC<127?String.fromCharCode(__recapC):".";
}
}else if(typeof ArrayBuffer!=="undefined"&&ArrayBuffer.isView&&ArrayBuffer.isView(__recapInput)){
__recapLen=__recapInput.byteLength;
for(var __recapJ=0;__recapJ<__recapInput.byteLength&&__recapJ<96;__recapJ++){
var __recapB=__recapInput[__recapJ]&255;
__recapHex+=(__recapB<16?"0":"")+__recapB.toString(16);
__recapAscii+=__recapB>=32&&__recapB<127?String.fromCharCode(__recapB):".";
}
}else if(__recapInput instanceof ArrayBuffer){
var __recapView=new Uint8Array(__recapInput);
__recapLen=__recapView.byteLength;
for(var __recapK=0;__recapK<__recapView.byteLength&&__recapK<96;__recapK++){
var __recapV=__recapView[__recapK]&255;
__recapHex+=(__recapV<16?"0":"")+__recapV.toString(16);
__recapAscii+=__recapV>=32&&__recapV<127?String.fromCharCode(__recapV):".";
}
}else if(typeof __recapInput.length==="number"){
__recapLen=__recapInput.length;
}
}
if(__recapLen>=80||String(__recapOutput||"").length>=80){
var __recapStack="";
try{__recapStack=String((new Error).stack||"").split("\\n").slice(1,7).join("\\n");}catch(__recapStackError){}
var __recapItem={kind:"sdk.base64",t:Date.now(),href:__recapWin.location&&__recapWin.location.href||"",inputType:__recapType,inputLength:__recapLen,urlSafe:!!__recapUrlSafe,outputLength:String(__recapOutput||"").length,outputPrefix:String(__recapOutput||"").slice(0,120),outputSuffix:String(__recapOutput||"").slice(-80),inputHexPrefix:__recapHex,inputAsciiPrefix:__recapAscii,stack:__recapStack};
__recapArr.push(__recapItem);
try{if(__recapWin.top&&__recapWin.top!==__recapWin)__recapWin.top.postMessage(JSON.stringify({__recapBase64Diag:true,item:__recapItem}),"*");else if(typeof __recapWin.document==="undefined"&&typeof __recapWin.postMessage==="function")__recapWin.postMessage({__recapBase64Diag:true,item:__recapItem});}catch(__recapPostError){}
if(__recapArr.length>200)__recapArr.shift();
}
}catch(__recapDiagError){}
return __recapOutput;
}catch(__recapEncodeError){throw __recapEncodeError;}
})(w,r)`;
  return source.replace(needle, replacement);
}

function patchRecaptchaFieldWriterSource(source) {
  if (typeof source !== 'string' || source.includes('__recapFieldDiagWriter')) return source;
  const needle = '(u=y[9](4,0,"number",Dv[0],V,A))?u(D,rh,V):V<Dv[1]||S[31](10,w,Dv[2],AA)';
  if (!source.includes(needle)) return source;
  const replacement = '(u=y[9](4,0,"number",Dv[0],V,A))?(((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(V,rh,A),u(D,rh,V)):V<Dv[1]||S[31](10,w,Dv[2],AA)';
  return source.replace(needle, replacement);
}

function patchRecaptchaExtensionWriterSource(source) {
  if (typeof source !== 'string') return source;
  const needle = 'z[45](9,x,function(V,rh,u,Dv,fy){for(Dv=(g[7]((fy=[0,3,5],fy[2]),D,D.D.end()),fy[0]);Dv<u.length;Dv++)g[7](4,D,C[43](fy[1],fy[0],u[Dv])||new Uint8Array(0))})';
  if (!source.includes(needle)) return source;
  const replacement = 'z[45](9,x,function(V,rh,u,Dv,fy){((typeof iN!=="undefined"&&iN.__recapExtensionDiag)||(typeof self!=="undefined"&&self.__recapExtensionDiag)||function(){})(V,rh,u);for(Dv=(g[7]((fy=[0,3,5],fy[2]),D,D.D.end()),fy[0]);Dv<u.length;Dv++)g[7](4,D,C[43](fy[1],fy[0],u[Dv])||new Uint8Array(0))})';
  return source.replace(needle, replacement);
}

function patchRecaptchaReloadBuilderSource(source) {
  if (typeof source !== 'string' || source.includes('__recapReloadBuilderDiagHook')) return source;
  const beforeNeedle = 'G=F[1](38,14,g[33](Q[0],O[0],C[42](45,2,new h6,D.L.P.value))),N&&g[12](32,5,G,N,W),T&&g[12](8,4,G,T,W),f)&&g[12](8,O[1],G,f,W),R&&g[12](8,24,G,R,W)';
  const beforeReplacement = 'G=F[1](38,14,g[33](Q[0],O[0],C[42](45,2,new h6,D.L.P.value))),((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapReloadBuilderDiagHook",phase:"beforeFields",fieldWxNo:r,fieldJbNo:w,A:A,D:D,d:d,N:N,T:T,f:f,R:R,keys:{Hc:Hc&&Hc[Q[2]],Wx:Wx&&Wx[Q[2]],iJ:iJ&&iJ[Q[2]],ez:ez&&ez[Q[2]],oa:oa&&oa[Q[2]],TU:TU&&TU[Q[2]],PF:PF&&PF[Q[2]],jB:jB&&jB[Q[2]]},mapped:{field5:N,field4:T,field16:f,field24:R,field8:d&&d[Hc[Q[2]]],fieldWx:d&&d[Wx[Q[2]]],field11:d&&d[iJ[Q[2]]],field10:d&&d[ez[Q[2]]],field15:d&&d[oa[Q[2]]],field17:d&&d[TU[Q[2]]],field28:d&&d[PF[Q[2]]],fieldJb:d&&d[jB[Q[2]]]}}),N&&g[12](32,5,G,N,W),T&&g[12](8,4,G,T,W),f)&&(((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(16,f,"reloadBuilder.field16"),g[12](8,O[1],G,f,W)),R&&g[12](8,24,G,R,W)';
  let out = source.includes(beforeNeedle) ? source.replace(beforeNeedle, beforeReplacement) : source;
  const fieldJbNeedle = 'd[jB[Q[2]]]&&C[36](19,w,G,d[jB[Q[2]]],void 0,W)';
  const fieldJbReplacement = 'd[jB[Q[2]]]&&(((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(w,d[jB[Q[2]]],"reloadBuilder.fieldJb"),C[36](19,w,G,d[jB[Q[2]]],void 0,W))';
  out = out.includes(fieldJbNeedle) ? out.replace(fieldJbNeedle, fieldJbReplacement) : out;
  const field20Needle = 'g[12](32,20,G,l,W),D.H=[],D.u=[];Y=(D.K&&g[12](Q[0],27,G,D.K,W),G)';
  const field20Replacement = 'g[12](32,20,G,l,W),((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(20,l,"reloadBuilder.field20"),((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapReloadBuilderDiagHook",phase:"field20",field20:l,D:D}),D.H=[],D.u=[];Y=(D.K&&g[12](Q[0],27,G,D.K,W),G)';
  out = out.includes(field20Needle) ? out.replace(field20Needle, field20Replacement) : out;
  return out;
}

function patchRecaptchaFingerprintArraySource(source) {
  if (typeof source !== 'string' || source.includes('__recapFingerprintArrayDiagHook')) return source;
  const needle = 'f=[C[34](38,p,g[a[0]](22,N),U),e[30](53,N,U),J[12](18,1372,r,w,D),v,C[15](77,x[0],g[a[0]](69,x[0]),-100),J[27](57,B),C[15](84,x[0],g[a[0]](53,x[0]),g[a[0]](55,B)),S[1](41,B),C[34](37,KF,d,d),E,e[0](21,H[39](2,x[2]),[y[37](3,-1)]),p,R,KF],n=CF(f),Vy[a[2]]().D(N,';
  if (!source.includes(needle)) return source;
  const replacement = 'f=[C[34](38,p,g[a[0]](22,N),U),e[30](53,N,U),J[12](18,1372,r,w,D),v,C[15](77,x[0],g[a[0]](69,x[0]),-100),J[27](57,B),C[15](84,x[0],g[a[0]](53,x[0]),g[a[0]](55,B)),S[1](41,B),C[34](37,KF,d,d),E,e[0](21,H[39](2,x[2]),[y[37](3,-1)]),p,R,KF],((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(22,f,"__recapFingerprintArrayDiagHook.input"),n=CF(f),((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(22,n,"__recapFingerprintArrayDiagHook.output"),((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapFingerprintArrayDiagHook",phase:"fingerprintArray",input:f,output:n,N:N,B:B,v:v,E:E,R:R}),Vy[a[2]]().D(N,';
  return source.replace(needle, replacement);
}

function patchRecaptchaCFSource(source) {
  if (typeof source !== 'string' || source.includes('__recapCFDiagHook')) return source;
  const needle = 'CF=function(){var q=[2,1,0],r=[0,16,2],w=cN.apply(r[q[2]],arguments).flat(Infinity),D=L[13](q[1],r[q[2]],w);return(w=D.filter(function(d){return H[44](42,d,1)===7}).length,D=S[q[0]](48,64,H[46](10,r[q[2]],r[q[1]],3,r[q[0]],D),r[q[0]]),b)[21](q[0],\nD,w)}';
  if (!source.includes(needle)) return source;
  const replacement = 'CF=function(){var __recapCFArgs=Array.prototype.slice.call(arguments,0),q=[2,1,0],r=[0,16,2],w=cN.apply(r[q[2]],arguments).flat(Infinity),D=L[13](q[1],r[q[2]],w),__recapCFCount;return __recapCFCount=D.filter(function(d){return H[44](42,d,1)===7}).length,D=S[q[0]](48,64,H[46](10,r[q[2]],r[q[1]],3,r[q[0]],D),r[q[0]]),D=(b)[21](q[0],D,__recapCFCount),((typeof iN!=="undefined"&&iN.__recapFieldDiag)||(typeof self!=="undefined"&&self.__recapFieldDiag)||function(){})(22,D,"__recapCFDiagHook.output"),((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapCFDiagHook",phase:"CF",args:__recapCFArgs,flatLength:w.length,count:__recapCFCount,output:D}),D}';
  return source.replace(needle, replacement);
}

function patchRecaptchaField16UpstreamSource(source) {
  if (typeof source !== 'string' || source.includes('__recapField16UpstreamDiagHook')) return source;
  const needle = 'A=function(){return(0,rJ.TR)(D.nE.bind(D,new Pf(w.aV)),r).then(function(x,N){return L[(N=[9,30,29],N)[1]](N[0],6,z[27](2,N[0],N[2],D,w.FL,x),GG)})}';
  if (!source.includes(needle)) return source;
  const replacement = 'A=function(){return(0,rJ.TR)(D.nE.bind(D,new Pf(w.aV)),r).then(function(x,N,__recapZ){return N=[9,30,29],__recapZ=z[27](2,N[0],N[2],D,w.FL,x),((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapField16UpstreamDiagHook",phase:"nE.then",x:x,z:__recapZ,D:D,FL:w.FL}),L[N[1]](N[0],6,__recapZ,GG)})}';
  return source.replace(needle, replacement);
}

function patchRecaptchaNERawInputsSource(source) {
  if (typeof source !== 'string' || source.includes('__recapNERawInputsDiagHook')) return source;
  const viNeedle = 'D)[E[2]]=function(v,R,B,h,G,a,T,m,t){if(a=(t=(B=[!1,1,3],[1,0,18]),v)[t[1]],a>t[1])';
  const viReplacement = 'D)[E[2]]=function(v,R,B,h,G,a,T,m,t){((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapVIRawDiagHook",phase:"D.vI",v:v,D:D});if(a=(t=(B=[!1,1,3],[1,0,18]),v)[t[1]],a>t[1])';
  source = source.includes(viNeedle) ? source.replace(viNeedle, viReplacement) : source;
  const initialBotguardNeedle = 'r5N(e[7](44),F[14](68)).then(function(U,f){return z[18](1,function(n,E){if(n[E=["ZY","P","L"],E[2]]==1)return n.D(D[E[0]].send(Df,new OE),\n2);return((f=n[E[1]],U).Ei(f.ib),n).return(f)})})';
  const initialBotguardDiag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const initialBotguardReplacement = `r5N(e[7](44),F[14](68)).then(function(U,f){${initialBotguardDiag}({hook:"__recapInitialBotguardDiagHook",phase:"r5N.resolved",U:U,D:D});return z[18](1,function(n,E){if(n[E=["ZY","P","L"],E[2]]==1)return ${initialBotguardDiag}({hook:"__recapInitialBotguardDiagHook",phase:"before ZY.send",U:U,D:D,ZY:D&&D[E[0]]}),n.D(D[E[0]].send(Df,new OE),\n2);return f=n[E[1]],${initialBotguardDiag}({hook:"__recapInitialBotguardDiagHook",phase:"ZY.send.resolved.before U.Ei",U:U,f:f,ib:f&&f.ib,D:D}),U.Ei(f.ib),${initialBotguardDiag}({hook:"__recapInitialBotguardDiagHook",phase:"after U.Ei",U:U,f:f,ib:f&&f.ib,D:D}),n.return(f)})})`;
  source = source.includes(initialBotguardNeedle) ? source.replace(initialBotguardNeedle, initialBotguardReplacement) : source;
  const eiNeedle = 'D).BI&&--v,T.Ei(E.ib),a).Ei(E.ib),G).Ei(E.ib),m.Ei(E.ib),n).Ei(E.ib),mb)(E.ib)';
  const eiPost = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const eiReplacement = `D).BI&&--v,${eiPost}({hook:"__recapField16EiStageDiagHook",phase:"before Ei",ib:E.ib}),T.Ei(E.ib),${eiPost}({hook:"__recapField16EiStageDiagHook",phase:"after T.Ei",ib:E.ib}),a).Ei(E.ib),${eiPost}({hook:"__recapField16EiStageDiagHook",phase:"after a.Ei",ib:E.ib}),G).Ei(E.ib),${eiPost}({hook:"__recapField16EiStageDiagHook",phase:"after G.Ei",ib:E.ib}),m.Ei(E.ib),${eiPost}({hook:"__recapField16EiStageDiagHook",phase:"after m.Ei",ib:E.ib}),n).Ei(E.ib),${eiPost}({hook:"__recapField16EiStageDiagHook",phase:"after n.Ei",ib:E.ib}),mb)(E.ib)`;
  source = source.includes(eiNeedle) ? source.replace(eiNeedle, eiReplacement) : source;
  const needle = 'F[10](25,!1,p[0],[N,C[15](32,A[p[2]],!1,1,18),dhj(e[7](p[1]),void 0,void 0,N,this.D.v),A5N(),xhn(),Nre(),Kyh(),r]).then(function(U,f,n,E,v,R,B,h,G,a,T,m,t){return(R=(n=(m=(a=(T=(f=(E=(t=g[34](6,U),t.next()).value,t.next().value),t.next().value),t.next()).value,G=t.next().value,t.next().value),t.next()).value,z)[18](18,function(O,l,Q,Y,X,k,q3,M,V,rh,u,Dv,fy,ny,py){return(B=(X=(rh=(l=(Y=(k=(Q=(V=(fy=(q3=(u=new (((((v=(h=((D.HI=new yl((M=';
  const replacement = 'F[10](25,!1,p[0],[N,C[15](32,A[p[2]],!1,1,18),dhj(e[7](p[1]),void 0,void 0,N,this.D.v),A5N(),xhn(),Nre(),Kyh(),r]).then(function(U,f,n,E,v,R,B,h,G,a,T,m,t){((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapNERawInputsDiagHook",phase:"K.then.raw",U:U,D:D});return(R=(n=(m=(a=(T=(f=(E=(t=g[34](6,U),t.next()).value,t.next().value),t.next().value),t.next()).value,G=t.next().value,t.next().value),t.next()).value,z)[18](18,function(O,l,Q,Y,X,k,q3,M,V,rh,u,Dv,fy,ny,py){return(B=(X=(rh=(l=(Y=(k=(Q=(V=(fy=(q3=(u=new (((((v=(h=((D.HI=new yl((M=';
  if (source.includes(needle)) return source.replace(needle, replacement);
  const shortNeedle = 'F[10](25,!1,p[0],[N,C[15](32,A[p[2]],!1,1,18),dhj(e[7](p[1]),void 0,void 0,N,this.D.v),A5N(),xhn(),Nre(),Kyh(),r]).then(function(U,f,n,E,v,R,B,h,G,a,T,m,t){return';
  const shortReplacement = 'F[10](25,!1,p[0],[N,C[15](32,A[p[2]],!1,1,18),dhj(e[7](p[1]),void 0,void 0,N,this.D.v),A5N(),xhn(),Nre(),Kyh(),r]).then(function(U,f,n,E,v,R,B,h,G,a,T,m,t){((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapNERawInputsDiagHook",phase:"K.then.raw",U:U,D:D});return';
  return source.includes(shortNeedle) ? source.replace(shortNeedle, shortReplacement) : source;
}

function patchRecaptchaField16EncodeSource(source) {
  if (typeof source !== 'string' || source.includes('__recapField16EncodeDiagHook')) return source;
  const needle = 'K.then(function(U,f){return Promise.resolve((f=[10,255,1],g[f[0]](12,4,Y5,L[f[0]](6,256,f[1],b[f[2]](f[2],63,U),fb))))})';
  if (!source.includes(needle)) return source;
  const replacement = 'K.then(function(U,f,__recapRaw,__recapOut){return f=[10,255,1],__recapRaw=b[f[2]](f[2],63,U),__recapOut=g[f[0]](12,4,Y5,L[f[0]](6,256,f[1],__recapRaw,fb)),((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})({hook:"__recapField16EncodeDiagHook",phase:"field16.encode",U:U,raw:__recapRaw,out:__recapOut,D:D}),Promise.resolve(__recapOut)})';
  return source.replace(needle, replacement);
}

function patchRecaptchaFjSendSource(source) {
  if (typeof source !== 'string' || source.includes('__recapFjSendDiagHook')) return source;
  const needle = 'Fj.prototype).send=function(q,r,w,D,d,A){return z[18]((w=(A=this,w===(r=r===void 0?null:r,void 0)?15E3:w),18),function(x,\nN){return(N=[38,"D",5],x).L==1?(D=C[N[2]](31),d=new q8,A.L.set(D,d),L[44](N[0],function(){d.reject(Error("reCAPTCHA Timeout ("+q+")")),A.L["delete"](D)},w),x[N[1]](e[14](7,0,A,r,q,D),2)):x.return(d.promise)})},Fj.prototype).X=function()';
  if (!source.includes(needle)) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const replacement = `Fj.prototype).send=function(q,r,w,D,d,A){${diag}({hook:"__recapFjSendDiagHook",phase:"send.call",q:q,r:r,thisObj:this});var __recapFjPromise=z[18]((w=(A=this,w===(r=r===void 0?null:r,void 0)?15E3:w),18),function(x,
N){return(N=[38,"D",5],x).L==1?(D=C[N[2]](31),d=new q8,A.L.set(D,d),L[44](N[0],function(){d.reject(Error("reCAPTCHA Timeout ("+q+")")),A.L["delete"](D)},w),x[N[1]](e[14](7,0,A,r,q,D),2)):x.return(d.promise)});try{return __recapFjPromise&&typeof __recapFjPromise.then==="function"?__recapFjPromise.then(function(__recapFjResult){${diag}({hook:"__recapFjSendDiagHook",phase:"send.resolved",q:q,r:r,result:__recapFjResult,thisObj:A});return __recapFjResult},function(__recapFjError){${diag}({hook:"__recapFjSendDiagHook",phase:"send.rejected",q:q,r:r,error:String(__recapFjError),thisObj:A});throw __recapFjError}):__recapFjPromise}catch(__recapFjWrapError){return __recapFjPromise}},Fj.prototype).X=function()`;
  return source.replace(needle, replacement);
}

function patchRecaptchaBotguardHandlerSource(source) {
  if (typeof source !== 'string' || source.includes('__recapBotguardHandlerDiagHook')) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const replacements = [
    {
      name: 'zhDfC12',
      needle: 'Df,function(f){return C[12](26,w,r,"",8,D,f)}',
      replacement: `Df,function(f){${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.call",variant:"zhDfC12",request:f,D:D});var __recapBghRet=C[12](26,w,r,"",8,D,f);try{return __recapBghRet&&typeof __recapBghRet.then==="function"?__recapBghRet.then(function(__recapBghValue){${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.resolved",variant:"zhDfC12",result:__recapBghValue,ib:__recapBghValue&&__recapBghValue.ib});return __recapBghValue},function(__recapBghError){${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.rejected",variant:"zhDfC12",error:String(__recapBghError)});throw __recapBghError}):(${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.return",variant:"zhDfC12",result:__recapBghRet,ib:__recapBghRet&&__recapBghRet.ib}),__recapBghRet)}catch(__recapBghWrapError){return __recapBghRet}}`
    },
    {
      name: 'enU_L8',
      needle: 'U_,function(f,n){return(n=[8,null,69],L)[n[0]](n[2],2,17,5,n[1],D,f)}',
      replacement: `U_,function(f,n){${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.call",variant:"enU_L8",request:f,D:D});n=[8,null,69];var __recapBghRet=L[n[0]](n[2],2,17,5,n[1],D,f);try{return __recapBghRet&&typeof __recapBghRet.then==="function"?__recapBghRet.then(function(__recapBghValue){${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.resolved",variant:"enU_L8",result:__recapBghValue,ib:__recapBghValue&&__recapBghValue.ib});return __recapBghValue},function(__recapBghError){${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.rejected",variant:"enU_L8",error:String(__recapBghError)});throw __recapBghError}):(${diag}({hook:"__recapBotguardHandlerDiagHook",phase:"handler.return",variant:"enU_L8",result:__recapBghRet,ib:__recapBghRet&&__recapBghRet.ib}),__recapBghRet)}catch(__recapBghWrapError){return __recapBghRet}}`
    }
  ];
  for (const item of replacements) {
    if (source.includes(item.needle)) return source.replace(item.needle, item.replacement);
  }
  return source;
}

function patchRecaptchaBotguardCoreSource(source) {
  if (typeof source !== 'string' || source.includes('__recapBotguardCoreDiagHook')) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const needle = 'return T[t[1]](g[29](t[2],m[t[2]],m[2],(f=(E=h.L)==m[t[2]]?void 0:E.L)!=m[t[2]]?f:null,A,x),w)}))';
  if (!source.includes(needle)) return source;
  const replacement = `var __recapBgCoreArg=(f=(E=h.L)==m[t[2]]?void 0:E.L)!=m[t[2]]?f:null;${diag}({hook:"__recapBotguardCoreDiagHook",phase:"before.g29",h:h,x:x,A:A,coreArg:__recapBgCoreArg});var __recapBgCoreRet=g[29](t[2],m[t[2]],m[2],__recapBgCoreArg,A,x);${diag}({hook:"__recapBotguardCoreDiagHook",phase:"after.g29",result:__recapBgCoreRet});return T[t[1]](__recapBgCoreRet,w)}))`;
  return source.replace(needle, replacement);
}

function patchRecaptchaStringDecoderSource(source) {
  if (typeof source !== 'string' || source.includes('__recapZ24StringDiagHook')) return source;
  const needle = 'U=g[p[0]](p[2],D[0],D[2],EL().slice(g[17](53,3122)[w],g[17](53,7348)[w+r]),g[17](48,2781)+e[p[1]](66,D[p[2]],$F,function(){return EL().slice(0,g[17](49,9699)[w])}))';
  if (!source.includes(needle)) return source;
  const record = 'function(){try{if(!(r===1&&typeof w==="number"&&w>=0&&w<120))return;var __recapZRoot=(typeof window!=="undefined"&&window)||(typeof self!=="undefined"&&self)||(typeof iN!=="undefined"&&iN)||(typeof globalThis!=="undefined"&&globalThis)||this,__recapZItem={kind:"sdk.z24",hook:"__recapZ24StringDiagHook",q:q,r:r,w:w,value:U,href:__recapZRoot.location&&__recapZRoot.location.href||""},__recapZArr=__recapZRoot.__recapZ24DiagItems;if(!__recapZArr){__recapZArr=[];Object.defineProperty(__recapZRoot,"__recapZ24DiagItems",{configurable:true,writable:true,enumerable:false,value:__recapZArr})}__recapZArr.push(__recapZItem);if(__recapZArr.length>3000)__recapZArr.shift();try{if(__recapZRoot.top&&__recapZRoot.top!==__recapZRoot)__recapZRoot.top.postMessage(JSON.stringify({__recapFieldDiag:true,item:__recapZItem}),"*");else if(typeof __recapZRoot.document==="undefined"&&typeof __recapZRoot.postMessage==="function")__recapZRoot.postMessage({__recapFieldDiag:true,item:__recapZItem})}catch(__recapZPostError){}}catch(__recapZDiagError){}}';
  return source.replace(needle, `${needle},(${record})()`);
}

function patchRecaptchaUGenericTaskSource(source) {
  if (typeof source !== 'string' || source.includes('__recapUTaskInnerDiagHook')) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  let out = source;
  const applyNeedle = 'G=S[35](26,"",T[1],T[2],3,d,function(){return r.apply(v,U)})';
  const applyReplacement = `G=S[35](26,"",T[1],T[2],3,d,function(){var __recapInnerOrigArgs=U&&U.slice?U.slice():U,__recapInnerResult,__recapInnerDiag=${diag};try{if(U&&U.length){for(var __recapArgI=0;__recapArgI<U.length;__recapArgI++){(function(__recapHelperIndex,__recapOrigHelper){if(typeof __recapOrigHelper!=="function")return;U[__recapHelperIndex]=function(){var __recapHelperArgs=Array.prototype.slice.call(arguments,0),__recapHelperResult=__recapOrigHelper.apply(this,arguments),__recapHelperPath=[],__recapDecodedPath=[];try{if(__recapHelperIndex===2&&__recapHelperArgs&&__recapHelperArgs.length>1){for(var __recapDecodeI=1;__recapDecodeI<__recapHelperArgs.length;__recapDecodeI++){try{var __recapDecodeArg=__recapHelperArgs[__recapDecodeI];__recapDecodedPath.push(typeof __recapDecodeArg==="number"?z[24](2,1,__recapDecodeArg):__recapDecodeArg)}catch(__recapDecodeOneError){(function(){var __recapDecodeErrItem={error:String(__recapDecodeOneError),input:__recapHelperArgs[__recapDecodeI]};try{__recapDecodeErrItem.fType=typeof $F;__recapDecodeErrItem.fString=String($F).slice(0,80)}catch(__recapDecodeFErr){}try{__recapDecodeErrItem.elType=typeof EL;var __recapElValue=EL();__recapDecodeErrItem.elLen=__recapElValue&&__recapElValue.length;__recapDecodeErrItem.elPrefix=String(__recapElValue).slice(0,40)}catch(__recapDecodeELErr){__recapDecodeErrItem.elError=String(__recapDecodeELErr)}try{var __recapDecodeNum=__recapHelperArgs[__recapDecodeI],__recapG3122=g[17](53,3122),__recapG7348=g[17](53,7348),__recapG9699=g[17](49,9699),__recapG2781=g[17](48,2781);__recapDecodeErrItem.g3122=String(__recapG3122).slice(0,90);__recapDecodeErrItem.g7348=String(__recapG7348).slice(0,90);__recapDecodeErrItem.g9699=String(__recapG9699).slice(0,90);__recapDecodeErrItem.g2781=String(__recapG2781).slice(0,90);__recapDecodeErrItem.start=__recapG3122&&__recapG3122[__recapDecodeNum];__recapDecodeErrItem.end=__recapG7348&&__recapG7348[__recapDecodeNum+1];__recapDecodeErrItem.preEnd=__recapG9699&&__recapG9699[__recapDecodeNum]}catch(__recapDecodeGErr){__recapDecodeErrItem.gError=String(__recapDecodeGErr)}try{if(typeof e!=="undefined"&&e[4]){__recapDecodeErrItem.e4=String(e[4](66,0,$F,function(){return String(EL()).slice(0,(__recapDecodeErrItem.preEnd||0))})).slice(0,120)}}catch(__recapDecodeE4Err){__recapDecodeErrItem.e4Error=String(__recapDecodeE4Err)}__recapDecodedPath.push(__recapDecodeErrItem)})()}}}}catch(__recapDecodePathError){}try{if(__recapHelperIndex===2&&__recapHelperArgs.length>1&&typeof Proxy==="function"){var __recapProxy;__recapProxy=new Proxy({},{get:function(__recapTarget,__recapProp){try{__recapHelperPath.push("get:"+String(__recapProp))}catch(__recapPathError){}return __recapProxy},has:function(__recapTarget,__recapProp){try{__recapHelperPath.push("has:"+String(__recapProp))}catch(__recapHasPathError){}return false},getOwnPropertyDescriptor:function(__recapTarget,__recapProp){try{__recapHelperPath.push("desc:"+String(__recapProp))}catch(__recapDescPathError){}return void 0},ownKeys:function(){try{__recapHelperPath.push("ownKeys")}catch(__recapOwnKeysPathError){}return []}});try{__recapOrigHelper.apply(this,[__recapProxy].concat(__recapHelperArgs.slice(1)))}catch(__recapPathProbeError){}}}catch(__recapPathOuterError){}try{__recapInnerDiag({hook:"__recapUTaskInnerDiagHook",phase:"helper.call",K:K,argIndex:__recapHelperIndex,args:__recapHelperArgs,result:__recapHelperResult,path:__recapHelperPath,decodedPath:__recapDecodedPath})}catch(__recapHelperDiagError){}return __recapHelperResult}})(__recapArgI,U[__recapArgI])}}}catch(__recapWrapHelperError){}try{__recapInnerResult=r.apply(v,U)}finally{try{if(__recapInnerOrigArgs&&__recapInnerOrigArgs.slice)U=__recapInnerOrigArgs}catch(__recapRestoreArgsError){}}try{__recapInnerDiag({hook:"__recapUTaskInnerDiagHook",phase:"r.apply",K:K,r:r,D:D,d:d,w:w,args:U,result:__recapInnerResult})}catch(__recapInnerDiagError){}return __recapInnerResult})`;
  out = out.includes(applyNeedle) ? out.replace(applyNeedle, applyReplacement) : out;
  const outputNeedle = 'h=a.P,f=h.HY,B=h.VI,g[12](32,T[t[2]],E,B)';
  const outputReplacement = `h=a.P,f=h.HY,B=h.VI,${diag}({hook:"__recapUTaskInnerDiagHook",phase:"after.S35",K:K,r:r,D:D,d:d,w:w,args:U,HY:f,VI:B,E:E,G:G}),g[12](32,T[t[2]],E,B)`;
  out = out.includes(outputNeedle) ? out.replace(outputNeedle, outputReplacement) : out;
  return out;
}

function patchRecaptchaHelperPathSource(source) {
  if (typeof source !== 'string' || source.includes('__recapHelperPathDiagHook')) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const needle = 'var A=typeof d==N[D=79,1]?z[24](2,N[2],d):d,x=J[29](33,N[0],q,A);w=82';
  const replacement = `var A=typeof d==N[D=79,1]?z[24](2,N[2],d):d,__recapHelperTask=null,__recapHelperBaseType=typeof q,__recapHelperBaseCtor="";try{__recapHelperTask=((typeof window!=="undefined"&&window.__recapCurrentUTask)||(typeof window!=="undefined"&&window.top&&window.top.__recapCurrentUTask)||(typeof self!=="undefined"&&self.__recapCurrentUTask)||null)}catch(__recapHelperTaskError){}try{__recapHelperBaseCtor=q&&q.constructor&&q.constructor.name||""}catch(__recapHelperCtorError){}try{var __recapHelperRoot=(typeof window!=="undefined"&&window)||(typeof self!=="undefined"&&self)||(typeof iN!=="undefined"&&iN)||(typeof globalThis!=="undefined"&&globalThis)||this,__recapHelperArr=__recapHelperRoot.__recapHelperPathKeys;if(!__recapHelperArr){__recapHelperArr=[];Object.defineProperty(__recapHelperRoot,"__recapHelperPathKeys",{configurable:true,writable:true,enumerable:false,value:__recapHelperArr})}__recapHelperArr.push({input:d,key:A,baseType:__recapHelperBaseType,baseCtor:__recapHelperBaseCtor,task:__recapHelperTask});if(__recapHelperArr.length>1500)__recapHelperArr.shift()}catch(__recapHelperStoreError){}try{${diag}({hook:"__recapHelperPathDiagHook",phase:"resolve",task:__recapHelperTask,input:d,key:A,baseType:__recapHelperBaseType,baseCtor:__recapHelperBaseCtor})}catch(__recapHelperPathDiagError){}var x=J[29](33,N[0],q,A);w=82`;
  return source.includes(needle) ? source.replace(needle, replacement) : source;
}

function patchRecaptchaELSetupSource(source) {
  if (typeof source !== 'string' || source.includes('__recapELSetupDiagHook')) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const needle = 'EL=function(){return e[4](65,r,$F,function(){return D.slice(w)})},A=D';
  const replacement = `EL=function(){return e[4](65,r,$F,function(){return D.slice(w)})},function(){try{var __recapDString;try{__recapDString=String(D)}catch(__recapDStringError){__recapDString=""}var __recapSlice="";try{__recapSlice=D&&D.slice?D.slice(w):""}catch(__recapSliceError){__recapSlice={error:String(__recapSliceError)}}var __recapELValue;try{__recapELValue=EL()}catch(__recapELError){__recapELValue={error:String(__recapELError)}}${diag}({hook:"__recapELSetupDiagHook",phase:"assign",q:q,r:r,w:w,DType:typeof D,DLen:D&&D.length,DStringLen:__recapDString.length,DPrefix:__recapDString.slice(0,80),DSuffix:__recapDString.slice(-80),sliceType:typeof __recapSlice,sliceLen:__recapSlice&&__recapSlice.length,slicePrefix:String(__recapSlice).slice(0,80),sliceSuffix:String(__recapSlice).slice(-80),ELType:typeof __recapELValue,ELLen:__recapELValue&&__recapELValue.length,ELPrefix:String(__recapELValue).slice(0,80),ELSuffix:String(__recapELValue).slice(-80),FType:typeof $F,FString:String($F).slice(0,80)})}catch(__recapELSetupError){try{${diag}({hook:"__recapELSetupDiagHook",phase:"assign.error",error:String(__recapELSetupError)})}catch(__recapELSetupError2){}}}(),A=D`;
  return source.includes(needle) ? source.replace(needle, replacement) : source;
}

function patchRecaptchaG43CssStringTableSource(source) {
  if (typeof source !== 'string' || source.includes('__recapG43CssDiagHook')) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const needle = 'D=S[23](5,e[15](x[2],Od),FZ),N=JM(function(){return D.match(/[^,]*,([\\w\\d\\+\\/]*)/)[w]},\r\nr)';
  const replacement = `D=S[23](5,e[15](x[2],Od),FZ),function(){try{var __recapG43El=null,__recapG43CS=null,__recapG43Value=D;try{__recapG43El=e[15](x[2],Od)}catch(__recapG43ElError){}try{if(__recapG43El&&__recapG43El.ownerDocument&&__recapG43El.ownerDocument.defaultView)__recapG43CS=__recapG43El.ownerDocument.defaultView.getComputedStyle(__recapG43El,null)}catch(__recapG43CSError){}${diag}({hook:"__recapG43CssDiagHook",phase:"cssStringTable",q:q,r:r,w:w,Od:Od,FZ:FZ,elementTag:__recapG43El&&__recapG43El.tagName,elementClass:__recapG43El&&__recapG43El.className,elementText:String(__recapG43El&&(__recapG43El.textContent||"")).slice(0,80),computedLen:__recapG43Value&&__recapG43Value.length,computedPrefix:String(__recapG43Value||"").slice(0,160),computedSuffix:String(__recapG43Value||"").slice(-120),cssTextPrefix:__recapG43CS&&String(__recapG43CS.cssText||"").slice(0,160),styleKeys:__recapG43CS&&Object.keys(__recapG43CS).slice(0,20)})}catch(__recapG43DiagError){try{${diag}({hook:"__recapG43CssDiagHook",phase:"cssStringTable.error",error:String(__recapG43DiagError)})}catch(__recapG43DiagError2){}}}(),N=JM(function(){return D.match(/[^,]*,([\\w\\d\\+\\/]*)/)[w]},\r\nr)`;
  return source.includes(needle) ? source.replace(needle, replacement) : source;
}

function patchRecaptchaUProducerSource(source) {
  if (typeof source !== 'string' || source.includes('__recapUProducerDiagHook')) return source;
  const needle = 'G=G.then(function(Ap){return function(tp){return e[32](31,v[Ap.R$],U[Ap.R$]).call(A,tp,p,Ap.R$)}}(B)).then(f);';
  if (!source.includes(needle)) return source;
  const diag = '((typeof iN!=="undefined"&&iN.__recapReloadBuilderDiag)||(typeof self!=="undefined"&&self.__recapReloadBuilderDiag)||function(){})';
  const replacement = `G=G.then(function(Ap){return function(tp){var __recapUIdx=Ap.R$,__recapUFn=v[__recapUIdx],__recapUMeta=U[__recapUIdx],__recapURoot=(typeof window!=="undefined"&&window)||(typeof self!=="undefined"&&self)||(typeof iN!=="undefined"&&iN)||this,__recapUTaskObj={idx:__recapUIdx,meta:__recapUMeta};try{Object.defineProperty(__recapURoot,"__recapCurrentUTask",{configurable:true,writable:true,value:__recapUTaskObj})}catch(__recapUSetError){try{__recapURoot.__recapCurrentUTask=__recapUTaskObj}catch(__recapUSetError2){}}try{if(__recapURoot.top&&__recapURoot.top!==__recapURoot)Object.defineProperty(__recapURoot.top,"__recapCurrentUTask",{configurable:true,writable:true,value:__recapUTaskObj})}catch(__recapUTopSetError){try{if(__recapURoot.top)__recapURoot.top.__recapCurrentUTask=__recapUTaskObj}catch(__recapUTopSetError2){}}var __recapUClear=function(){try{__recapURoot.__recapCurrentUTask=null}catch(__recapUClearError){}try{if(__recapURoot.top&&__recapURoot.top!==__recapURoot)__recapURoot.top.__recapCurrentUTask=null}catch(__recapUTopClearError){}};var __recapUTaskFn=e[32](31,__recapUFn,__recapUMeta);${diag}({hook:"__recapUProducerDiagHook",phase:"task.before",idx:__recapUIdx,fn:__recapUFn,resolvedFn:__recapUTaskFn,meta:__recapUMeta,input:tp,p:p});var __recapURet=__recapUTaskFn.call(A,tp,p,__recapUIdx);try{if(__recapURet&&typeof __recapURet.then==="function")return __recapURet.then(function(__recapUResolved){try{if(__recapUResolved&&typeof __recapUResolved.Ei==="function"&&!__recapUResolved.__recapEiWriteWrapped){var __recapOrigEi=__recapUResolved.Ei;Object.defineProperty(__recapUResolved,"__recapEiWriteWrapped",{value:true,configurable:true});__recapUResolved.Ei=function(__recapEiArr){var __recapBeforeKeys=[];try{__recapBeforeKeys=Object.keys(__recapEiArr||{})}catch(__recapEiBeforeError){}var __recapEiRet=__recapOrigEi.apply(this,arguments);var __recapAfterKeys=[];try{__recapAfterKeys=Object.keys(__recapEiArr||{})}catch(__recapEiAfterError){}var __recapChanged=[];try{for(var __recapKi=0;__recapKi<__recapAfterKeys.length;__recapKi++){var __recapK=__recapAfterKeys[__recapKi];if(__recapBeforeKeys.indexOf(__recapK)<0)__recapChanged.push([__recapK,__recapEiArr[__recapK]])}}catch(__recapEiChangedError){}${diag}({hook:"__recapUProducerDiagHook",phase:"task.ei.write",idx:__recapUIdx,changed:__recapChanged,arrayLength:__recapEiArr&&__recapEiArr.length});return __recapEiRet}}}catch(__recapWrapEiError){${diag}({hook:"__recapUProducerDiagHook",phase:"task.ei.wrap.error",idx:__recapUIdx,error:String(__recapWrapEiError)})}${diag}({hook:"__recapUProducerDiagHook",phase:"task.resolved",idx:__recapUIdx,result:__recapUResolved});__recapUClear();return __recapUResolved},function(__recapUErr){${diag}({hook:"__recapUProducerDiagHook",phase:"task.rejected",idx:__recapUIdx,error:String(__recapUErr)});__recapUClear();throw __recapUErr});${diag}({hook:"__recapUProducerDiagHook",phase:"task.return",idx:__recapUIdx,result:__recapURet});__recapUClear();}catch(__recapUWrapError){__recapUClear()}return __recapURet}}(B)).then(function(Ap){return function(__recapUPost){try{var __recapUOut=f(__recapUPost);${diag}({hook:"__recapUProducerDiagHook",phase:"task.output",idx:Ap.R$,output:__recapUOut});return __recapUOut}catch(__recapUOutError){${diag}({hook:"__recapUProducerDiagHook",phase:"task.output.error",idx:Ap.R$,error:String(__recapUOutError)});throw __recapUOutError}}}(B));`;
  return source.replace(needle, replacement);
}

function recaptchaSdkPatchStatusPrelude(statusItems) {
  const safeStatus = JSON.stringify(Array.isArray(statusItems) ? statusItems : []);
  return `
;(function(){try{
  var __recapPatchWin=(typeof window!=="undefined"&&window)||(typeof iN!=="undefined"&&iN)||(typeof self!=="undefined"&&self)||this;
  function __recapPatchHiddenSlot(__recapRoot,__recapName,__recapFallback){
    var __recapValue;
    try{__recapValue=__recapRoot&&__recapRoot[__recapName];if(!__recapValue)__recapValue=__recapFallback;Object.defineProperty(__recapRoot,__recapName,{configurable:true,writable:true,enumerable:false,value:__recapValue});return __recapValue;}catch(__recapPatchHiddenError){try{__recapRoot[__recapName]=__recapValue||__recapFallback;return __recapRoot[__recapName];}catch(__recapPatchHiddenError2){return __recapFallback;}}
  }
  function __recapPatchPost(__recapPatchPayload){
    try{
      if(__recapPatchWin.top&&__recapPatchWin.top!==__recapPatchWin)__recapPatchWin.top.postMessage(JSON.stringify(__recapPatchPayload),"*");
      else if(typeof __recapPatchWin.document==="undefined"&&typeof __recapPatchWin.postMessage==="function")__recapPatchWin.postMessage(__recapPatchPayload);
    }catch(__recapPatchPostError){}
  }
  var __recapPatchArr=__recapPatchHiddenSlot(__recapPatchWin,"__recapFieldDiagItems",[]);
  __recapPatchArr.push({kind:"sdk.patchStatus",t:Date.now(),href:__recapPatchWin.location&&__recapPatchWin.location.href||"",patches:${safeStatus}});
  if(__recapPatchArr.length>300)__recapPatchArr.shift();
  __recapPatchPost({__recapFieldDiag:true,item:__recapPatchArr[__recapPatchArr.length-1]});
}catch(__recapPatchStatusError){}
})();
`;
}

function recaptchaSdkBtoaDiagnosticPrelude() {
  return `
;(function(){try{
  var __recapBtoaWin=(typeof window!=="undefined"&&window)||(typeof iN!=="undefined"&&iN)||this;
  function __recapHiddenSlot(__recapRoot,__recapName,__recapFallback){
    var __recapValue;
    try{__recapValue=__recapRoot&&__recapRoot[__recapName];if(!__recapValue)__recapValue=__recapFallback;Object.defineProperty(__recapRoot,__recapName,{configurable:true,writable:true,enumerable:false,value:__recapValue});return __recapValue;}catch(__recapHiddenError){try{__recapRoot[__recapName]=__recapValue||__recapFallback;return __recapRoot[__recapName];}catch(__recapHiddenError2){return __recapFallback;}}
  }
  function __recapDefineHidden(__recapRoot,__recapName,__recapValue){
    try{Object.defineProperty(__recapRoot,__recapName,{configurable:true,writable:true,enumerable:false,value:__recapValue});return __recapValue;}catch(__recapDefineHiddenError){try{__recapRoot[__recapName]=__recapValue;}catch(__recapDefineHiddenError2){}return __recapValue;}
  }
  function __recapPostDiag(__recapPayload){
    try{
      if(__recapBtoaWin.top&&__recapBtoaWin.top!==__recapBtoaWin)__recapBtoaWin.top.postMessage(JSON.stringify(__recapPayload),"*");
      else if(typeof __recapBtoaWin.document==="undefined"&&typeof __recapBtoaWin.postMessage==="function")__recapBtoaWin.postMessage(__recapPayload);
    }catch(__recapPostDiagError){}
  }
  if(__recapBtoaWin&&!__recapBtoaWin.__recapFieldDiag){
    __recapDefineHidden(__recapBtoaWin,"__recapFieldDiag",function(__recapField,__recapValue,__recapTypeHint){
      try{
        var __recapType=typeof __recapValue,__recapCtor="",__recapString="",__recapLength=0,__recapKeys=[];
        if(__recapValue!=null){
          try{__recapCtor=__recapValue.constructor&&__recapValue.constructor.name||"";}catch(__recapCtorError){}
          if(typeof __recapValue==="string"){
            __recapString=__recapValue;
            __recapLength=__recapString.length;
          }else if(typeof ArrayBuffer!=="undefined"&&ArrayBuffer.isView&&ArrayBuffer.isView(__recapValue)){
            __recapLength=__recapValue.byteLength;
          }else if(__recapValue instanceof ArrayBuffer){
            __recapLength=__recapValue.byteLength;
          }else if(typeof __recapValue.length==="number"){
            __recapLength=__recapValue.length;
          }else if(typeof __recapValue==="object"){
            try{__recapKeys=Object.keys(__recapValue).slice(0,24);}catch(__recapKeysError){}
            try{__recapString=__recapValue&&__recapValue.toString?String(__recapValue.toString()):"";}catch(__recapStringError){}
            __recapLength=__recapString.length||__recapKeys.length;
          }
        }
        if(__recapLength>=80||__recapField===16||__recapField===20||__recapField===22||__recapField===2){
          var __recapStack="";
          try{__recapStack=String((new Error).stack||"").split("\\n").slice(1,8).join("\\n");}catch(__recapStackError){}
          var __recapItem={kind:"sdk.field",t:Date.now(),href:__recapBtoaWin.location&&__recapBtoaWin.location.href||"",field:__recapField,typeHint:String(__recapTypeHint),valueType:__recapType,ctor:__recapCtor,length:__recapLength,prefix:String(__recapString||"").slice(0,180),suffix:String(__recapString||"").slice(-120),keys:__recapKeys,stack:__recapStack};
          var __recapArr=__recapHiddenSlot(__recapBtoaWin,"__recapFieldDiagItems",[]);
          __recapArr.push(__recapItem);
          if(__recapArr.length>300)__recapArr.shift();
          __recapPostDiag({__recapFieldDiag:true,item:__recapItem});
        }
      }catch(__recapFieldError){}
    });
  }
  if(__recapBtoaWin&&!__recapBtoaWin.__recapReloadBuilderDiag){
    __recapDefineHidden(__recapBtoaWin,"__recapReloadBuilderDiag",function(__recapPayload){
      try{
        function __recapClip(__recapValue,__recapMax){
          __recapValue=String(__recapValue==null?"":__recapValue);
          __recapMax=__recapMax||180;
          return __recapValue.length>__recapMax?__recapValue.slice(0,__recapMax)+"...<"+__recapValue.length+">":__recapValue;
        }
        function __recapSummary(__recapValue,__recapDepth){
          var __recapOut={type:typeof __recapValue},__recapI,__recapKeys,__recapName;
          __recapDepth=__recapDepth||0;
          try{
            if(__recapValue==null){__recapOut.value=__recapValue;return __recapOut;}
            try{__recapOut.ctor=__recapValue.constructor&&__recapValue.constructor.name||"";}catch(__recapCtorError){}
            if(typeof __recapValue==="string"){
              __recapOut.length=__recapValue.length;
              __recapOut.prefix=__recapValue.slice(0,220);
              __recapOut.suffix=__recapValue.slice(-160);
            }else if(typeof ArrayBuffer!=="undefined"&&ArrayBuffer.isView&&ArrayBuffer.isView(__recapValue)){
              __recapOut.length=__recapValue.length;
              __recapOut.byteLength=__recapValue.byteLength;
              __recapOut.prefix=Array.prototype.slice.call(__recapValue,0,32);
            }else if(__recapValue instanceof ArrayBuffer){
              __recapOut.ctor="ArrayBuffer";
              __recapOut.byteLength=__recapValue.byteLength;
            }else if(Array.isArray(__recapValue)){
              __recapOut.length=__recapValue.length;
              try{
                var __recapNumericArray=__recapValue.length>0;
                for(__recapI=0;__recapI<__recapValue.length&&__recapI<64;__recapI++){
                  if(typeof __recapValue[__recapI]!=="number"){__recapNumericArray=false;break;}
                }
                if(__recapNumericArray){
                  __recapOut.byteArray=true;
                  __recapOut.asciiPrefix="";
                  __recapOut.asciiSuffix="";
                  if(__recapValue.length<=6000)__recapOut.asciiFull="";
                  for(__recapI=0;__recapI<__recapValue.length&&__recapI<260;__recapI++){
                    var __recapC=__recapValue[__recapI]&255;
                    __recapOut.asciiPrefix+=__recapC>=32&&__recapC<127?String.fromCharCode(__recapC):".";
                  }
                  for(__recapI=Math.max(0,__recapValue.length-200);__recapI<__recapValue.length;__recapI++){
                    var __recapS=__recapValue[__recapI]&255;
                    __recapOut.asciiSuffix+=__recapS>=32&&__recapS<127?String.fromCharCode(__recapS):".";
                  }
                  if(__recapOut.asciiFull!==void 0){
                    for(__recapI=0;__recapI<__recapValue.length;__recapI++){
                      var __recapF=__recapValue[__recapI]&255;
                      __recapOut.asciiFull+=__recapF>=32&&__recapF<127?String.fromCharCode(__recapF):".";
                    }
                  }
                }
              }catch(__recapByteArrayError){}
              if(__recapDepth<3){
                __recapOut.items=[];
                for(__recapI=0;__recapI<__recapValue.length&&__recapI<120;__recapI++)__recapOut.items.push(__recapSummary(__recapValue[__recapI],__recapDepth+1));
              }
              try{__recapOut.string=__recapClip(__recapValue.toString(),260);}catch(__recapArrStringError){}
            }else if(typeof __recapValue==="function"){
              try{__recapOut.name=__recapValue.name||"";}catch(__recapFnNameError){}
              try{__recapOut.length=__recapValue.length;}catch(__recapFnLengthError){}
              try{
                var __recapFnSource=Function.prototype.toString.call(__recapValue);
                __recapOut.sourceLength=__recapFnSource.length;
                __recapOut.sourcePrefix=__recapFnSource.slice(0,4000);
                __recapOut.sourceSuffix=__recapFnSource.slice(-1200);
                if(__recapFnSource.length<=10000)__recapOut.sourceFull=__recapFnSource;
              }catch(__recapFnSourceError){__recapOut.sourceError=String(__recapFnSourceError);}
            }else if(typeof __recapValue==="object"){
              try{__recapKeys=Object.getOwnPropertyNames(__recapValue).slice(0,24);__recapOut.keys=__recapKeys;}catch(__recapKeysError){__recapKeys=[];}
              try{__recapOut.string=__recapClip(__recapValue&&__recapValue.toString?__recapValue.toString():Object.prototype.toString.call(__recapValue),260);}catch(__recapObjStringError){}
              if(__recapDepth<3&&__recapKeys&&__recapKeys.length){
                __recapOut.props={};
                for(__recapI=0;__recapI<__recapKeys.length&&__recapI<16;__recapI++){
                  __recapName=__recapKeys[__recapI];
                  try{__recapOut.props[__recapName]=__recapSummary(__recapValue[__recapName],__recapDepth+1);}catch(__recapPropError){__recapOut.props[__recapName]={error:String(__recapPropError)};}
                }
              }
            }else{
              __recapOut.value=__recapClip(__recapValue,220);
            }
          }catch(__recapSummaryError){__recapOut.error=String(__recapSummaryError);}
          return __recapOut;
        }
        var __recapStack="";
        try{__recapStack=String((new Error).stack||"").split("\\n").slice(1,8).join("\\n");}catch(__recapStackError){}
        var __recapItem={kind:"sdk.reloadBuilder",t:Date.now(),href:__recapBtoaWin.location&&__recapBtoaWin.location.href||"",payload:__recapSummary(__recapPayload,0),stack:__recapStack};
        var __recapArr=__recapHiddenSlot(__recapBtoaWin,"__recapFieldDiagItems",[]);
        __recapArr.push(__recapItem);
        if(__recapArr.length>300)__recapArr.shift();
        __recapPostDiag({__recapFieldDiag:true,item:__recapItem});
      }catch(__recapReloadBuilderError){}
    });
  }
  if(__recapBtoaWin&&!__recapBtoaWin.__recapExtensionDiag){
    __recapDefineHidden(__recapBtoaWin,"__recapExtensionDiag",function(__recapExtKey,__recapExtMeta,__recapChunks){
      try{
        function __recapValueSummary(__recapValue){
          var __recapType=typeof __recapValue,__recapCtor="",__recapString="",__recapLength=0,__recapKeys=[];
          try{if(__recapValue!=null)__recapCtor=__recapValue.constructor&&__recapValue.constructor.name||"";}catch(__recapCtorError){}
          try{
            if(typeof __recapValue==="string"){__recapString=__recapValue;__recapLength=__recapValue.length;}
            else if(__recapValue&&typeof __recapValue.length==="number"){__recapLength=__recapValue.length;}
            else if(__recapValue&&typeof __recapValue==="object"){__recapKeys=Object.keys(__recapValue).slice(0,12);__recapString=__recapValue.toString?String(__recapValue.toString()):"";__recapLength=__recapString.length||__recapKeys.length;}
          }catch(__recapSummaryError){}
          return {type:__recapType,ctor:__recapCtor,length:__recapLength,prefix:String(__recapString||"").slice(0,80),keys:__recapKeys};
        }
        function __recapByteAt(__recapChunk,__recapIndex){
          if(!__recapChunk)return 0;
          if(typeof __recapChunk==="string")return __recapChunk.charCodeAt(__recapIndex)&255;
          return __recapChunk[__recapIndex]&255;
        }
        function __recapChunkLength(__recapChunk){
          if(!__recapChunk)return 0;
          if(typeof __recapChunk.byteLength==="number")return __recapChunk.byteLength;
          if(typeof __recapChunk.length==="number")return __recapChunk.length;
          return 0;
        }
        function __recapReadVarint(__recapChunk,__recapPos,__recapEnd){
          var __recapVal=0,__recapShift=0,__recapStart=__recapPos;
          for(;__recapPos<__recapEnd;__recapPos++){
            var __recapB=__recapByteAt(__recapChunk,__recapPos);
            __recapVal|=(__recapB&127)<<__recapShift;
            if(!(__recapB&128))return {value:__recapVal,pos:__recapPos+1,start:__recapStart};
            __recapShift+=7;
          }
          return {value:0,pos:__recapPos,start:__recapStart,error:"eof"};
        }
        function __recapAscii(__recapChunk,__recapStart,__recapLen){
          var __recapOut="",__recapEnd=Math.min(__recapStart+__recapLen,__recapStart+96,__recapChunkLength(__recapChunk));
          for(var __recapI=__recapStart;__recapI<__recapEnd;__recapI++){
            var __recapC=__recapByteAt(__recapChunk,__recapI);
            __recapOut+=__recapC>=32&&__recapC<127?String.fromCharCode(__recapC):".";
          }
          return __recapOut;
        }
        var __recapChunkSummaries=[];
        var __recapCount=__recapChunks&&typeof __recapChunks.length==="number"?__recapChunks.length:0;
        for(var __recapIdx=0;__recapIdx<__recapCount&&__recapIdx<40;__recapIdx++){
          var __recapChunk=__recapChunks[__recapIdx],__recapLen=__recapChunkLength(__recapChunk),__recapKey=__recapReadVarint(__recapChunk,0,__recapLen),__recapField=__recapKey.value>>>3,__recapWire=__recapKey.value&7,__recapValueLen=null,__recapValueStart=__recapKey.pos;
          if(__recapWire===2){
            var __recapL=__recapReadVarint(__recapChunk,__recapKey.pos,__recapLen);
            __recapValueLen=__recapL.value;
            __recapValueStart=__recapL.pos;
          }
          __recapChunkSummaries.push({index:__recapIdx,length:__recapLen,field:__recapField,wire:__recapWire,valueLength:__recapValueLen,valuePrefix:__recapAscii(__recapChunk,__recapValueStart,__recapValueLen||Math.min(96,__recapLen-__recapValueStart))});
        }
        if(__recapChunkSummaries.length){
          var __recapStack="";
          try{__recapStack=String((new Error).stack||"").split("\\n").slice(1,8).join("\\n");}catch(__recapStackError){}
          var __recapItem={kind:"sdk.extension",t:Date.now(),href:__recapBtoaWin.location&&__recapBtoaWin.location.href||"",extKey:__recapValueSummary(__recapExtKey),extMeta:__recapValueSummary(__recapExtMeta),chunkCount:__recapCount,chunks:__recapChunkSummaries,stack:__recapStack};
          var __recapArr=__recapHiddenSlot(__recapBtoaWin,"__recapFieldDiagItems",[]);
          __recapArr.push(__recapItem);
          if(__recapArr.length>300)__recapArr.shift();
          __recapPostDiag({__recapFieldDiag:true,item:__recapItem});
        }
      }catch(__recapExtensionError){}
    });
  }
  if(__recapBtoaWin&&typeof __recapBtoaWin.btoa==="function"&&!__recapBtoaWin.btoa.__recapBtoaDiagWrapped){
    (function(__recapOrigBtoa){
      function __recapPreviewBinary(__recapInput){
        var __recapLen=0,__recapHex="",__recapAscii="",__recapType=typeof __recapInput;
        try{
          if(__recapInput!=null){
            if(typeof __recapInput==="string"){
              __recapLen=__recapInput.length;
              for(var __recapI=0;__recapI<__recapInput.length&&__recapI<96;__recapI++){
                var __recapC=__recapInput.charCodeAt(__recapI)&255;
                __recapHex+=(__recapC<16?"0":"")+__recapC.toString(16);
                __recapAscii+=__recapC>=32&&__recapC<127?String.fromCharCode(__recapC):".";
              }
            }else if(typeof __recapInput.length==="number"){
              __recapLen=__recapInput.length;
            }
          }
        }catch(__recapPreviewError){}
        return {inputType:__recapType,inputLength:__recapLen,inputHexPrefix:__recapHex,inputAsciiPrefix:__recapAscii};
      }
      var __recapWrappedBtoa=function recapBtoaDiag(__recapInput){
        var __recapOutput=__recapOrigBtoa.apply(this,arguments);
        try{
          var __recapArr=__recapHiddenSlot(__recapBtoaWin,"__recapBase64Diag",[]);
          var __recapPreview=__recapPreviewBinary(__recapInput);
          if(__recapPreview.inputLength>=80||String(__recapOutput||"").length>=80){
            var __recapStack="";
            try{__recapStack=String((new Error).stack||"").split("\\n").slice(1,7).join("\\n");}catch(__recapStackError){}
            var __recapItem={kind:"sdk.btoa",t:Date.now(),href:__recapBtoaWin.location&&__recapBtoaWin.location.href||"",inputType:__recapPreview.inputType,inputLength:__recapPreview.inputLength,outputLength:String(__recapOutput||"").length,outputPrefix:String(__recapOutput||"").slice(0,120),outputSuffix:String(__recapOutput||"").slice(-80),inputHexPrefix:__recapPreview.inputHexPrefix,inputAsciiPrefix:__recapPreview.inputAsciiPrefix,stack:__recapStack};
            __recapArr.push(__recapItem);
            __recapPostDiag({__recapBase64Diag:true,item:__recapItem});
            if(__recapArr.length>200)__recapArr.shift();
          }
        }catch(__recapDiagError){}
        return __recapOutput;
      };
      try{Object.defineProperty(__recapWrappedBtoa,"__recapBtoaDiagWrapped",{value:true});}catch(__recapFlagError){__recapWrappedBtoa.__recapBtoaDiagWrapped=true;}
      __recapBtoaWin.btoa=__recapWrappedBtoa;
    })(__recapBtoaWin.btoa);
  }
}catch(__recapBtoaInstallError){}
})();
`;
}

function recaptchaSdkApiTouchDiagnosticPrelude() {
  return `
;(function(){try{
  var __recapApiRoot=(typeof iN!=="undefined"&&iN)||(typeof self!=="undefined"&&self)||(typeof window!=="undefined"&&window)||this;
  if(__recapApiRoot&&!__recapApiRoot.__recapApiTouchDiagInstalled){
    try{Object.defineProperty(__recapApiRoot,"__recapApiTouchDiagInstalled",{value:true,configurable:true});}catch(__recapInstallFlagError){__recapApiRoot.__recapApiTouchDiagInstalled=true;}
    function __recapApiHiddenSlot(__recapRoot,__recapName,__recapFallback){
      var __recapValue;
      try{__recapValue=__recapRoot&&__recapRoot[__recapName];if(!__recapValue)__recapValue=__recapFallback;Object.defineProperty(__recapRoot,__recapName,{configurable:true,writable:true,enumerable:false,value:__recapValue});return __recapValue;}catch(__recapHiddenError){try{__recapRoot[__recapName]=__recapValue||__recapFallback;return __recapRoot[__recapName];}catch(__recapHiddenError2){return __recapFallback;}}
    }
    var __recapApiItems=__recapApiHiddenSlot(__recapApiRoot,"__recapApiTouchDiagItems",[]);
    var __recapApiCounts=__recapApiHiddenSlot(__recapApiRoot,"__recapApiTouchDiagCounts",Object.create(null));
    var __recapApiRecording=false;
    function __recapApiPost(__recapPayload){
      try{
        if(__recapApiRoot.top&&__recapApiRoot.top!==__recapApiRoot)__recapApiRoot.top.postMessage(JSON.stringify(__recapPayload),"*");
        else if(typeof __recapApiRoot.document==="undefined"&&typeof __recapApiRoot.postMessage==="function")__recapApiRoot.postMessage(__recapPayload);
      }catch(__recapApiPostError){}
    }
    function __recapApiArgSummary(__recapArgs){
      var __recapOut=[];
      try{
        for(var __recapI=0;__recapI<__recapArgs.length&&__recapI<4;__recapI++){
          var __recapV=__recapArgs[__recapI],__recapT=typeof __recapV;
          if(__recapV==null)__recapOut.push({type:__recapT,value:__recapV});
          else if(__recapT==="number"||__recapT==="boolean")__recapOut.push({type:__recapT,value:String(__recapV)});
          else if(__recapT==="string")__recapOut.push({type:__recapT,length:__recapV.length,prefix:__recapV.slice(0,80)});
          else if(typeof ArrayBuffer!=="undefined"&&ArrayBuffer.isView&&ArrayBuffer.isView(__recapV))__recapOut.push({type:__recapT,ctor:__recapV.constructor&&__recapV.constructor.name||"",length:__recapV.length,byteLength:__recapV.byteLength});
          else __recapOut.push({type:__recapT,ctor:__recapV.constructor&&__recapV.constructor.name||"",keys:Object.keys(Object(__recapV)).slice(0,8)});
        }
      }catch(__recapArgError){}
      return __recapOut;
    }
    function __recapApiCurrentTask(){
      try{
        var __recapRoots=[__recapApiRoot];
        try{if(typeof globalThis!=="undefined")__recapRoots.push(globalThis);}catch(__recapGlobalThisError){}
        try{if(typeof window!=="undefined")__recapRoots.push(window);}catch(__recapWindowError){}
        try{if(typeof window!=="undefined"&&window.top)__recapRoots.push(window.top);}catch(__recapTopError){}
        try{if(typeof self!=="undefined")__recapRoots.push(self);}catch(__recapSelfError){}
        try{if(typeof iN!=="undefined")__recapRoots.push(iN);}catch(__recapINError){}
        for(var __recapI=0;__recapI<__recapRoots.length;__recapI++){
          var __recapRootCandidate=__recapRoots[__recapI];
          if(__recapRootCandidate&&__recapRootCandidate.__recapCurrentUTask)return __recapRootCandidate.__recapCurrentUTask;
        }
      }catch(__recapTaskReadError){}
      return null;
    }
    function __recapApiRecord(__recapKind,__recapName,__recapExtra){
      try{
        if(__recapApiRecording)return;
        __recapApiRecording=true;
        var __recapKey=__recapKind+":"+__recapName;
        var __recapCount=(__recapApiCounts[__recapKey]||0)+1;
        __recapApiCounts[__recapKey]=__recapCount;
        var __recapTask=__recapApiCurrentTask();
        if(__recapCount>12&&__recapCount%50!==0&&!__recapTask){__recapApiRecording=false;return;}
        var __recapItem={kind:"sdk.apiTouch",touchKind:__recapKind,name:__recapName,count:__recapCount,t:Date.now(),href:__recapApiRoot.location&&__recapApiRoot.location.href||"",realm:typeof __recapApiRoot.document==="undefined"?"worker":"window"};
        if(__recapTask)__recapItem.uTask={idx:__recapTask.idx,meta:__recapTask.meta};
        if(__recapExtra)for(var __recapK in __recapExtra)__recapItem[__recapK]=__recapExtra[__recapK];
        if(__recapTask&&(__recapKind==="inspect"||__recapKind==="stringify"||__recapKind==="functionString")){
          try{__recapItem.stack=String((new Error).stack||"").split("\\n").slice(2,7).join("\\n");}catch(__recapStackError){}
        }
        __recapApiItems.push(__recapItem);
        if(__recapApiItems.length>2600)__recapApiItems.shift();
        __recapApiPost({__recapApiTouchDiag:true,item:__recapItem});
        __recapApiRecording=false;
      }catch(__recapApiRecordError){try{__recapApiRecording=false;}catch(__recapResetError){}}
    }
    function __recapWrapMethod(__recapObj,__recapLabel,__recapProp){
      try{
        if(!__recapObj||typeof __recapObj[__recapProp]!=="function"||__recapObj[__recapProp].__recapApiTouchWrapped)return;
        var __recapOrig=__recapObj[__recapProp];
        var __recapWrapped=function recapApiTouchMethod(){
          __recapApiRecord("method",__recapLabel+"."+__recapProp,{args:__recapApiArgSummary(arguments)});
          return __recapOrig.apply(this,arguments);
        };
        try{Object.defineProperty(__recapWrapped,"name",{configurable:true,value:__recapOrig.name||__recapProp});Object.defineProperty(__recapWrapped,"length",{configurable:true,value:__recapOrig.length});Object.defineProperty(__recapWrapped,"__recapApiTouchWrapped",{value:true});}catch(__recapWrapMetaError){}
        __recapObj[__recapProp]=__recapWrapped;
      }catch(__recapWrapMethodError){}
    }
    function __recapWrapGetter(__recapObj,__recapLabel,__recapProp){
      try{
        if(!__recapObj)return;
        var __recapDesc=Object.getOwnPropertyDescriptor(__recapObj,__recapProp);
        if(!__recapDesc||typeof __recapDesc.get!=="function"||__recapDesc.get.__recapApiTouchWrapped)return;
        var __recapOrigGet=__recapDesc.get,__recapOrigSet=__recapDesc.set;
        var __recapWrappedGet=function recapApiTouchGetter(){
          __recapApiRecord("getter",__recapLabel+"."+__recapProp,{});
          return __recapOrigGet.call(this);
        };
        try{Object.defineProperty(__recapWrappedGet,"name",{configurable:true,value:__recapOrigGet.name||("get "+__recapProp)});Object.defineProperty(__recapWrappedGet,"__recapApiTouchWrapped",{value:true});}catch(__recapGetterMetaError){}
        Object.defineProperty(__recapObj,__recapProp,{configurable:__recapDesc.configurable,enumerable:__recapDesc.enumerable,get:__recapWrappedGet,set:__recapOrigSet});
      }catch(__recapWrapGetterError){}
    }
    var __recapNavProto=typeof Navigator!=="undefined"&&Navigator.prototype;
    ["userAgent","appVersion","platform","language","languages","hardwareConcurrency","deviceMemory","maxTouchPoints","vendor","webdriver","cookieEnabled","pdfViewerEnabled","doNotTrack","plugins","mimeTypes","mediaDevices","storage","permissions","userAgentData","onLine"].forEach(function(p){__recapWrapGetter(__recapNavProto,"Navigator",p);});
    var __recapUADProto=typeof NavigatorUAData!=="undefined"&&NavigatorUAData.prototype;
    ["brands","mobile","platform"].forEach(function(p){__recapWrapGetter(__recapUADProto,"NavigatorUAData",p);});
    ["getHighEntropyValues","toJSON"].forEach(function(p){__recapWrapMethod(__recapUADProto,"NavigatorUAData",p);});
    var __recapPerfProto=typeof Performance!=="undefined"&&Performance.prototype;
    ["timeOrigin","memory"].forEach(function(p){__recapWrapGetter(__recapPerfProto,"Performance",p);});
    ["now","getEntries","getEntriesByType","getEntriesByName","mark","measure","clearMarks","clearMeasures"].forEach(function(p){__recapWrapMethod(__recapPerfProto,"Performance",p);});
    var __recapCryptoProto=typeof Crypto!=="undefined"&&Crypto.prototype;
    ["getRandomValues","randomUUID"].forEach(function(p){__recapWrapMethod(__recapCryptoProto,"Crypto",p);});
    ["subtle"].forEach(function(p){__recapWrapGetter(__recapCryptoProto,"Crypto",p);});
    var __recapIDBProto=typeof IDBFactory!=="undefined"&&IDBFactory.prototype;
    ["open","deleteDatabase","databases","cmp"].forEach(function(p){__recapWrapMethod(__recapIDBProto,"IDBFactory",p);});
    var __recapCacheProto=typeof CacheStorage!=="undefined"&&CacheStorage.prototype;
    ["open","keys","match","has","delete"].forEach(function(p){__recapWrapMethod(__recapCacheProto,"CacheStorage",p);});
    var __recapSchedulerProto=typeof Scheduler!=="undefined"&&Scheduler.prototype;
    ["postTask","yield"].forEach(function(p){__recapWrapMethod(__recapSchedulerProto,"Scheduler",p);});
    var __recapScreenProto=typeof Screen!=="undefined"&&Screen.prototype;
    ["width","height","availWidth","availHeight","colorDepth","pixelDepth","availLeft","availTop","orientation"].forEach(function(p){__recapWrapGetter(__recapScreenProto,"Screen",p);});
    var __recapCanvasProto=typeof HTMLCanvasElement!=="undefined"&&HTMLCanvasElement.prototype;
    ["getContext","toDataURL","toBlob"].forEach(function(p){__recapWrapMethod(__recapCanvasProto,"HTMLCanvasElement",p);});
    var __recapOffscreenProto=typeof OffscreenCanvas!=="undefined"&&OffscreenCanvas.prototype;
    ["getContext","convertToBlob","transferToImageBitmap"].forEach(function(p){__recapWrapMethod(__recapOffscreenProto,"OffscreenCanvas",p);});
    var __recapCtx2DProto=typeof CanvasRenderingContext2D!=="undefined"&&CanvasRenderingContext2D.prototype;
    ["fillText","strokeText","measureText","getImageData","putImageData","drawImage"].forEach(function(p){__recapWrapMethod(__recapCtx2DProto,"CanvasRenderingContext2D",p);});
    var __recapWebGLProto=typeof WebGLRenderingContext!=="undefined"&&WebGLRenderingContext.prototype;
    ["getParameter","getExtension","getSupportedExtensions","readPixels","getShaderPrecisionFormat"].forEach(function(p){__recapWrapMethod(__recapWebGLProto,"WebGLRenderingContext",p);});
    var __recapWebGL2Proto=typeof WebGL2RenderingContext!=="undefined"&&WebGL2RenderingContext.prototype;
    ["getParameter","getExtension","getSupportedExtensions","readPixels","getShaderPrecisionFormat"].forEach(function(p){__recapWrapMethod(__recapWebGL2Proto,"WebGL2RenderingContext",p);});
    var __recapDocProto=typeof Document!=="undefined"&&Document.prototype;
    ["createElement","createElementNS","getElementById","querySelector","querySelectorAll","hasStorageAccess"].forEach(function(p){__recapWrapMethod(__recapDocProto,"Document",p);});
    ["visibilityState","hidden","cookie","referrer","currentScript"].forEach(function(p){__recapWrapGetter(__recapDocProto,"Document",p);});
    // Keep Object/Reflect/JSON.stringify/Function.prototype.toString unwrapped
    // in this diagnostic prelude. Broad inspection wrappers perturb the SDK
    // enough to suppress the existing field diagnostic postMessage path.
    __recapApiRecord("install","sdk.apiTouch",{
      hasDocument:typeof __recapApiRoot.document!=="undefined",
      hasNavigator:typeof __recapApiRoot.navigator!=="undefined",
      hasOffscreenCanvas:typeof __recapApiRoot.OffscreenCanvas!=="undefined",
      hasIndexedDB:typeof __recapApiRoot.indexedDB!=="undefined"
    });
  }
}catch(__recapApiTouchInstallError){}
})();
`;
}

function patchRecaptchaSdkResource(resource, enabled) {
  if (!enabled || !resource || typeof resource.body !== 'string') return resource;
  const url = String(resource.url || '');
  if (!/recaptcha__[^/]+\.js(?:[?#].*)?$/.test(url)) return resource;
  let body = resource.body;
  const patchStatus = [];
  const applyPatch = (name, fn) => {
    const before = body;
    body = fn(body);
    patchStatus.push({ name, hit: body !== before });
  };
  applyPatch('base64Encoder', patchRecaptchaBase64EncoderSource);
  applyPatch('fieldWriter', patchRecaptchaFieldWriterSource);
  applyPatch('extensionWriter', patchRecaptchaExtensionWriterSource);
  applyPatch('reloadBuilder', patchRecaptchaReloadBuilderSource);
  applyPatch('fingerprintArray', patchRecaptchaFingerprintArraySource);
  applyPatch('CF', patchRecaptchaCFSource);
  applyPatch('field16Upstream', patchRecaptchaField16UpstreamSource);
  applyPatch('nERawInputs', patchRecaptchaNERawInputsSource);
  applyPatch('field16Encode', patchRecaptchaField16EncodeSource);
  applyPatch('FjSend', patchRecaptchaFjSendSource);
  applyPatch('BotguardHandler', patchRecaptchaBotguardHandlerSource);
  applyPatch('BotguardCore', patchRecaptchaBotguardCoreSource);
  applyPatch('stringDecoderZ24', patchRecaptchaStringDecoderSource);
  applyPatch('UGenericTask', patchRecaptchaUGenericTaskSource);
  applyPatch('helperPath', patchRecaptchaHelperPathSource);
  applyPatch('ELSetup', patchRecaptchaELSetupSource);
  applyPatch('G43CssStringTable', patchRecaptchaG43CssStringTableSource);
  applyPatch('UProducer', patchRecaptchaUProducerSource);
  body = recaptchaSdkPatchStatusPrelude(patchStatus) + body;
  if (!body.includes('__recapApiTouchDiagInstalled')) {
    body = recaptchaSdkApiTouchDiagnosticPrelude() + body;
  }
  if (!body.includes('__recapBtoaDiagWrapped')) {
    body = recaptchaSdkBtoaDiagnosticPrelude() + body;
  }
  if (body.includes('__recapDiagWrapped')) return Object.assign({}, resource, { body });
  const marker = '}).call(this);';
  const idx = body.lastIndexOf(marker);
  if (idx < 0) return resource;
  if (nodeProcess.env.RECAPTCHA_DUMP_PATCHED_SDK_DIR) {
    try {
      const dumpDir = path.resolve(nodeProcess.env.RECAPTCHA_DUMP_PATCHED_SDK_DIR);
      fs.mkdirSync(dumpDir, { recursive: true });
      const dumpName = 'recaptcha_patched_debug_' + nodeBuffer.from(url).toString('base64url').slice(0, 32) + '.js';
      fs.writeFileSync(path.join(dumpDir, dumpName), body, 'utf8');
    } catch (_) {
    }
  }
  return Object.assign({}, resource, {
    body: body.slice(0, idx) + recaptchaSdkDiagnosticPatchSource() + body.slice(idx)
  });
}











function installAnchorDefer(state) {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'src');
  if (!descriptor || typeof descriptor.set !== 'function') return;
  state.iframeSrcDescriptor = descriptor;
  Object.defineProperty(HTMLIFrameElement.prototype, 'src', {
    configurable: descriptor.configurable,
    enumerable: descriptor.enumerable,
    get: function recapDeferAnchorSrcGet() {
      if (this.__recapDeferredAnchorSrc) return this.__recapDeferredAnchorSrc;
      if (this.__recapDeferredBframeSrc) return this.__recapDeferredBframeSrc;
      return descriptor.get ? descriptor.get.call(this) : '';
    },
    set: function recapDeferAnchorSrcSet(value) {
      const absolute = new URL(String(value || ''), location.href).href;
      const isAnchor = absolute.includes('/recaptcha/api2/anchor?')
        || absolute.includes('/recaptcha/enterprise/anchor?');
      if (!state.anchorReady && isAnchor) {
        try { Object.defineProperty(this, '__recapDeferredAnchorSrc', { configurable: true, writable: true, enumerable: false, value: absolute }); } catch { this.__recapDeferredAnchorSrc = absolute; }
        state.deferredAnchor = { element: this, url: absolute };
        return;
      }
      if (
        !state.bframeReady
        && absolute.includes('/recaptcha/enterprise/bframe?')
      ) {
        try { Object.defineProperty(this, '__recapDeferredBframeSrc', { configurable: true, writable: true, enumerable: false, value: absolute }); } catch { this.__recapDeferredBframeSrc = absolute; }
        state.deferredBframe = { element: this, url: absolute };
        return;
      }
      return descriptor.set.call(this, value);
    }
  });
  state.anchorDeferInstalled = true;
}

















function isEnterpriseEndpoint(url, name) {
  try {
    return new URL(String(url || '')).pathname
      === '/recaptcha/enterprise/' + String(name || '');
  } catch {
    return false;
  }
}

function enterpriseRequestCount(state, name) {
  return state.liveRequests.filter((item) => (
    isEnterpriseEndpoint(item && item.url, name)
  )).length;
}

async function waitForEnterpriseTileReplacement(
  state, frame, beforeCount, beforeSnapshot, tileIndex, timeoutMs = 15000,
) {
  return waitForTileReplacement({
    state, beforeCount, beforeSnapshot, tileIndex,
    networkTimeoutMs: (state.networkTimeoutMs || 90000) + 500,
    settleTimeoutMs: timeoutMs,
  }, {
    requests: () => state.liveRequests.filter(item => isEnterpriseEndpoint(item && item.url, 'replaceimage')),
    snapshot: () => enterpriseChallengeSnapshot(frame, 0),
    token: () => enterpriseTokenValue(state),
    delay,
  });
}

async function waitForEnterpriseSubmissionProgress(
  state,
  frame,
  beforeSnapshot,
  beforeUserverifyCount,
  beforeReplaceimageCount,
  timeoutMs = 2500,
) {
  const deadline = Date.now() + Math.max(500, Number(timeoutMs || 2500));
  const beforeFingerprint = beforeSnapshot
    ? beforeSnapshot.fingerprint
    : '';
  while (Date.now() < deadline) {
    if (enterpriseTokenValue(state)) return true;
    if (state.userverifyRequests.length > beforeUserverifyCount) return true;
    if (
      enterpriseRequestCount(state, 'replaceimage')
      > beforeReplaceimageCount
    ) {
      return true;
    }
    const current = enterpriseChallengeSnapshot(frame, 0);
    if (
      current
      && beforeFingerprint
      && current.fingerprint !== beforeFingerprint
    ) {
      return true;
    }
    await delay(100);
  }
  return false;
}

function parseEnterpriseDresp(text) {
  let raw = String(text || '').trim();
  if (raw.startsWith(")]}'")) {
    raw = raw.split('\n', 1)[1] || raw.slice(4).trim();
  }
  try {
    const value = JSON.parse(raw);
    if (!Array.isArray(value) || value[0] !== 'dresp') return null;
    return {
      value,
      code: value.length > 3 ? value[3] : null,
      retryable: (
        value.length > 3
        && value[1] == null
        && value[2] == null
        && value[3] === 2
      )
    };
  } catch {
    return null;
  }
}

async function forwardEnterpriseNetworkRequest(
  request,
  bridge,
  state,
  timeoutMs,
) {
  const bodyBytes = Array.isArray(request && request.bodyBytes)
    ? request.bodyBytes
    : [];
  const body = nodeBuffer.from(bodyBytes);
  const url = String(request && request.url || '');
  const summary = {
    url,
    method: String(request && request.method || 'GET').toUpperCase(),
    headers: Object.assign({}, request && request.headers || {}),
    bodyLength: body.length,
    bodyBase64: body.toString('base64'),
    initiatorType: String(request && request.initiatorType || ''),
    referrer: String(request && request.referrer || ''),
    locationHref: String(request && request.locationHref || ''),
    status: 0,
    responseLength: 0,
    responsePrefix: '',
    startedAtMs: Date.now(),
    completedAtMs: null,
    networkError: false
  };
  state.liveRequests.push(summary);
  if (state.liveRequests.length > 100) state.liveRequests.shift();
  if (isEnterpriseEndpoint(url, 'userverify')) {
    state.userverifyRequests.push(summary);
  }

  let reply;
  try {
    reply = await bridge.request('network_request', {
      url: summary.url,
      method: summary.method,
      headers: summary.headers,
      bodyLength: summary.bodyLength,
      bodyBase64: summary.bodyBase64,
      initiatorType: summary.initiatorType,
      referrer: summary.referrer,
      locationHref: summary.locationHref
    }, timeoutMs);
  } catch (error) {
    summary.completedAtMs = Date.now();
    summary.elapsedMs = summary.completedAtMs - summary.startedAtMs;
    summary.networkError = true;
    if (isEnterpriseEndpoint(url, 'userverify')) state.completion.notify();
    return {
      networkError: true,
      error: String(error && error.message || error)
    };
  }
  if (!reply || reply.error) {
    summary.completedAtMs = Date.now();
    summary.elapsedMs = summary.completedAtMs - summary.startedAtMs;
    summary.networkError = true;
    if (isEnterpriseEndpoint(url, 'userverify')) state.completion.notify();
    return {
      networkError: true,
      error: String(reply && reply.error || 'network bridge failed')
    };
  }
  const responseBytes = nodeBuffer.from(
    String(reply.bodyBase64 || ''),
    'base64',
  );
  addEnterpriseResources(reply.resources || [], false);
  summary.status = Number(reply.status || 0);
  summary.completedAtMs = Date.now();
  summary.elapsedMs = summary.completedAtMs - summary.startedAtMs;
  summary.bridgeTotalSeconds = reply.bridgeTotalSeconds;
  summary.responseLength = responseBytes.length;
  const responseText = responseBytes.toString('utf8');
  summary.responsePrefix = responseText.slice(0, 300);
  if (isEnterpriseEndpoint(url, 'userverify')) {
    summary.responseText = responseText;
    recordEnterpriseProtocolResponse(summary, responseText);
    state.completion.notify();
  }
  if (isEnterpriseEndpoint(url, 'replaceimage')) {
    const parsed = parseEnterpriseDresp(responseText);
    summary.replaceimageCode = parsed ? parsed.code : null;
    state.replaceimageRetrySubmitting = false;
    state.replaceimageRetryPending = Boolean(parsed && parsed.retryable);
    if (state.replaceimageRetryPending) {
      state.replaceimageErrorCount += 1;
    }
  }
  return {
    status: Number(reply.status || 0),
    statusText: String(reply.statusText || ''),
    headers: Object.assign({}, reply.headers || {}),
    body: new Uint8Array(responseBytes),
    responseURL: String(reply.url || url)
  };
}

function addEnterpriseResources(resources, debugEncoder) {
  for (const resource of resources || []) {
    const normalized = Object.assign({}, resource || {});
    if (normalized.bodyBase64) {
      normalized.body = new Uint8Array(
        nodeBuffer.from(String(normalized.bodyBase64), 'base64'),
      );
      delete normalized.bodyBase64;
    }
    __mini_browser__.addResource(
      patchRecaptchaSdkResource(normalized, Boolean(debugEncoder)),
    );
  }
}

async function waitForDeferredFrame(state, key, timeoutMs) {
  const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 30000));
  while (Date.now() < deadline) {
    const deferred = state[key];
    if (deferred && deferred.element && deferred.url) return deferred;
    await delay(50);
  }
  throw new Error(key + ' iframe request not captured');
}

async function resumeDeferredFrame(deferred, stateKey, state) {
  const frame = deferred && deferred.element;
  const url = String(deferred && deferred.url || '');
  if (!frame || !url) throw new Error(stateKey + ' iframe is missing');
  state[stateKey + 'Ready'] = true;
  const hiddenName = stateKey === 'anchor'
    ? '__recapDeferredAnchorSrc'
    : '__recapDeferredBframeSrc';
  try {
    delete frame[hiddenName];
  } catch {
    try {
      Object.defineProperty(frame, hiddenName, {
        configurable: true,
        writable: true,
        enumerable: false,
        value: ''
      });
    } catch {
      frame[hiddenName] = '';
    }
  }
  frame.src = url;
  await delay(100);
  return frame;
}

async function waitForFrameDocument(frame, timeoutMs) {
  const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 30000));
  while (Date.now() < deadline) {
    try {
      const doc = frame && frame.contentDocument;
      if (doc && doc.documentElement && doc.body) return doc;
    } catch {
    }
    await delay(50);
  }
  throw new Error('iframe document did not load');
}

function parseStylePercent(styleText, propertyName) {
  const match = new RegExp(
    '(?:^|;)\\s*' + propertyName + '\\s*:\\s*(-?[0-9.]+)%',
    'i',
  ).exec(String(styleText || ''));
  return match ? Number(match[1]) : 0;
}

function elementText(documentObject, selector) {
  try {
    const element = documentObject.querySelector(selector);
    return String(element && element.innerText || '').trim();
  } catch {
    return '';
  }
}

function resourceBodyBase64(url) {
  const target = String(url || '');
  if (!target) return '';
  const records = [];
  try {
    records.push(...(__mini_browser__.network.resources || []));
  } catch {
  }
  try {
    records.push(...(__mini_browser__.network.entries || []));
  } catch {
  }
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index];
    if (!record || String(record.url || '') !== target) continue;
    const candidates = [
      record.body,
      record.responseBody,
      record.response,
    ];
    for (const value of candidates) {
      const encoded = bodyBase64(value);
      if (encoded) return encoded;
    }
  }
  return '';
}

function enterpriseActiveTileContext(doc) {
  const tableSelector = [
    '.rc-imageselect-table-33',
    '.rc-imageselect-table-44',
    '.rc-imageselect-table-42',
    '#rc-imageselect-target table'
  ].join(',');
  const tables = Array.from(doc.querySelectorAll(tableSelector))
    .filter((table) => (
      table.querySelectorAll('.rc-imageselect-tile').length > 0
    ));
  const activeTables = tables.filter((table) => {
    const marker = [
      String(table.className || ''),
      String(table.parentElement && table.parentElement.className || ''),
      String(table.getAttribute && table.getAttribute('aria-hidden') || ''),
    ].join(' ');
    return !/(?:offscreen|leaving)|\btrue\b/i.test(marker);
  });
  const table = (
    activeTables.length
      ? activeTables[activeTables.length - 1]
      : tables[tables.length - 1]
  ) || null;
  let tiles = table
    ? Array.from(table.querySelectorAll('.rc-imageselect-tile'))
    : Array.from(doc.querySelectorAll('.rc-imageselect-tile'));
  let rows = table
    ? Array.from(table.querySelectorAll('tr')).filter(
        (row) => row.querySelectorAll('.rc-imageselect-tile').length > 0,
      ).length
    : Math.max(1, Math.round(Math.sqrt(tiles.length || 1)));
  let columns = 0;
  if (table) {
    for (const row of Array.from(table.querySelectorAll('tr'))) {
      columns = Math.max(
        columns,
        row.querySelectorAll('.rc-imageselect-tile').length,
      );
    }
  }
  if (!columns) {
    columns = Math.max(1, Math.ceil((tiles.length || 1) / rows));
  }
  const activeCount = Math.max(1, rows * columns);
  if (tiles.length > activeCount) tiles = tiles.slice(-activeCount);
  return {
    table,
    tiles,
    rows,
    columns,
    tableCount: tables.length,
    allTileCount: doc.querySelectorAll('.rc-imageselect-tile').length
  };
}

function enterpriseChallengeSnapshot(frame, round) {
  let doc;
  try {
    doc = frame && frame.contentDocument;
  } catch {
    return null;
  }
  if (!doc || !doc.body) return null;
  const bodyText = String(doc.body.innerText || '').trim();
  const blocked = /稍后重试|try again later|automated quer/i.test(bodyText);
  const tileContext = enterpriseActiveTileContext(doc);
  const tiles = tileContext.tiles.map((tile, index) => {
    const images = Array.from(tile.querySelectorAll('img'));
    const image = images.reverse().find((item) => (
      item && (item.getAttribute('src') || item.src)
    )) || null;
    const style = String(image && image.getAttribute('style') || '');
    const src = String(
      image && (image.getAttribute('src') || image.src) || '',
    );
    const className = String(tile.className || '');
    return {
      index: index + 1,
      id: String(tile.id || index),
      selected: images.length <= 1
        && /(?:^|\s)(?:rc-imageselect-tileselected|rc-imageselect-dynamic-selected)(?:\s|$)/.test(className),
      className,
      domSelected: /(?:^|\s)(?:rc-imageselect-tileselected|rc-imageselect-dynamic-selected)(?:\s|$)/.test(className),
      imageCount: images.length,
      imageComplete: Boolean(image && image.complete),
      naturalWidth: Number(image && image.naturalWidth || 0),
      naturalHeight: Number(image && image.naturalHeight || 0),
      src,
      style,
      leftPercent: parseStylePercent(style, 'left'),
      topPercent: parseStylePercent(style, 'top')
    };
  });
  const rows = tileContext.rows;
  const columns = tileContext.columns;
  const promptParts = [
    elementText(doc, '.rc-imageselect-desc-wrapper'),
    elementText(doc, '.rc-imageselect-desc-no-canonical'),
    elementText(doc, '.rc-imageselect-instructions'),
    elementText(doc, '.rc-imageselect-carousel-instructions'),
  ].filter(Boolean);
  const uniquePromptParts = Array.from(new Set(promptParts));
  const prompt = uniquePromptParts.filter((part) => (
    !uniquePromptParts.some(
      (other) => other !== part && other.includes(part),
    )
  )).join('\n');
  const errors = [
    elementText(doc, '.rc-imageselect-incorrect-response'),
    elementText(doc, '.rc-imageselect-error-select-more'),
    elementText(doc, '.rc-imageselect-error-dynamic-more'),
    elementText(doc, '.rc-imageselect-error-select-something'),
  ].filter(Boolean);
  const verifyButton = doc.querySelector('#recaptcha-verify-button');
  const imageURLs = Array.from(
    new Set(tiles.map((tile) => tile.src).filter(Boolean)),
  );
  if (imageURLs.length > 1) {
    for (const tile of tiles) tile.selected = false;
  }
  const imageBodies = {};
  for (const url of imageURLs) {
    const body = resourceBodyBase64(url);
    if (body) imageBodies[url] = body;
  }
  const imageResourcesReady = imageURLs.length > 0
    && imageURLs.every((url) => Boolean(imageBodies[url]));
  const interactionReady = Boolean(
    verifyButton && !verifyButton.disabled,
  );
  const snapshot = {
    round,
    ready: (
      tiles.length > 0
      && imageResourcesReady
      && interactionReady
    ),
    blocked,
    bodyText: bodyText.slice(0, 1200),
    prompt,
    errors,
    rows,
    columns,
    tileCount: tiles.length,
    allTileCount: tileContext.allTileCount,
    tableCount: tileContext.tableCount,
    tiles,
    imageURLs,
    imageBodies,
    imageResourcesReady,
    interactionReady,
    verifyButtonText: String(verifyButton && verifyButton.innerText || ''),
    verifyButtonDisabled: Boolean(verifyButton && verifyButton.disabled),
    bframeURL: String(frame && frame.src || ''),
  };
  snapshot.fingerprint = JSON.stringify({
    prompt: snapshot.prompt,
    errors: snapshot.errors,
    rows: snapshot.rows,
    columns: snapshot.columns,
    verifyButtonText: snapshot.verifyButtonText,
    tiles: tiles.map((tile) => [
      tile.src,
      tile.style,
      tile.selected,
    ])
  });
  return snapshot;
}

function enterpriseTokenValue(state) {
  return enterpriseEvidence(state).responseToken;
}

function enterpriseResult(state, extra) {
  const userverify = state.userverifyRequests.length
    ? state.userverifyRequests[state.userverifyRequests.length - 1]
    : null;
  const result = {
    ...enterpriseEvidence(state),
    userverifyRequest: userverify,
    userverifyObserved: Boolean(userverify),
    userverifyCodeIsOne: Boolean(userverify && userverify.userverifyCodeIsOne),
    userverifyCode: userverify ? userverify.userverifyCode : null,
    liveRequests: state.liveRequests.map((item) => ({
      url: redactURL(item.url),
      method: item.method,
      bodyLength: item.bodyLength,
      status: item.status,
      responseLength: item.responseLength,
      startedAtMs: item.startedAtMs,
      completedAtMs: item.completedAtMs,
      elapsedMs: item.elapsedMs,
      bridgeTotalSeconds: item.bridgeTotalSeconds,
      networkError: Boolean(item.networkError)
    })),
    tileWaitHistory: state.tileWaitHistory || [],
    protocolResponseDiagnostics: state.userverifyRequests.map(item => ({
      status: item.status, code: item.userverifyCode,
      hasNextChallenge: item.hasNextChallenge,
      nextChallengeType: item.nextChallengeType,
      startedAtMs: item.startedAtMs, completedAtMs: item.completedAtMs
    })),
    messageCount: state.messages.length,
    asyncErrors: state.asyncErrors,
    networkEntryCount: (__mini_browser__.network.entries || []).length
  };
  const output = Object.assign({}, result, extra || {});
  if (result.protocol_terminal) {
    output.ok = result.ok;
    output.error = result.error;
  }
  if (output.challenge && output.challenge.imageBodies) {
    output.challenge = Object.assign({}, output.challenge);
    delete output.challenge.imageBodies;
  }
  return output;
}

function captureEnterpriseFrameErrors(frame, label, state) {
  const target = frame && frame.contentWindow;
  if (!target || typeof target.addEventListener !== 'function') return;
  target.addEventListener('error', event => {
    state.asyncErrors.push({ frame: label, type: 'error', message: String(event && event.message || '') });
  });
  target.addEventListener('unhandledrejection', event => {
    state.asyncErrors.push({ frame: label, type: 'unhandledrejection', reason: String(event && event.reason || '') });
  });
}

async function runEnterpriseMain(payload) {
  const bridge = createLineBridge(payload.protocol_timeout_ms || 180000);
  const pageUrl = String(
    payload.page_url
    || 'https://www.google.com/recaptcha-enterprise-protocol.html',
  );
  const state = {
    messages: [],
    fetches: [],
    xhrs: [],
    asyncErrors: [],
    textEncodes: [],
    workerDiagnostics: [],
    fieldDiagnostics: [],
    messagePortDiagnostics: [],
    performanceObserverDiagnostics: [],
    apiTouchDiagnostics: [],
    enableWorkerDiagnostics: Boolean(payload.debug_encoder),
    enableNetworkHooks: false,
    networkTimeoutMs: Number(payload.network_timeout_ms || 90000),
    liveRequests: [],
    userverifyRequests: [],
    replaceimageErrorCount: 0,
    replaceimageRetryPending: false,
    replaceimageRetrySubmitting: false,
    submissionNoChangeCount: 0,
    submissionRetryPending: false,
    lastChallengeSnapshot: null,
    anchorReady: false,
    bframeReady: false
  };
  state.completion = createEnterpriseCompletion(state);

  const baseEnvironment = {
    location: { href: pageUrl },
    document: { referrer: '', readyState: 'loading' },
    navigator: {
      userAgent: String(payload.user_agent || ''),
      language: String(payload.language || 'zh-CN'),
      languages: Array.isArray(payload.languages)
        ? payload.languages
        : ['zh-CN', 'zh'],
      platform: 'Win32',
      webdriver: false,
      hardwareConcurrency: 16,
      deviceMemory: 8
    },
    screen: {
      width: 1365,
      height: 900,
      availWidth: 1365,
      availHeight: 860,
      colorDepth: 24,
      pixelDepth: 24
    },
    network: {
      defaultStatus: 404,
      defaultStatusText: 'Not Found',
      defaultBody: '',
      requestHandler: (request) => forwardEnterpriseNetworkRequest(
        request,
        bridge,
        state,
        payload.network_timeout_ms || 90000,
      )
    }
  };
  const environment = mergeEnvironment(
    baseEnvironment,
    payload.environment || {},
  );
  if (!environment.network) environment.network = {};
  environment.network.requestHandler = baseEnvironment.network.requestHandler;
  installBrowserEnv({ environment });
  installCaptureHooks(state);
  if (payload.debug_encoder) installTextEncoderHook(state);
  if (payload.debug_encoder) installMessagePortDiagnostics(state);
  if (payload.debug_encoder) installApiTouchDiagnostics(state);
  installAnchorDefer(state);

  const resources = (Array.isArray(payload.resources) ? payload.resources : [])
    .map((resource) => patchRecaptchaSdkResource(
      resource,
      Boolean(payload.debug_encoder),
    ));
  __mini_browser__.page.load({
    baseURL: pageUrl,
    html: String(payload.html || ''),
    resources
  });

  try {
    const anchorDeferred = await waitForDeferredFrame(
      state,
      'deferredAnchor',
      30000,
    );
    const anchorReply = await bridge.request('need_frame', {
      kind: 'anchor',
      url: anchorDeferred.url
    }, payload.network_timeout_ms || 90000);
    if (anchorReply.error) throw new Error(anchorReply.error);
    addEnterpriseResources(
      anchorReply.resources || [],
      payload.debug_encoder,
    );
    const anchorFrame = await resumeDeferredFrame(
      anchorDeferred,
      'anchor',
      state,
    );
    captureEnterpriseFrameErrors(anchorFrame, 'anchor', state);
    const anchorDocument = await waitForFrameDocument(anchorFrame, 30000);
    let checkbox = null;
    for (let index = 0; index < 300; index += 1) {
      checkbox = anchorDocument.querySelector('#recaptcha-anchor');
      if (checkbox) break;
      await delay(50);
    }
    if (!checkbox) throw new Error('Enterprise anchor checkbox not found');
    checkbox.click();

    const bframeDeferred = await waitForDeferredFrame(
      state,
      'deferredBframe',
      30000,
    );
    const bframeReply = await bridge.request('need_frame', {
      kind: 'bframe',
      url: bframeDeferred.url
    }, payload.network_timeout_ms || 90000);
    if (bframeReply.error) throw new Error(bframeReply.error);
    addEnterpriseResources(
      bframeReply.resources || [],
      payload.debug_encoder,
    );
    const bframe = await resumeDeferredFrame(
      bframeDeferred,
      'bframe',
      state,
    );
    captureEnterpriseFrameErrors(bframe, 'bframe', state);
    await waitForFrameDocument(bframe, 30000);

    return await runEnterpriseInteraction({ payload, state, bridge, frame: bframe }, {
      completion: state.completion.promise,
      terminal: () => enterpriseProtocolOutcome(state).terminal,
      snapshot: enterpriseChallengeSnapshot,
      token: () => enterpriseTokenValue(state),
      result: enterpriseResult,
      requestCount: enterpriseRequestCount,
      tileContext: enterpriseActiveTileContext,
      waitForSubmission: waitForEnterpriseSubmissionProgress,
      waitForTile: waitForEnterpriseTileReplacement,
      delay,
    });
  } finally {
    bridge.close();
  }
}

async function main() {
  const payloadPath = nodeProcess.argv[2];
  if (!payloadPath) throw new Error('missing internal payload file');
  const payload = JSON.parse(fs.readFileSync(payloadPath, 'utf8'));
  if (payload.mode !== 'enterprise-userverify') throw new Error('Only enterprise-userverify mode is supported');
  const result = await runEnterpriseMain(payload);
  await emitFinalJSON({ type: 'result', result });
  nodeProcess.exit(result.ok ? 0 : 2);
}

// Exports allow offline regression without launching the worker.
module.exports = { runEnterpriseInteraction, waitForTileReplacement,
  recordEnterpriseProtocolResponse, enterpriseEvidence,
  createEnterpriseCompletion, enterpriseProtocolOutcome };

if (require.main === module) {
  main().catch(async (error) => {
    try {
      await emitFinalJSON({ type: 'result', result: { ok: false, error: normalizeError(error) } });
    } finally { nodeProcess.exit(1); }
  });
}
