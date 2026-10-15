#!/usr/bin/env python3
from __future__ import annotations
from html.parser import HTMLParser
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)
# On macOS, importing curl_cffi before iv8 may fail in some conda envs with
# `symbol not found ... _CFRelease`.  Preload CoreFoundation globally so the
# bundled native extension can resolve the symbol regardless of import order.
import ctypes
import sys as _sys
if _sys.platform == "darwin":
    try:
        ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", mode=ctypes.RTLD_GLOBAL)
    except Exception:
        pass
from curl_cffi import requests
import asyncio
import concurrent.futures
import contextlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))

def _now() -> float:
    return time.perf_counter()


DEFAULT_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36'
DEFAULT_PLATFORM = "macOS"
DEFAULT_PLATFORM_VERSION = "14.6.0"
DEFAULT_CHROME_MAJOR = "155"
DEFAULT_CHROME_FULL_VERSION = "155.0.7827.104"
DEFAULT_X_BROWSER_VALIDATION = "rDexa0VxSGod7UzQ3NAJdokcges="
# Measured from this Mac's GPU-enabled CDP, not an IV8 hardware backend.
DEFAULT_GPU_VENDOR = 'Google Inc. (Apple)'
DEFAULT_GPU_RENDERER = 'ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)'
_IV8_CONTEXT_INIT_LOCK = threading.RLock()


class _EnterpriseFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.widget: Dict[str, str] = {}
        self.hidden: Dict[str, str] = {}
        self.form_action = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        values = {
            name.lower(): str(value or "")
            for name, value in attrs
        }
        if tag == "form" and values.get("id") == "captcha-form":
            self.form_action = values.get("action") or "index"
        elif tag == "input" and values.get("name") in {"q", "continue"}:
            self.hidden[values["name"]] = values.get("value") or ""
        elif tag in {"div", "span"}:
            classes = set(values.get("class", "").split())
            if "g-recaptcha" in classes and not self.widget:
                self.widget = values


@contextlib.contextmanager
def _silence_iv8_stdio():
    """Suppress noisy native iv8 stdout/stderr unless SGSS_VERBOSE_IV8=1."""
    if os.environ.get("SGSS_VERBOSE_IV8") == "1":
        yield
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    saved_stdout = saved_stderr = devnull = None
    try:
        saved_stdout = os.dup(1)
        saved_stderr = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        if saved_stdout is not None:
            try:
                os.dup2(saved_stdout, 1)
            except Exception:
                pass
            try:
                os.close(saved_stdout)
            except Exception:
                pass
        if saved_stderr is not None:
            try:
                os.dup2(saved_stderr, 2)
            except Exception:
                pass
            try:
                os.close(saved_stderr)
            except Exception:
                pass
        if devnull is not None:
            try:
                os.close(devnull)
            except Exception:
                pass


@dataclass
class Iv8SgssOptions:
    timeout_ms: int = 10000
    user_agent: str = DEFAULT_UA
    inner_width: int = 1365
    inner_height: int = 813
    outer_width: int = 1365
    outer_height: int = 900
    device_pixel_ratio: int = 1
    language: str = "zh-CN"
    languages: tuple = ("zh-CN", "zh")
    connection_downlink: float = 1.5
    connection_rtt: int = 200
    connection_effective_type: str = "4g"
    time_mode: str = "system"
    # ICU caches its process-wide locale at the FIRST JSContext creation.
    # Match the measured Chinese Chrome UI without patching Date/Intl methods.
    icu_locale: str = "zh_CN.UTF-8"
    # Match the GPU-enabled CDP profile. Native rendering is still limited.
    webgl_enabled: bool = True
    gpu_vendor: str = DEFAULT_GPU_VENDOR
    gpu_renderer: str = DEFAULT_GPU_RENDERER


