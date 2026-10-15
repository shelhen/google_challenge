
(() => {
  const __uaData = /*__SGSS_UA_DATA__*/{};
  // Adapt operations on the native instance; do not replace it with
  // an Object or add own navigator properties. No toString spoofing.
  const uaPrototype = NavigatorUAData.prototype;
  const nativeBrands = Object.getOwnPropertyDescriptor(uaPrototype, 'brands').get;
  const nativeEntropy = uaPrototype.getHighEntropyValues;
  const nativeUAJSON = uaPrototype.toJSON;
  const nativeLowEntropy = Reflect.apply(nativeUAJSON, navigator.userAgentData, []);
  const nativeUAConfigured = JSON.stringify(nativeLowEntropy) === JSON.stringify({
    brands: __uaData.brands, mobile: __uaData.mobile, platform: __uaData.platform
  });
  const cloneBrands = () => (nativeUAConfigured
    ? Reflect.apply(nativeBrands, navigator.userAgentData, []) : __uaData.brands).map(x => ({...x}));
  const uaJSON = () => ({brands: cloneBrands(), mobile: __uaData.mobile,
                        platform: __uaData.platform});
  const brandsDescriptor = Object.getOwnPropertyDescriptor(uaPrototype, 'brands');
  Object.defineProperty(uaPrototype, 'brands', {
    ...brandsDescriptor,
    get: Object.getOwnPropertyDescriptor({get brands() {
      Reflect.apply(nativeBrands, this, []); // native receiver check
      return Object.freeze(cloneBrands()); // fresh frozen array per read
    }}, 'brands').get
  });
  if (!nativeUAConfigured) Object.defineProperty(uaPrototype, 'toJSON', {
    ...Object.getOwnPropertyDescriptor(uaPrototype, 'toJSON'),
    value: function toJSON() {
      'use strict';
      Reflect.apply(nativeBrands, this, []);
      return uaJSON();
    }
  });
  const entropyKeys = new Set(['architecture','bitness','model',
    'platformVersion','fullVersionList','uaFullVersion','wow64']);
  Object.defineProperty(uaPrototype, 'getHighEntropyValues', {
    ...Object.getOwnPropertyDescriptor(uaPrototype, 'getHighEntropyValues'),
    value: function getHighEntropyValues(hints) {
      'use strict';
      const prefix = "Failed to execute 'getHighEntropyValues' on 'NavigatorUAData': ";
      try {
        try { Reflect.apply(nativeBrands, this, []); }
        catch (error) { throw new TypeError(prefix + 'Illegal invocation'); }
        if (!arguments.length) throw new TypeError(prefix + '1 argument required, but only 0 present.');
        if (hints === null || (typeof hints !== 'object' && typeof hints !== 'function')) {
          throw new TypeError(prefix + 'The provided value cannot be converted to a sequence.');
        }
        const iterator = hints[Symbol.iterator];
        if (typeof iterator !== 'function') {
          throw new TypeError(prefix + 'The object must have a callable @@iterator property.');
        }
        const converted = [];
        // Consume the iterable and each DOMString only once. Never
        // copy arbitrary hint names such as constructor/toJSON.
        for (const hint of {[Symbol.iterator]: () => Reflect.apply(iterator, hints, [])}) {
          if (typeof hint === 'symbol') throw new TypeError(prefix + 'Cannot convert a Symbol value to a string');
          converted.push(String(hint));
        }
        return Reflect.apply(nativeEntropy, this, [converted]).then(() => {
          const out = uaJSON();
          for (const key of converted) {
            if (entropyKeys.has(key)) out[key] = key === 'fullVersionList'
              ? __uaData.fullVersionList.map(x => ({...x})) : __uaData[key];
          }
          return out;
        });
      } catch (error) { return Promise.reject(error); }
    }
  });

  // IV8 emits loading/interactive/complete readystatechange events in
  // the right order, but its getter always returns the environment's
  // 'complete'. Follow the actual native loader, without reparsing HTML,
  // rewriting challenge scripts, or manually dispatching lifecycle events.
  const readyDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'readyState');
  let loadingState = Reflect.apply(readyDescriptor.get, document, []);
  let readyChange = 0;
  document.addEventListener('readystatechange', event => {
    if (event.target === document && event.isTrusted) {
      loadingState = ['loading', 'interactive', 'complete'][readyChange++ % 3];
    }
  }, true);
  Object.defineProperty(Document.prototype, 'readyState', {
    ...readyDescriptor,
    get: function() {
      const nativeState = Reflect.apply(readyDescriptor.get, this, []);
      return this === document ? loadingState : nativeState;
    }
  });
  // Recover the main HTML document's live script collection using
  // IV8's native HTMLCollection, never a tagged Array or Proxy.
  // The native namespace-aware lookup is itself incomplete, so this
  // narrowly covers ordinary HTML scripts, not SVG/XML documents.
  const scriptsDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'scripts');
  const nativeScripts = scriptsDescriptor.get;
  const nativeGetScripts = Document.prototype.getElementsByTagName;
  let htmlScripts;
  if (Reflect.apply(nativeScripts, document, []) === null) {
    Object.defineProperty(Document.prototype, 'scripts', {
      ...scriptsDescriptor,
      get: Object.getOwnPropertyDescriptor({get scripts() {
        const current = Reflect.apply(nativeScripts, this, []);
        if (current !== null || this !== document || this.contentType !== 'text/html') return current;
        if (!htmlScripts) htmlScripts = Reflect.apply(nativeGetScripts, this, ['script']);
        return htmlScripts;
      }}, 'scripts').get
    });
  }

  // Correct whitespace parsing only for single keyword media features
  // that the native engine already evaluates. Width/resolution, ranges,
  // compound queries and non-string coercion stay native/unmodified.
  const nativeMatchMedia = window.matchMedia;
  if (Reflect.apply(nativeMatchMedia, window, ['(pointer:fine)']).matches !==
      Reflect.apply(nativeMatchMedia, window, ['(pointer: fine)']).matches) {
    const mediaDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia');
    Object.defineProperty(window, 'matchMedia', {
      ...mediaDescriptor,
      value: function matchMedia(query) {
        'use strict';
        if (typeof query === 'string') {
          const simple = query.match(/^\(\s*(pointer|any-pointer|hover|any-hover|color-gamut|prefers-color-scheme|prefers-reduced-motion)\s*:\s*([a-z-]+)\s*\)$/);
          if (simple) query = '(' + simple[1] + ': ' + simple[2] + ')';
        }
        const args = Array.from(arguments);
        if (args.length) args[0] = query;
        return Reflect.apply(nativeMatchMedia, this, args);
      }
    });
  }
  // IV8 0.1.3 already provides native PluginArray/MimeTypeArray.
  // Do not replace them with tagged Arrays or invent chrome.runtime.
  // IV8 0.1.3/0.1.4 cannot create native Trusted* values. Keep an explicitly
  // limited STRING fallback so eval(createScript(...)) still executes.
  // Correct callback/DOMString semantics, but never fake native tags,
  // isScript()/isHTML(), eval(), or CSP enforcement.
  const policyRules = new WeakMap();
  const policyPrefix = "Failed to execute 'createPolicy' on 'TrustedTypePolicyFactory': ";
  const domString = (value, prefix) => {
    if (typeof value === 'symbol') throw new TypeError(prefix + 'Cannot convert a Symbol value to a string');
    return String(value);
  };
  const stringPolicyPrototype = {};
  Object.defineProperty(stringPolicyPrototype, 'name', {enumerable: true,
    get: function() {
      const data = policyRules.get(this);
      if (!data) throw new TypeError('Illegal invocation');
      return data.name;
    }
  });
  for (const operation of ['createHTML', 'createScript', 'createScriptURL']) {
    // Concise method syntax gives the correct name and arity naturally.
    const method = {[operation](input) {
      const prefix = "Failed to execute '" + operation + "' on 'TrustedTypePolicy': ";
      const data = policyRules.get(this);
      if (!data) throw new TypeError(prefix + 'Illegal invocation');
      if (!arguments.length) throw new TypeError(prefix + '1 argument required, but only 0 present.');
      const text = domString(input, prefix);
      const callback = data.rules[operation];
      if (!callback) throw new TypeError(prefix + 'Policy ' + data.name +
        "'s TrustedTypePolicyOptions did not specify a '" + operation + "' member.");
      const result = Reflect.apply(callback, undefined, [text, ...Array.from(arguments).slice(1)]);
      // Nullable callback return: null/undefined produce empty text.
      const callbackName = operation[0].toUpperCase() + operation.slice(1) + 'Callback';
      return result == null ? '' : domString(result,
        "Failed to execute 'invoke' on '" + callbackName + "': " + prefix);
    }}[operation];
    Object.defineProperty(stringPolicyPrototype, operation, {value: method,
      enumerable: true, writable: true, configurable: true});
  }
  const factory = window.trustedTypes;
  const nativeCreatePolicy = factory.createPolicy;
  const nativeIsScript = factory.isScript;
  Object.defineProperty(factory, 'createPolicy', {
    configurable: true, writable: true,
    value: function createPolicy(name) {
      'use strict';
      try { Reflect.apply(nativeIsScript, this, ['']); }
      catch (error) { throw new TypeError(policyPrefix + 'Illegal invocation'); }
      if (!arguments.length) throw new TypeError(policyPrefix + '1 argument required, but only 0 present.');
      name = domString(name, policyPrefix);
      const options = arguments[1];
      if (options != null && typeof options !== 'object' && typeof options !== 'function') {
        throw new TypeError(policyPrefix + "The provided value is not of type 'TrustedTypePolicyOptions'.");
      }
      const rules = {};
      for (const key of ['createHTML','createScript','createScriptURL']) {
        const callback = options == null ? undefined : options[key];
        if (callback !== undefined && typeof callback !== 'function') {
          throw new TypeError(policyPrefix + "Failed to read the '" + key +
            "' property from 'TrustedTypePolicyOptions': The given value is not a function.");
        }
        rules[key] = callback;
      }
      // Do not override working native policy implementations on a
      // future IV8 version. Only fall back for the observed empty stub.
      const nativePolicy = Reflect.apply(nativeCreatePolicy, this, [name, rules]);
      if (nativePolicy instanceof TrustedTypePolicy &&
          ['createHTML','createScript','createScriptURL'].every(k => typeof nativePolicy[k] === 'function')) {
        return nativePolicy;
      }
      const policy = Object.create(stringPolicyPrototype);
      policyRules.set(policy, {name, rules});
      return policy;
    }
  });
  // Actual challenge trace: it creates an anchor WITHOUT href,
  // installs onclick and calls click(). IV8 0.1.3/0.1.4 click() is
  // silent, while dispatchEvent correctly delivers a native event.
  // Repair only this non-navigating case; do not invent activation
  // behavior for links, form controls, downloads, or checkboxes.
  const clickDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'click');
  const nativeClick = clickDescriptor.value;
  const clickProbe = document.createElement('a');
  let nativeAnchorClick = false;
  clickProbe.addEventListener('click', () => {nativeAnchorClick = true;});
  Reflect.apply(nativeClick, clickProbe, []);
  if (!nativeAnchorClick) {
    const clicking = new WeakSet();
    Object.defineProperty(HTMLElement.prototype, 'click', {
      ...clickDescriptor,
      value: function click() {
        'use strict';
        if (!(this instanceof HTMLAnchorElement) || this.hasAttribute('href')) {
          return Reflect.apply(nativeClick, this, arguments);
        }
        if (clicking.has(this)) return;
        clicking.add(this);
        try {
          this.dispatchEvent(new PointerEvent('click', {
            bubbles: true, cancelable: true, composed: true, view: window,
            detail: 0, button: 0, buttons: 0, pointerId: -1,
            pointerType: '', isPrimary: false, width: 1, height: 1, pressure: 0
          }));
        } finally { clicking.delete(this); }
      }
    });
  }

  // IV8's chrome.csi().pageT is a constant 1000. The challenge reads
  // it and checks Number.isInteger; use the real native high-resolution
  // page clock, not a sampled Chrome value or random decimal.
  if (window.chrome && typeof chrome.csi === 'function') {
    const nativeCSI = chrome.csi;
    const csiDescriptor = Object.getOwnPropertyDescriptor(chrome, 'csi');
    Object.defineProperty(chrome, 'csi', {...csiDescriptor,
      value: function csi() {
        const result = Reflect.apply(nativeCSI, this, arguments);
        result.pageT = performance.now();
        return result;
      }
    });
  }

  // IV8 0.1.3: legacy event factories reject supported interface names.
  // Try native first so a future IV8 implementation takes precedence.
  const createEventDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'createEvent');
  const nativeCreateEvent = createEventDescriptor.value;
  const documentBrand = Object.getOwnPropertyDescriptor(Document.prototype, 'documentElement').get;
  const legacyEvents = new WeakSet();
  const eventTypes = new Map([
    ['mouseevent', MouseEvent], ['mouseevents', MouseEvent],
    ['event', Event], ['events', Event], ['htmlevents', Event],
    ['uievent', UIEvent], ['uievents', UIEvent],
    ['keyboardevent', KeyboardEvent], ['customevent', CustomEvent]
  ]);
  Object.defineProperty(Document.prototype, 'createEvent', {
    ...createEventDescriptor,
    value: function createEvent(type) {
      'use strict';
      // Use a native getter for the receiver brand check, not instanceof.
      Reflect.apply(documentBrand, this, []);
      if (!arguments.length) return Reflect.apply(nativeCreateEvent, this, []);
      if (typeof type === 'symbol') throw new TypeError('Cannot convert a Symbol value to a string');
      const name = String(type);
      try { return Reflect.apply(nativeCreateEvent, this, [name]); }
      catch (error) {
        const Constructor = eventTypes.get(name.toLowerCase());
        if (error.name !== 'NotSupportedError' || !Constructor) throw error;
        // CustomEvent in this IV8 rejects an empty constructor type.
        const event = new Constructor('sgss-legacy-event');
        event.initEvent('', false, false);
        legacyEvents.add(event);
        return event;
      }
    }
  });
  // createEvent yields an UNINITIALIZED event, unlike new Event('').
  // Only legacy fallback instances are tracked; ordinary events retain
  // the native behavior and prototype chain.
  for (const [prototype, name] of [
    [Event.prototype, 'initEvent'], [UIEvent.prototype, 'initUIEvent'],
    [MouseEvent.prototype, 'initMouseEvent'],
    [KeyboardEvent.prototype, 'initKeyboardEvent'],
    [CustomEvent.prototype, 'initCustomEvent']
  ]) {
    const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
    if (!descriptor || typeof descriptor.value !== 'function') continue;
    const nativeInit = descriptor.value;
    Object.defineProperty(prototype, name, {...descriptor, value: function(...args) {
      const result = Reflect.apply(nativeInit, this, args);
      legacyEvents.delete(this);
      return result;
    }});
  }
  const dispatchDescriptor = Object.getOwnPropertyDescriptor(EventTarget.prototype, 'dispatchEvent');
  const nativeDispatch = dispatchDescriptor.value;
  Object.defineProperty(EventTarget.prototype, 'dispatchEvent', {
    ...dispatchDescriptor,
    value: function dispatchEvent(event) {
      'use strict';
      if (this instanceof EventTarget && legacyEvents.has(event)) {
        throw new DOMException("Failed to execute 'dispatchEvent' on 'EventTarget': The event is already being dispatched or was never initialized.", 'InvalidStateError');
      }
      return Reflect.apply(nativeDispatch, this, arguments);
    }
  });

  // Promise-returning Web IDL operation: invalid receivers reject the
  // returned promise rather than throwing into the caller's try/catch.
  // Do not replace Promise or change valid query results.
  const queryDescriptor = Object.getOwnPropertyDescriptor(Permissions.prototype, 'query');
  const nativeQuery = queryDescriptor.value;
  Object.defineProperty(Permissions.prototype, 'query', {
    ...queryDescriptor,
    value: function query(permissionDesc) {
      'use strict';
      try { return Reflect.apply(nativeQuery, this, arguments); }
      catch (error) {
        if (error instanceof TypeError && error.message === 'Illegal invocation') {
          error = new TypeError("Failed to execute 'query' on 'Permissions': Illegal invocation");
        }
        return Promise.reject(error);
      }
    }
  });

  window.__sgss_console = [];
  window.__sgss_errors = [];
  const recordError = value => {
    if (window.__sgss_errors.length < 20) window.__sgss_errors.push(String(value).slice(0, 500));
  };
  window.addEventListener('error', event => recordError(event.message || event.error));
  window.addEventListener('unhandledrejection', event => recordError(event.reason));
  window.__sgss_cookie_writes = [];
  window.__sgss_probe = {
    ua: String(navigator.userAgent||''),
    platform: String(navigator.platform||''),
    uaData: null,
    pluginsTag: Object.prototype.toString.call(navigator.plugins),
    mimeTypesTag: Object.prototype.toString.call(navigator.mimeTypes),
    chromeTag: Object.prototype.toString.call(window.chrome),
    dpr: Number(window.devicePixelRatio||0),
    inner: [Number(window.innerWidth||0), Number(window.innerHeight||0)],
    screen: [Number(screen.width||0), Number(screen.height||0)],
    dateType: typeof Date.now,
    perfType: typeof performance.now,
    cryptoType: typeof (crypto && crypto.getRandomValues),
    intlLocale: Intl.DateTimeFormat().resolvedOptions().locale,
    dateString: new Date().toString()
  };
  try { window.__sgss_probe.uaData = JSON.parse(JSON.stringify(navigator.userAgentData||null)); } catch(e) {}
  try {
    const __cookieDesc = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
    if (__cookieDesc && __cookieDesc.set) {
      Object.defineProperty(Document.prototype, 'cookie', {
        configurable: true,
        get: __cookieDesc.get,
        set: function(v) {
          try { window.__sgss_cookie_writes.push(String(v)); } catch(e) {}
          return __cookieDesc.set.call(this, v);
        }
      });
    }
  } catch(e) {}
  const origLog = console.log ? console.log.bind(console) : function(){};
  console.log = function(...args) {
    try { window.__sgss_console.push(args.map(x => String(x)).join(' ')); } catch(e) {}
    try { return origLog(...args); } catch(e) {}
  };
  window.__sgss_read = function() {
    const ck = String(document.cookie || '');
    const m = ck.match(/(?:^|; )SG_SS=([^;]+)/);
    let fromConsole = '';
    try {
      for (const line of window.__sgss_console || []) {
        if (String(line).startsWith('SGSS_RESULT ')) fromConsole = String(line).slice('SGSS_RESULT '.length);
      }
    } catch(e) {}
    const writes = (window.__sgss_cookie_writes || []).slice(-10);
    // Mirror CDP: explicit result, otherwise CURRENT document.cookie.
    // A historical write (even a longer one) may be replaced/deleted.
    const sg = fromConsole || (m && m[1]) || '';
    return {href: String(location.href), cookie: ck, sg_ss: sg,
      source: fromConsole ? 'console' : (sg ? 'document.cookie' : ''),
      writes, probe: window.__sgss_probe, errors: window.__sgss_errors.slice()};
  };
})();