class Iv8SgssRuntime:
    """Synchronous iv8 SG_SS generator.

    A fresh JSContext is used per challenge.  SG challenges are bound to the
    current HTML/eid and may leave timers/cookies behind, so per-call isolation
    is safer than reusing one context.
    """

    def __init__(self, opts: Optional[Iv8SgssOptions] = None):
        self.opts = opts or Iv8SgssOptions()
        self._lock = threading.RLock()

    def _import_iv8(self):
        try:
            with _silence_iv8_stdio():
                import iv8  # type: ignore
            return iv8
        except Exception as exc:  # pragma: no cover - host dependent
            raise RuntimeError(
                "iv8 is not available in this Python environment. "
                "Windows/Linux: `pip install iv8`; macOS: use Python 3.11+ on macOS 14+ and run "
                "`/Users/qinshaowen/Desktop/personal/hcaptcha/hcaptcha3.0/install_iv8_macos.sh`, "
                "or download a matching wheel from https://github.com/HanZzzzz000/iv8/releases."
            ) from exc

    def _create_context(self, iv8, page_url: str):
        # ICU reads LC_ALL while the first native context is initialized. The
        # environment change is scoped/restored; ICU's cached choice is not
        # per-context. A process that already initialized ICU with another
        # locale must restart to change it. All our runtime instances share
        # this lock; unrelated third-party context creation is outside it.
        with _IV8_CONTEXT_INIT_LOCK:
            previous = os.environ.get("LC_ALL")
            try:
                if self.opts.icu_locale:
                    os.environ["LC_ALL"] = self.opts.icu_locale
                return iv8.JSContext(
                    environment=self._browser_environment(page_url),
                    config=self._browser_config(),
                    time_mode=self.opts.time_mode,
                )
            finally:
                if previous is None:
                    os.environ.pop("LC_ALL", None)
                else:
                    os.environ["LC_ALL"] = previous

    def _browser_config(self) -> Dict[str, Any]:
        # Use IV8's native context switches; do not replace getContext().
        return {"canvas": {"context": {
            name: {"enabled": self.opts.webgl_enabled}
            for name in ("webgl", "experimental-webgl", "webgl2")
        }}}

    def _browser_environment(self, page_url: str) -> Dict[str, Any]:
        o = self.opts
        headers = {
            "navigator": {
                "userAgent": o.user_agent,
                "appVersion": o.user_agent.replace("Mozilla/", ""),
                # Emulation.setUserAgentOverride(platform="macOS") in the
                # working CDP runner exposes navigator.platform as "macOS".
                # This matches the measured CDP baseline; it does not by
                # itself establish the cause of a server-side HTTP 429.
                "platform": DEFAULT_PLATFORM,
                "vendor": "Google Inc.",
                "language": o.language,
                "languages": list(o.languages),
                "hardwareConcurrency": 8,
                "deviceMemory": 16,
                "maxTouchPoints": 0,
                "webdriver": False,
                "cookieEnabled": True,
                "onLine": True,
                "product": "Gecko",
                "productSub": "20030107",
                "pdfViewerEnabled": True,
                # Keep IV8's real NavigatorUAData instance. 0.1.4 supports
                # native brand/version config; adapters retain missing semantics.
                "userAgentData": {
                    # Official IV8 0.1.4 configuration (#20); 0.1.3 ignores
                    # these two fields, so the adapter still supports it.
                    "brands": [
                        {"brand": "Google Chrome", "version": DEFAULT_CHROME_MAJOR},
                        {"brand": "Chromium", "version": DEFAULT_CHROME_MAJOR},
                        {"brand": "Not)A;Brand", "version": "24"},
                    ],
                    "uaFullVersion": DEFAULT_CHROME_FULL_VERSION,
                    "platform": DEFAULT_PLATFORM,
                    "platformVersion": DEFAULT_PLATFORM_VERSION,
                    "architecture": "arm", "bitness": "64", "model": "",
                    "mobile": False, "wow64": False,
                },
                "connection": {
                    "downlink": o.connection_downlink,
                    "rtt": o.connection_rtt,
                    "effectiveType": o.connection_effective_type,
                    "saveData": False,
                },
            },
            "screen": {
                # Match the headless Chrome/CDP baseline.  These values are
                # observable by page scripts; token binding is not established.
                "width": 800,
                "height": 600,
                "availWidth": 800,
                "availHeight": 600,
                "colorDepth": 24,
                "pixelDepth": 24,
                "availLeft": 0,
                "availTop": 0,
            },
            "window": {
                "innerWidth": o.inner_width,
                "innerHeight": o.inner_height,
                "outerWidth": o.outer_width,
                "outerHeight": o.outer_height,
                "devicePixelRatio": o.device_pixel_ratio,
            },
            # Native environment metadata measured on Apple M3/ANGLE Metal.
            # Matching metadata does NOT supply a functioning GPU renderer.
            "webgl": {
                'VENDOR': 'WebKit',
                'RENDERER': 'WebKit WebGL',
                'VERSION': 'WebGL 1.0 (OpenGL ES 2.0 Chromium)',
                'SHADING_LANGUAGE_VERSION': 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)',
                'MAX_TEXTURE_SIZE': 16384,
                'MAX_CUBE_MAP_TEXTURE_SIZE': 16384,
                'MAX_RENDERBUFFER_SIZE': 16384,
                'MAX_VERTEX_ATTRIBS': 16,
                'MAX_VERTEX_TEXTURE_IMAGE_UNITS': 16,
                'MAX_TEXTURE_IMAGE_UNITS': 16,
                'MAX_COMBINED_TEXTURE_IMAGE_UNITS': 32,
                'MAX_VIEWPORT_DIMS': {'0': 16384, '1': 16384},
                'ALIASED_LINE_WIDTH_RANGE': {'0': 1, '1': 1},
                'ALIASED_POINT_SIZE_RANGE': {'0': 1, '1': 511},
                'RED_BITS': 8,
                'GREEN_BITS': 8,
                'BLUE_BITS': 8,
                'ALPHA_BITS': 8,
                'DEPTH_BITS': 24,
                'STENCIL_BITS': 0,
                'MAX_VERTEX_UNIFORM_VECTORS': 1024,
                'MAX_FRAGMENT_UNIFORM_VECTORS': 1024,
                'MAX_VARYING_VECTORS': 30,
                'SUBPIXEL_BITS': 4,
                "UNMASKED_VENDOR_WEBGL": o.gpu_vendor,
                "UNMASKED_RENDERER_WEBGL": o.gpu_renderer,
            },
            "webgpu": {
                "adapterInfo": {"vendor": "apple", "architecture": "metal-3",
                                "device": "", "description": ""},
            },
            # The CDP service creates about:blank, then navigates to the HTML.
            "history": {"length": 2},
            "location": {"href": page_url},
        }
        return headers

    @staticmethod
    def _preload_js() -> str:
        """Small compatibility layer for Google SG challenge in iv8."""
        brands = [
            {"brand": "Google Chrome", "version": DEFAULT_CHROME_MAJOR},
            {"brand": "Chromium", "version": DEFAULT_CHROME_MAJOR},
            {"brand": "Not)A;Brand", "version": "24"},
        ]
        full_version_list = [
            {"brand": "Google Chrome", "version": DEFAULT_CHROME_FULL_VERSION},
            {"brand": "Chromium", "version": DEFAULT_CHROME_FULL_VERSION},
            {"brand": "Not)A;Brand", "version": "24.0.0.0"},
        ]
        ua_data = {
            "brands": brands,
            "mobile": False,
            "platform": DEFAULT_PLATFORM,
            "architecture": "arm",
            "bitness": "64",
            "model": "",
            "platformVersion": DEFAULT_PLATFORM_VERSION,
            "fullVersionList": full_version_list,
            "uaFullVersion": DEFAULT_CHROME_FULL_VERSION,
            "wow64": False,
        }
        return f"""
        (() => {{
          const __uaData = {json.dumps(ua_data, ensure_ascii=False)};
          // Adapt operations on the native instance; do not replace it with
          // an Object or add own navigator properties. No toString spoofing.
          const uaPrototype = NavigatorUAData.prototype;
          const nativeBrands = Object.getOwnPropertyDescriptor(uaPrototype, 'brands').get;
          const nativeEntropy = uaPrototype.getHighEntropyValues;
          const nativeUAJSON = uaPrototype.toJSON;
          const nativeLowEntropy = Reflect.apply(nativeUAJSON, navigator.userAgentData, []);
          const nativeUAConfigured = JSON.stringify(nativeLowEntropy) === JSON.stringify({{
            brands: __uaData.brands, mobile: __uaData.mobile, platform: __uaData.platform
          }});
          const cloneBrands = () => (nativeUAConfigured
            ? Reflect.apply(nativeBrands, navigator.userAgentData, []) : __uaData.brands).map(x => ({{...x}}));
          const uaJSON = () => ({{brands: cloneBrands(), mobile: __uaData.mobile,
                                platform: __uaData.platform}});
          const brandsDescriptor = Object.getOwnPropertyDescriptor(uaPrototype, 'brands');
          Object.defineProperty(uaPrototype, 'brands', {{
            ...brandsDescriptor,
            get: Object.getOwnPropertyDescriptor({{get brands() {{
              Reflect.apply(nativeBrands, this, []); // native receiver check
              return Object.freeze(cloneBrands()); // fresh frozen array per read
            }}}}, 'brands').get
          }});
          if (!nativeUAConfigured) Object.defineProperty(uaPrototype, 'toJSON', {{
            ...Object.getOwnPropertyDescriptor(uaPrototype, 'toJSON'),
            value: function toJSON() {{
              'use strict';
              Reflect.apply(nativeBrands, this, []);
              return uaJSON();
            }}
          }});
          const entropyKeys = new Set(['architecture','bitness','model',
            'platformVersion','fullVersionList','uaFullVersion','wow64']);
          Object.defineProperty(uaPrototype, 'getHighEntropyValues', {{
            ...Object.getOwnPropertyDescriptor(uaPrototype, 'getHighEntropyValues'),
            value: function getHighEntropyValues(hints) {{
              'use strict';
              const prefix = "Failed to execute 'getHighEntropyValues' on 'NavigatorUAData': ";
              try {{
                try {{ Reflect.apply(nativeBrands, this, []); }}
                catch (error) {{ throw new TypeError(prefix + 'Illegal invocation'); }}
                if (!arguments.length) throw new TypeError(prefix + '1 argument required, but only 0 present.');
                if (hints === null || (typeof hints !== 'object' && typeof hints !== 'function')) {{
                  throw new TypeError(prefix + 'The provided value cannot be converted to a sequence.');
                }}
                const iterator = hints[Symbol.iterator];
                if (typeof iterator !== 'function') {{
                  throw new TypeError(prefix + 'The object must have a callable @@iterator property.');
                }}
                const converted = [];
                // Consume the iterable and each DOMString only once. Never
                // copy arbitrary hint names such as constructor/toJSON.
                for (const hint of {{[Symbol.iterator]: () => Reflect.apply(iterator, hints, [])}}) {{
                  if (typeof hint === 'symbol') throw new TypeError(prefix + 'Cannot convert a Symbol value to a string');
                  converted.push(String(hint));
                }}
                return Reflect.apply(nativeEntropy, this, [converted]).then(() => {{
                  const out = uaJSON();
                  for (const key of converted) {{
                    if (entropyKeys.has(key)) out[key] = key === 'fullVersionList'
                      ? __uaData.fullVersionList.map(x => ({{...x}})) : __uaData[key];
                  }}
                  return out;
                }});
              }} catch (error) {{ return Promise.reject(error); }}
            }}
          }});

          // IV8 emits loading/interactive/complete readystatechange events in
          // the right order, but its getter always returns the environment's
          // 'complete'. Follow the actual native loader, without reparsing HTML,
          // rewriting challenge scripts, or manually dispatching lifecycle events.
          const readyDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'readyState');
          let loadingState = Reflect.apply(readyDescriptor.get, document, []);
          let readyChange = 0;
          document.addEventListener('readystatechange', event => {{
            if (event.target === document && event.isTrusted) {{
              loadingState = ['loading', 'interactive', 'complete'][readyChange++ % 3];
            }}
          }}, true);
          Object.defineProperty(Document.prototype, 'readyState', {{
            ...readyDescriptor,
            get: function() {{
              const nativeState = Reflect.apply(readyDescriptor.get, this, []);
              return this === document ? loadingState : nativeState;
            }}
          }});
          // Recover the main HTML document's live script collection using
          // IV8's native HTMLCollection, never a tagged Array or Proxy.
          // The native namespace-aware lookup is itself incomplete, so this
          // narrowly covers ordinary HTML scripts, not SVG/XML documents.
          const scriptsDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'scripts');
          const nativeScripts = scriptsDescriptor.get;
          const nativeGetScripts = Document.prototype.getElementsByTagName;
          let htmlScripts;
          if (Reflect.apply(nativeScripts, document, []) === null) {{
            Object.defineProperty(Document.prototype, 'scripts', {{
              ...scriptsDescriptor,
              get: Object.getOwnPropertyDescriptor({{get scripts() {{
                const current = Reflect.apply(nativeScripts, this, []);
                if (current !== null || this !== document || this.contentType !== 'text/html') return current;
                if (!htmlScripts) htmlScripts = Reflect.apply(nativeGetScripts, this, ['script']);
                return htmlScripts;
              }}}}, 'scripts').get
            }});
          }}

          // Correct whitespace parsing only for single keyword media features
          // that the native engine already evaluates. Width/resolution, ranges,
          // compound queries and non-string coercion stay native/unmodified.
          const nativeMatchMedia = window.matchMedia;
          if (Reflect.apply(nativeMatchMedia, window, ['(pointer:fine)']).matches !==
              Reflect.apply(nativeMatchMedia, window, ['(pointer: fine)']).matches) {{
            const mediaDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia');
            Object.defineProperty(window, 'matchMedia', {{
              ...mediaDescriptor,
              value: function matchMedia(query) {{
                'use strict';
                if (typeof query === 'string') {{
                  const simple = query.match(/^\\(\\s*(pointer|any-pointer|hover|any-hover|color-gamut|prefers-color-scheme|prefers-reduced-motion)\\s*:\\s*([a-z-]+)\\s*\\)$/);
                  if (simple) query = '(' + simple[1] + ': ' + simple[2] + ')';
                }}
                const args = Array.from(arguments);
                if (args.length) args[0] = query;
                return Reflect.apply(nativeMatchMedia, this, args);
              }}
            }});
          }}
          // IV8 0.1.3 already provides native PluginArray/MimeTypeArray.
          // Do not replace them with tagged Arrays or invent chrome.runtime.
          // IV8 0.1.3/0.1.4 cannot create native Trusted* values. Keep an explicitly
          // limited STRING fallback so eval(createScript(...)) still executes.
          // Correct callback/DOMString semantics, but never fake native tags,
          // isScript()/isHTML(), eval(), or CSP enforcement.
          const policyRules = new WeakMap();
          const policyPrefix = "Failed to execute 'createPolicy' on 'TrustedTypePolicyFactory': ";
          const domString = (value, prefix) => {{
            if (typeof value === 'symbol') throw new TypeError(prefix + 'Cannot convert a Symbol value to a string');
            return String(value);
          }};
          const stringPolicyPrototype = {{}};
          Object.defineProperty(stringPolicyPrototype, 'name', {{enumerable: true,
            get: function() {{
              const data = policyRules.get(this);
              if (!data) throw new TypeError('Illegal invocation');
              return data.name;
            }}
          }});
          for (const operation of ['createHTML', 'createScript', 'createScriptURL']) {{
            // Concise method syntax gives the correct name and arity naturally.
            const method = {{[operation](input) {{
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
            }}}}[operation];
            Object.defineProperty(stringPolicyPrototype, operation, {{value: method,
              enumerable: true, writable: true, configurable: true}});
          }}
          const factory = window.trustedTypes;
          const nativeCreatePolicy = factory.createPolicy;
          const nativeIsScript = factory.isScript;
          Object.defineProperty(factory, 'createPolicy', {{
            configurable: true, writable: true,
            value: function createPolicy(name) {{
              'use strict';
              try {{ Reflect.apply(nativeIsScript, this, ['']); }}
              catch (error) {{ throw new TypeError(policyPrefix + 'Illegal invocation'); }}
              if (!arguments.length) throw new TypeError(policyPrefix + '1 argument required, but only 0 present.');
              name = domString(name, policyPrefix);
              const options = arguments[1];
              if (options != null && typeof options !== 'object' && typeof options !== 'function') {{
                throw new TypeError(policyPrefix + "The provided value is not of type 'TrustedTypePolicyOptions'.");
              }}
              const rules = {{}};
              for (const key of ['createHTML','createScript','createScriptURL']) {{
                const callback = options == null ? undefined : options[key];
                if (callback !== undefined && typeof callback !== 'function') {{
                  throw new TypeError(policyPrefix + "Failed to read the '" + key +
                    "' property from 'TrustedTypePolicyOptions': The given value is not a function.");
                }}
                rules[key] = callback;
              }}
              // Do not override working native policy implementations on a
              // future IV8 version. Only fall back for the observed empty stub.
              const nativePolicy = Reflect.apply(nativeCreatePolicy, this, [name, rules]);
              if (nativePolicy instanceof TrustedTypePolicy &&
                  ['createHTML','createScript','createScriptURL'].every(k => typeof nativePolicy[k] === 'function')) {{
                return nativePolicy;
              }}
              const policy = Object.create(stringPolicyPrototype);
              policyRules.set(policy, {{name, rules}});
              return policy;
            }}
          }});
          // Actual challenge trace: it creates an anchor WITHOUT href,
          // installs onclick and calls click(). IV8 0.1.3/0.1.4 click() is
          // silent, while dispatchEvent correctly delivers a native event.
          // Repair only this non-navigating case; do not invent activation
          // behavior for links, form controls, downloads, or checkboxes.
          const clickDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'click');
          const nativeClick = clickDescriptor.value;
          const clickProbe = document.createElement('a');
          let nativeAnchorClick = false;
          clickProbe.addEventListener('click', () => {{nativeAnchorClick = true;}});
          Reflect.apply(nativeClick, clickProbe, []);
          if (!nativeAnchorClick) {{
            const clicking = new WeakSet();
            Object.defineProperty(HTMLElement.prototype, 'click', {{
              ...clickDescriptor,
              value: function click() {{
                'use strict';
                if (!(this instanceof HTMLAnchorElement) || this.hasAttribute('href')) {{
                  return Reflect.apply(nativeClick, this, arguments);
                }}
                if (clicking.has(this)) return;
                clicking.add(this);
                try {{
                  this.dispatchEvent(new PointerEvent('click', {{
                    bubbles: true, cancelable: true, composed: true, view: window,
                    detail: 0, button: 0, buttons: 0, pointerId: -1,
                    pointerType: '', isPrimary: false, width: 1, height: 1, pressure: 0
                  }}));
                }} finally {{ clicking.delete(this); }}
              }}
            }});
          }}

          // IV8's chrome.csi().pageT is a constant 1000. The challenge reads
          // it and checks Number.isInteger; use the real native high-resolution
          // page clock, not a sampled Chrome value or random decimal.
          if (window.chrome && typeof chrome.csi === 'function') {{
            const nativeCSI = chrome.csi;
            const csiDescriptor = Object.getOwnPropertyDescriptor(chrome, 'csi');
            Object.defineProperty(chrome, 'csi', {{...csiDescriptor,
              value: function csi() {{
                const result = Reflect.apply(nativeCSI, this, arguments);
                result.pageT = performance.now();
                return result;
              }}
            }});
          }}

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
          Object.defineProperty(Document.prototype, 'createEvent', {{
            ...createEventDescriptor,
            value: function createEvent(type) {{
              'use strict';
              // Use a native getter for the receiver brand check, not instanceof.
              Reflect.apply(documentBrand, this, []);
              if (!arguments.length) return Reflect.apply(nativeCreateEvent, this, []);
              if (typeof type === 'symbol') throw new TypeError('Cannot convert a Symbol value to a string');
              const name = String(type);
              try {{ return Reflect.apply(nativeCreateEvent, this, [name]); }}
              catch (error) {{
                const Constructor = eventTypes.get(name.toLowerCase());
                if (error.name !== 'NotSupportedError' || !Constructor) throw error;
                // CustomEvent in this IV8 rejects an empty constructor type.
                const event = new Constructor('sgss-legacy-event');
                event.initEvent('', false, false);
                legacyEvents.add(event);
                return event;
              }}
            }}
          }});
          // createEvent yields an UNINITIALIZED event, unlike new Event('').
          // Only legacy fallback instances are tracked; ordinary events retain
          // the native behavior and prototype chain.
          for (const [prototype, name] of [
            [Event.prototype, 'initEvent'], [UIEvent.prototype, 'initUIEvent'],
            [MouseEvent.prototype, 'initMouseEvent'],
            [KeyboardEvent.prototype, 'initKeyboardEvent'],
            [CustomEvent.prototype, 'initCustomEvent']
          ]) {{
            const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
            if (!descriptor || typeof descriptor.value !== 'function') continue;
            const nativeInit = descriptor.value;
            Object.defineProperty(prototype, name, {{...descriptor, value: function(...args) {{
              const result = Reflect.apply(nativeInit, this, args);
              legacyEvents.delete(this);
              return result;
            }}}});
          }}
          const dispatchDescriptor = Object.getOwnPropertyDescriptor(EventTarget.prototype, 'dispatchEvent');
          const nativeDispatch = dispatchDescriptor.value;
          Object.defineProperty(EventTarget.prototype, 'dispatchEvent', {{
            ...dispatchDescriptor,
            value: function dispatchEvent(event) {{
              'use strict';
              if (this instanceof EventTarget && legacyEvents.has(event)) {{
                throw new DOMException("Failed to execute 'dispatchEvent' on 'EventTarget': The event is already being dispatched or was never initialized.", 'InvalidStateError');
              }}
              return Reflect.apply(nativeDispatch, this, arguments);
            }}
          }});

          // Promise-returning Web IDL operation: invalid receivers reject the
          // returned promise rather than throwing into the caller's try/catch.
          // Do not replace Promise or change valid query results.
          const queryDescriptor = Object.getOwnPropertyDescriptor(Permissions.prototype, 'query');
          const nativeQuery = queryDescriptor.value;
          Object.defineProperty(Permissions.prototype, 'query', {{
            ...queryDescriptor,
            value: function query(permissionDesc) {{
              'use strict';
              try {{ return Reflect.apply(nativeQuery, this, arguments); }}
              catch (error) {{
                if (error instanceof TypeError && error.message === 'Illegal invocation') {{
                  error = new TypeError("Failed to execute 'query' on 'Permissions': Illegal invocation");
                }}
                return Promise.reject(error);
              }}
            }}
          }});

          window.__sgss_console = [];
          window.__sgss_errors = [];
          const recordError = value => {{
            if (window.__sgss_errors.length < 20) window.__sgss_errors.push(String(value).slice(0, 500));
          }};
          window.addEventListener('error', event => recordError(event.message || event.error));
          window.addEventListener('unhandledrejection', event => recordError(event.reason));
          window.__sgss_cookie_writes = [];
          window.__sgss_probe = {{
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
          }};
          try {{ window.__sgss_probe.uaData = JSON.parse(JSON.stringify(navigator.userAgentData||null)); }} catch(e) {{}}
          try {{
            const __cookieDesc = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
            if (__cookieDesc && __cookieDesc.set) {{
              Object.defineProperty(Document.prototype, 'cookie', {{
                configurable: true,
                get: __cookieDesc.get,
                set: function(v) {{
                  try {{ window.__sgss_cookie_writes.push(String(v)); }} catch(e) {{}}
                  return __cookieDesc.set.call(this, v);
                }}
              }});
            }}
          }} catch(e) {{}}
          const origLog = console.log ? console.log.bind(console) : function(){{}};
          console.log = function(...args) {{
            try {{ window.__sgss_console.push(args.map(x => String(x)).join(' ')); }} catch(e) {{}}
            try {{ return origLog(...args); }} catch(e) {{}}
          }};
          window.__sgss_read = function() {{
            const ck = String(document.cookie || '');
            const m = ck.match(/(?:^|; )SG_SS=([^;]+)/);
            let fromConsole = '';
            try {{
              for (const line of window.__sgss_console || []) {{
                if (String(line).startsWith('SGSS_RESULT ')) fromConsole = String(line).slice('SGSS_RESULT '.length);
              }}
            }} catch(e) {{}}
            const writes = (window.__sgss_cookie_writes || []).slice(-10);
            // Mirror CDP: explicit result, otherwise CURRENT document.cookie.
            // A historical write (even a longer one) may be replaced/deleted.
            const sg = fromConsole || (m && m[1]) || '';
            return {{href: String(location.href), cookie: ck, sg_ss: sg,
              source: fromConsole ? 'console' : (sg ? 'document.cookie' : ''),
              writes, probe: window.__sgss_probe, errors: window.__sgss_errors.slice()}};
          }};
        }})();
        """

    def generate(self, html: str, page_url: str, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        if not html or not isinstance(html, str):
            raise RuntimeError("missing challenge html")
        timeout_ms = int(timeout_ms or self.opts.timeout_ms)
        t0 = time.perf_counter()
        with self._lock:
            iv8 = self._import_iv8()
            ctx = None
            try:
                with _silence_iv8_stdio():
                    ctx = self._create_context(iv8, page_url)
                    ctx.eval(self._preload_js(), name="sgss_preload.js")
                    # Equivalent to CDP Fetch.fulfillRequest for the main document.
                    snapshot = {"baseURL": page_url, "url": page_url, "html": html}
                    ctx.eval("window.__iv8__.page.load(" + json.dumps(snapshot, ensure_ascii=False) + ");", name="sgss_page_load.js")

                deadline = time.perf_counter() + timeout_ms / 1000.0
                last: Dict[str, Any] = {}
                samples = []
                rounds = 0
                while time.perf_counter() < deadline:
                    rounds += 1
                    with _silence_iv8_stdio():
                        # Process due work only. drain() also runs FUTURE timers
                        # and can clear/replace a completed cookie before it is read.
                        ctx.eval("window.__iv8__.eventLoop.drainMicrotasks();")
                        ctx.eval("window.__iv8__.eventLoop.advance(0);")
                        ctx.eval("window.__iv8__.eventLoop.drainMicrotasks();")
                        last = ctx.eval("window.__sgss_read ? window.__sgss_read() : ({href:String(location.href), cookie:String(document.cookie||''), sg_ss:''})", to_py=True)
                    sg = str((last or {}).get("sg_ss") or "")
                    samples.append({"round": rounds, "len": len(sg), "cookie_len": len(str(last.get("cookie") or "")), "probe": last.get("probe")})
                    samples = samples[-20:]
                    if sg:
                        # SG_SS is produced by the challenge's terminal
                        # callback.  Continuing to drain for the full
                        # timeout can run post-completion timers and makes a
                        # valid token needlessly stale (and used to add
                        # 10–15 seconds to every request).
                        break
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    # IV8 system time freezes while Python is idle. Advance by
                    # the actual wait, not arbitrary future time, and avoid a busy loop.
                    wait_start = time.perf_counter()
                    time.sleep(min(0.05, remaining))
                    elapsed_ms = (time.perf_counter() - wait_start) * 1000
                    with _silence_iv8_stdio():
                        ctx.eval(f"window.__iv8__.eventLoop.advance({elapsed_ms});")

                sg_ss = str((last or {}).get("sg_ss") or "")
                output = {
                    "ok": bool(sg_ss), "sg_ss": sg_ss, "sg_ss_len": len(sg_ss),
                    "href": last.get("href"), "cookie": last.get("cookie"),
                    "writes": last.get("writes", []), "probe": last.get("probe"),
                    "errors": last.get("errors", []), "source": last.get("source", ""),
                    "samples": samples[-20:], "rounds": rounds,
                    "ms": _ms(t0), "engine": "iv8",
                    "iv8_version": str(getattr(iv8, "__version__", "unknown")),
                    "v8_version": str(getattr(iv8, "v8_version", "unknown")),
                    "time_mode": self.opts.time_mode,
                    "requested_icu_locale": self.opts.icu_locale,
                    "compatibility": {
                        "browser_equivalent": False,
                        "adapters": ["legacy_events", "permissions_promise",
                                     "native_uadata", "document_ready_state",
                                     "trusted_policy_callbacks", "non_navigating_anchor_click",
                                     "csi_page_clock", "html_document_scripts", "simple_media_whitespace"],
                        "webgl_enabled": self.opts.webgl_enabled,
                        "known_limits": [
                            *(["IV8 0.1.3 page.load does not expose the executing currentScript"]
                              if str(getattr(iv8, "__version__", "unknown")) == "0.1.3" else []),
                            "IV8 does not produce native navigation timing entries",
                            "scripts adapter covers the main ordinary HTML document, not SVG/XML namespace semantics",
                            "media adapter only normalizes simple keyword queries; viewport/range queries remain incomplete",
                            "Canvas drawing, layout and viewport propagation remain non-equivalent",
                            "WebGL metadata is configured from CDP; IV8 shader rendering/readback remains incomplete",
                            "IV8 WebGPU requestAdapter returns null despite availability configuration",
                            "Trusted Types fallback returns strings, not native trusted objects",
                            "JS adapters retain their real JS source and stack frames",
                            "IV8 inline-script attribution differs from page URL/position",
                            "click adapter covers anchors without href, not form activation/navigation",
                            "ICU locale is process-wide and requires a fresh process if initialized earlier",
                        ],
                    },
                }
                if not sg_ss:
                    # Source text is not evidence that a script actually ran.
                    output["error"] = "timeout/no SG_SS"
                return output
            finally:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:
                        pass


class Iv8SgssEngine:
    """Async facade that keeps all iv8 operations on one OS thread."""

    def __init__(self, opts: Optional[Iv8SgssOptions] = None):
        self.runtime = Iv8SgssRuntime(opts)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="iv8-sgss")
        self._closed = False

    async def generate(self, html: str, page_url: str, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        if self._closed:
            raise RuntimeError("Iv8SgssEngine is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: self.runtime.generate(html, page_url, timeout_ms))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=False)


@dataclass
class SgssConfig:
    timeout: int = 30
    iv8_timeout_ms: int = 10000
    impersonate: str = "chrome133a"
    verify_tls: bool = False


class GoogleSgssClient:
    """Standalone SG_SS client using local IV8 execution.

    HTTP behavior matches the CDP client, but all request methods are defined
    locally. No sibling Python client, Chrome, or CDP service is required.
    """

    _engine_timing_key = "iv8"

    def __init__(self, *, timeout: int = 30, iv8_timeout_ms: int = 10000, cdp_timeout_ms: Optional[int] = None):
        """Create SG_SS client.

        Args:
            timeout: Network request timeout for Google fetch/retry.
            iv8_timeout_ms: IV8 challenge execution timeout.
            cdp_timeout_ms: Backward-compatible alias for iv8_timeout_ms.
        """
        if cdp_timeout_ms is not None:
            iv8_timeout_ms = cdp_timeout_ms
        self.config = SgssConfig(timeout=timeout, iv8_timeout_ms=iv8_timeout_ms)
        self._iv8_runtime = None

    @staticmethod
    def build_headers(url: str) -> Dict[str, str]:
        return {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "available-dictionary": ":5ZzuMsi/NiQlXSx+miNhV5mDFcm2/UoutbFPjwfLwLU=:",
            "cache-control": "no-cache",
            "downlink": "1.3",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": url,
            "rtt": "400",
            "sec-ch-prefers-color-scheme": "light",
            "sec-ch-ua": '"Google Chrome";v="155", "Chromium";v="155", "Not)A;Brand";v="24"',
            "sec-ch-ua-arch": '"arm"',
            "sec-ch-ua-bitness": '"64"',
            "sec-ch-ua-form-factors": '"Desktop"',
            "sec-ch-ua-full-version": '"155.0.7827.104"',
            "sec-ch-ua-full-version-list": '"Google Chrome";v="155.0.7827.104", "Chromium";v="155.0.7827.104", "Not)A;Brand";v="24.0.0.0"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-model": '""',
            "sec-ch-ua-platform": '"macOS"',
            "sec-ch-ua-platform-version": '"14.6.0"',
            "sec-ch-ua-wow64": "?0",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
            "user-agent": DEFAULT_UA,
            "x-browser-channel": "stable",
            "x-browser-copyright": "Copyright 2026 Google LLC. All Rights Reserved.",
            "x-browser-validation": "rDexa0VxSGod7UzQ3NAJdokcges=",
            "x-browser-year": "2026",
            "x-client-data": "CKmdygEIlqHLAQiGoM0BCJHOlDAIls+UMBiLy5QwGIrNlDA=",
        }


    @staticmethod
    def pick_var(html: str, name: str) -> Optional[Any]:
        ms = list(re.finditer(r"var\s+" + re.escape(name) + r"\s*=\s*([\s\S]*?);", html))
        if not ms:
            return None
        raw = ms[-1].group(1).strip()
        if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
            return raw[1:-1].replace(r"\x3d", "=").replace(r"\/", "/")
        try:
            return json.loads(raw)
        except Exception:
            return raw


    @staticmethod
    def is_challenge(html: str) -> bool:
        return "SG_SS" in html and "knitsail" in html


    @staticmethod
    def retry_url(url: str, eid: str) -> str:
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k not in ("sg_ss", "sei", "eid")]
        q.append(("sei", eid))
        return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(q), p.fragment))


    @staticmethod
    def cookie_domain(url: str) -> str:
        host = urlparse(url).hostname or "www.google.com.hk"
        if host.endswith("google.com.hk"):
            return ".google.com.hk"
        if host.endswith("google.com"):
            return ".google.com"
        return host


    @staticmethod
    def _proxy_dict(proxy: Optional[str]) -> Optional[Dict[str, str]]:
        proxy = (proxy or "").strip()
        return {"http": proxy, "https": proxy} if proxy else None


    @classmethod
    def build_navigation_headers(cls, url: str, *, initial: bool = False) -> Dict[str, str]:
        """Compatibility helper; the HTTP flow uses session.headers."""
        return cls.build_headers(url)

    @classmethod
    def extract_enterprise_context(
        cls,
        html: str,
        page_url: str,
    ) -> Dict[str, str]:
        """Extract the binding fields from Google's 429 form response.

        The Enterprise widget on the 429 page is not interchangeable with a
        fresh standalone widget.  In particular, ``data-s`` is forwarded by
        the official SDK as the anchor ``s`` parameter and binds the
        resulting token to this challenge.
        """
        parser = _EnterpriseFormParser()
        parser.feed(str(html or ""))
        parser.close()

        return {
            "site_key": parser.widget.get("data-sitekey") or "",
            "challenge_s": parser.widget.get("data-s") or "",
            "q": parser.hidden.get("q") or "",
            "continue_url": parser.hidden.get("continue") or "",
            "form_url": urljoin(
                str(page_url or ""),
                parser.form_action or "index",
            ),
        }

    @staticmethod
    def is_enterprise_form(html: str) -> bool:
        source = str(html or "")
        return (
            "captcha-form" in source
            and "g-recaptcha" in source
            and "data-sitekey" in source
        )

    @classmethod
    def classify_response(cls, status: int, url: str, html: str) -> str:
        """Describe observable response content, not the unproven cause of 429."""
        if cls.is_enterprise_form(html):
            return "enterprise_form"
        if "/sorry/" in urlparse(url).path:
            return "sorry_page"
        if cls.is_challenge(html):
            return "sg_challenge"
        if status == 429:
            return "http_429"
        if not 200 <= status < 400:
            return "http_error"
        return "ok"

    def generate_sgss_by_iv8(self, html: str, page_url: str) -> Dict[str, Any]:
        if self._iv8_runtime is None:
            self._iv8_runtime = Iv8SgssRuntime(Iv8SgssOptions(timeout_ms=self.config.iv8_timeout_ms, user_agent=DEFAULT_UA))
        data = self._iv8_runtime.generate(html, page_url, self.config.iv8_timeout_ms)
        if not data.get("ok") or not data.get("sg_ss"):
            raise RuntimeError("iv8 SG_SS failed: " + json.dumps(data, ensure_ascii=False)[:1500])
        return data

    @staticmethod
    def enterprise_browser_environment() -> Dict[str, Any]:
        """Match the Enterprise mini-browser to the SG_SS browser profile."""
        brands = [
            {"brand": "Google Chrome", "version": DEFAULT_CHROME_MAJOR},
            {"brand": "Chromium", "version": DEFAULT_CHROME_MAJOR},
            {"brand": "Not)A;Brand", "version": "24"},
        ]
        full_version_list = [
            {"brand": "Google Chrome", "version": DEFAULT_CHROME_FULL_VERSION},
            {"brand": "Chromium", "version": DEFAULT_CHROME_FULL_VERSION},
            {"brand": "Not)A;Brand", "version": "24.0.0.0"},
        ]
        ua_data = {
            "brands": brands,
            "mobile": False,
            "platform": DEFAULT_PLATFORM,
            "architecture": "arm",
            "bitness": "64",
            "model": "",
            "platformVersion": DEFAULT_PLATFORM_VERSION,
            "fullVersionList": full_version_list,
            "uaFullVersion": DEFAULT_CHROME_FULL_VERSION,
            "wow64": False,
            "highEntropyValues": {
                "architecture": "arm",
                "bitness": "64",
                "brands": full_version_list,
                "mobile": False,
                "model": "",
                "platform": DEFAULT_PLATFORM,
                "platformVersion": DEFAULT_PLATFORM_VERSION,
                "uaFullVersion": DEFAULT_CHROME_FULL_VERSION,
                "fullVersionList": full_version_list,
                "wow64": False,
            },
        }
        return {
            "navigator": {
                "userAgent": DEFAULT_UA,
                "appVersion": DEFAULT_UA.replace("Mozilla/", ""),
                "platform": DEFAULT_PLATFORM,
                "vendor": "Google Inc.",
                "language": "zh-CN",
                "languages": ["zh-CN", "zh"],
                "hardwareConcurrency": 8,
                "deviceMemory": 16,
                "maxTouchPoints": 0,
                "webdriver": False,
                "cookieEnabled": True,
                "onLine": True,
                "userAgentData": ua_data,
            },
            "userAgentData": ua_data,
            "window": {
                "innerWidth": 1365,
                "innerHeight": 813,
                "outerWidth": 1365,
                "outerHeight": 900,
                "devicePixelRatio": 1,
            },
            "screen": {
                "width": 800,
                "height": 600,
                "availWidth": 800,
                "availHeight": 600,
                "colorDepth": 24,
                "pixelDepth": 24,
                "availLeft": 0,
                "availTop": 0,
            },
        }


    def _engine_result_fields(self, data: Dict[str, Any], page_url: str) -> Dict[str, Any]:
        return {
            "engine": data.get("engine", "iv8"),
            "engine_ms": data.get("ms"),
            "engine_diagnostics": {
                "iv8_version": data.get("iv8_version"),
                "v8_version": data.get("v8_version"),
                "time_mode": data.get("time_mode"),
                "requested_icu_locale": data.get("requested_icu_locale"),
                "compatibility": data.get("compatibility"),
                "source": data.get("source"),
                "probe": data.get("probe"),
                "errors": data.get("errors", []),
                "rounds": data.get("rounds"),
                "cookie_write_count": len(data.get("writes", [])),
                "href_changed": data.get("href") != page_url,
            },
        }

    def _retry_result_fields(self, response) -> Dict[str, Any]:
        # Diagnostic only: preserve the CDP reference success decision.
        return {
            "retry_kind": self.classify_response(response.status_code, response.url, response.text),
            "retry_text": response.text,
            "retry_headers": {
                "retry_after": response.headers.get("retry-after", ""),
                "content_type": response.headers.get("content-type", ""),
                "location": response.headers.get("location", ""),
                "x_hallmonitor_challenge": response.headers.get("x-hallmonitor-challenge", ""),
            },
        }


    def get_cookie(self, *, proxy: Optional[str], url: str) -> Dict[str, Any]:
        """Fetch challenge, generate SG_SS via IV8, retry once, return cookie info.

        Args:
            proxy: 例如 "http://127.0.0.1:7890"；不需要代理传 "" 或 None。
            url: 目标 Google search URL。

        Returns:
            dict，包括：ok、cookie、cookie_header、sg_ss、domain、path、timings_ms。
        """
        total_t0 = _now()
        timings: Dict[str, int] = {}
        proxy_cfg = self._proxy_dict(proxy)

        session = requests.Session(impersonate=self.config.impersonate, verify=self.config.verify_tls)
        session.headers = self.build_headers(url)

        t0 = _now()
        r1 = session.get(url, proxies=proxy_cfg, timeout=self.config.timeout)
        timings["first_request"] = _ms(t0)

        result: Dict[str, Any] = {
            "ok": False,
            "input_url": url,
            "first_status": r1.status_code,
            "first_url": r1.url,
            "is_challenge": self.is_challenge(r1.text),
            "timings_ms": timings,
        }

        if not self.is_challenge(r1.text):
            # 没遇到 SG challenge，则没有 SG_SS 可返回。
            result.update({
                "error": "not SG challenge",
                "total_ms": _ms(total_t0),
                "cookies": session.cookies.get_dict(),
                "cookie_header": "; ".join(f"{k}={v}" for k, v in session.cookies.get_dict().items()),
            })
            return result

        eid = self.pick_var(r1.text, "eid") or ""
        result["eid"] = eid

        t0 = _now()
        iv8_out = self.generate_sgss_by_iv8(r1.text, r1.url)
        timings[self._engine_timing_key] = _ms(t0)
        sg_ss = iv8_out["sg_ss"]

        domain = self.cookie_domain(r1.url)
        path = "/"
        session.cookies.set("SG_SS", sg_ss, domain=domain, path=path)

        t0 = _now()
        r2 = session.get(self.retry_url(r1.url, str(eid)), proxies=proxy_cfg, timeout=self.config.timeout, allow_redirects=True)
        timings["retry_request"] = _ms(t0)

        retry_ok = "/sorry/" not in r2.url and r2.status_code not in (403, 429) and not self.is_challenge(r2.text)
        cookies = session.cookies.get_dict()
        timings["total"] = _ms(total_t0)

        result.update({
            "ok": bool(retry_ok),
            "retry_status": r2.status_code,
            "retry_url": r2.url,
            "sg_ss": sg_ss,
            "sg_ss_len": len(sg_ss),
            "domain": domain,
            "path": path,
            "cookies": cookies,
            "timings_ms": timings,
            "total_ms": timings["total"],
        })
        result.update(self._engine_result_fields(iv8_out, r1.url))
        result.update(self._retry_result_fields(r2))
        return result



# ------------------------- 右键运行配置 -------------------------
tag_url = "https://www.google.com.hk/search?newwindow=1&udm=2&q=iphone&sa=X"
proxy = "http://127.0.0.1:7890"



def main() -> Dict[str, Any]:
    client = GoogleSgssClient()
    result = client.get_cookie(
        proxy=proxy,
        url=tag_url,
    )
    print("\n" + json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
