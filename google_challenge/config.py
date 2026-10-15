"""全包配置入口；直接编辑本文件，不读取环境变量。

修改后重新启动 Python 进程。接口参数可覆盖单次调用的默认值。
浏览器环境是实验用快照，不表示本机实际硬件。
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PACKAGE_DIR / "runtime"
OUTPUT_DIR = PACKAGE_DIR / "output"
RESULT_DIR = OUTPUT_DIR / "results"
TEMP_DIR = OUTPUT_DIR / "tmp"
CACHE_DIR = OUTPUT_DIR / "cache"
CHALLENGE_ROOT = OUTPUT_DIR / "challenges"
RESULT_FILE = RESULT_DIR / "enterprise_result.json"
DETAIL_FILE = RESULT_DIR / "enterprise_detail.json"
RUNNER_FILE = RUNTIME_DIR / "recaptcha_runner.js"
CHROME_RUNNER_FILE = RUNTIME_DIR / "chrome_service.js"

NODE_EXE = None  # 可填写 Node.js 的绝对路径；None 表示从 PATH 查找。
CHROME_PATH = None  # 可填写 Chrome 的绝对路径；None 表示检查常见安装目录。
PROXY = None  # 例如 http://127.0.0.1:7897；None 表示直连。
VERIFY_TLS = True
HTTP_TIMEOUT = 30
CONSOLE_ENCODING = "utf-8"  # 统一终端、重定向和日志管道的输出编码。
WINDOWS_CONSOLE_CODE_PAGE = 65001
SGSS_ENGINE = "iv8"  # 可选 iv8 或 chrome。
SGSS_TIMEOUT_MS = 10000
HTTP_IMPERSONATE = "chrome133a"
ENTERPRISE_IMPERSONATE = "chrome146"

RPC_HOST = "127.0.0.1"
RPC_PORT = 4083
CDP_PORT = 9491
RPC_TOKEN = ""
RPC_MAX_JOBS = 1
RPC_MAX_BODY_BYTES = 8 * 1024 * 1024
CHROME_START_TIMEOUT = 15

IV8_VERBOSE = False
IV8_TIME_MODE = "system"
IV8_ICU_LOCALE = "zh_CN.UTF-8"
IV8_WEBGL_ENABLED = True
IV8_INNER_WIDTH = 1365
IV8_INNER_HEIGHT = 813
IV8_OUTER_WIDTH = 1365
IV8_OUTER_HEIGHT = 900
IV8_DEVICE_PIXEL_RATIO = 1
IV8_LANGUAGE = "zh-CN"
IV8_LANGUAGES = ("zh-CN", "zh")
IV8_CONNECTION_DOWNLINK = 1.5
IV8_CONNECTION_RTT = 200
IV8_CONNECTION_EFFECTIVE_TYPE = "4g"
DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
DEFAULT_PLATFORM = "macOS"
DEFAULT_PLATFORM_VERSION = "14.6.0"
DEFAULT_CHROME_MAJOR = "155"
DEFAULT_CHROME_FULL_VERSION = "155.0.7827.104"
DEFAULT_GPU_VENDOR = "Google Inc. (Apple)"
DEFAULT_GPU_RENDERER = "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)"

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
DEFAULT_SITE_KEY = "6LdLLIMbAAAAAIl-KLj9p1ePhM-4LCCDbjtJLqRO"
DEFAULT_ORIGIN = "https://www.google.com"
ENTERPRISE_TIMEOUT = 600
ENTERPRISE_TRANSPORT = "requests"
ENTERPRISE_REFRESH_CACHE = False
ENTERPRISE_QUIET = False
ENTERPRISE_DEBUG_ENCODER = False
ENTERPRISE_PRINT_RESPONSE = False
ENTERPRISE_NETWORK_TIMEOUT_MS = 90000
ENTERPRISE_ANSWER_TIMEOUT_MS = 3600000
ENTERPRISE_MAX_REDIRECTS = 5

# 浏览器环境快照集中于此，不另外读取配置文件。
FINGERPRINT = {
    "environment": {
        "navigator": {
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36",
            "appVersion": "5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36",
            "platform": "Win32",
            "language": "zh-CN",
            "languages": ["zh-CN", "zh"],
            "hardwareConcurrency": 32,
            "deviceMemory": 32,
            "maxTouchPoints": 0,
            "vendor": "Google Inc.",
            "webdriver": False,
            "cookieEnabled": True,
            "pdfViewerEnabled": True,
            "doNotTrack": None,
            "appCodeName": "Mozilla",
            "appName": "Netscape",
            "product": "Gecko",
            "productSub": "20030107",
            "vendorSub": "",
            "onLine": True,
        },
        "userAgentData": {
            "brands": [
                {"brand": "Google Chrome", "version": "149"},
                {"brand": "Chromium", "version": "149"},
                {"brand": "Not)A;Brand", "version": "24"},
            ],
            "mobile": False,
            "platform": "Windows",
            "highEntropyValues": {
                "architecture": "x86",
                "bitness": "64",
                "brands": [
                    {"brand": "Google Chrome", "version": "149"},
                    {"brand": "Chromium", "version": "149"},
                    {"brand": "Not)A;Brand", "version": "24"},
                ],
                "fullVersionList": [
                    {"brand": "Google Chrome", "version": "149.0.7827.201"},
                    {"brand": "Chromium", "version": "149.0.7827.201"},
                    {"brand": "Not)A;Brand", "version": "24.0.0.0"},
                ],
                "mobile": False,
                "model": "",
                "platform": "Windows",
                "platformVersion": "19.0.0",
                "uaFullVersion": "149.0.7827.201",
                "wow64": False,
            },
        },
        "window": {
            "innerWidth": 1351,
            "innerHeight": 806,
            "outerWidth": 1366,
            "outerHeight": 900,
            "screenX": 80,
            "screenY": 40,
            "screenLeft": 80,
            "screenTop": 40,
            "devicePixelRatio": 1.5,
            "isSecureContext": True,
            "crossOriginIsolated": False,
        },
        "screen": {
            "width": 1707,
            "height": 1067,
            "availWidth": 1707,
            "availHeight": 1019,
            "availLeft": 0,
            "availTop": 0,
            "colorDepth": 32,
            "pixelDepth": 32,
            "isExtended": False,
            "orientation": {"angle": 0, "type": "landscape-primary"},
        },
        "visualViewport": {
            "width": 1351.3333740234375,
            "height": 790.6666870117188,
            "scale": 1,
            "offsetLeft": 0,
            "offsetTop": 0,
            "pageLeft": 0,
            "pageTop": 0,
        },
        "plugins": {
            "items": [
                {
                    "name": "PDF Viewer",
                    "filename": "internal-pdf-viewer",
                    "description": "Portable Document Format",
                    "mimeTypes": [
                        {
                            "type": "application/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                        {
                            "type": "text/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                    ],
                },
                {
                    "name": "Chrome PDF Viewer",
                    "filename": "internal-pdf-viewer",
                    "description": "Portable Document Format",
                    "mimeTypes": [
                        {
                            "type": "application/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                        {
                            "type": "text/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                    ],
                },
                {
                    "name": "Chromium PDF Viewer",
                    "filename": "internal-pdf-viewer",
                    "description": "Portable Document Format",
                    "mimeTypes": [
                        {
                            "type": "application/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                        {
                            "type": "text/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                    ],
                },
                {
                    "name": "Microsoft Edge PDF Viewer",
                    "filename": "internal-pdf-viewer",
                    "description": "Portable Document Format",
                    "mimeTypes": [
                        {
                            "type": "application/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                        {
                            "type": "text/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                    ],
                },
                {
                    "name": "WebKit built-in PDF",
                    "filename": "internal-pdf-viewer",
                    "description": "Portable Document Format",
                    "mimeTypes": [
                        {
                            "type": "application/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                        {
                            "type": "text/pdf",
                            "suffixes": "pdf",
                            "description": "Portable Document Format",
                        },
                    ],
                },
            ]
        },
        "mediaDevices": {
            "supportedConstraints": {
                "aspectRatio": True,
                "autoGainControl": True,
                "brightness": True,
                "channelCount": True,
                "colorTemperature": True,
                "contrast": True,
                "deviceId": True,
                "displaySurface": True,
                "echoCancellation": True,
                "exposureCompensation": True,
                "exposureMode": True,
                "exposureTime": True,
                "facingMode": True,
                "focusDistance": True,
                "focusMode": True,
                "frameRate": True,
                "groupId": True,
                "height": True,
                "iso": True,
                "latency": True,
                "noiseSuppression": True,
                "pan": True,
                "pointsOfInterest": True,
                "resizeMode": True,
                "restrictOwnAudio": True,
                "sampleRate": True,
                "sampleSize": True,
                "saturation": True,
                "sharpness": True,
                "suppressLocalAudioPlayback": True,
                "tilt": True,
                "torch": True,
                "voiceIsolation": True,
                "whiteBalanceMode": True,
                "width": True,
                "zoom": True,
            },
            "devices": [
                {"deviceId": "", "kind": "audioinput", "label": "", "groupId": ""},
                {"deviceId": "", "kind": "videoinput", "label": "", "groupId": ""},
                {"deviceId": "", "kind": "audiooutput", "label": "", "groupId": ""},
            ],
        },
        "storageQuota": {"quota": 10737418240, "usage": 0, "usageDetails": {}, "persisted": False},
        "permissions": {
            "states": {
                "geolocation": "prompt",
                "notifications": "prompt",
                "midi": "prompt",
                "camera": "prompt",
                "microphone": "prompt",
                "background-sync": "granted",
                "accelerometer": "granted",
                "gyroscope": "granted",
                "magnetometer": "granted",
                "clipboard-read": "prompt",
                "clipboard-write": "granted",
                "payment-handler": "granted",
                "persistent-storage": "prompt",
            },
            "errors": {
                "push": {
                    "name": "NotSupportedError",
                    "message": "Failed to execute "
                    "'query' on "
                    "'Permissions': Push "
                    "Permission without "
                    "userVisibleOnly:true "
                    "isn't supported yet.",
                    "stackMessage": "Push Permission "
                    "without "
                    "userVisibleOnly:true "
                    "isn't supported "
                    "yet.",
                },
                "ambient-light-sensor": {
                    "name": "TypeError",
                    "message": "Failed "
                    "to "
                    "execute "
                    "'query' "
                    "on "
                    "'Permissions': "
                    "GenericSensorExtraClasses "
                    "flag "
                    "is "
                    "not "
                    "enabled.",
                    "stackMessage": "GenericSensorExtraClasses flag is not enabled.",
                },
            },
        },
        "performance": {
            "memory": {
                "jsHeapSizeLimit": 4395630592,
                "totalJSHeapSize": 22686491,
                "usedJSHeapSize": 12761919,
            },
            "resourceTimingBufferSize": 250,
        },
        "webgl": {
            "supportedExtensions": [
                "ANGLE_instanced_arrays",
                "EXT_blend_minmax",
                "EXT_clip_control",
                "EXT_color_buffer_half_float",
                "EXT_depth_clamp",
                "EXT_disjoint_timer_query",
                "EXT_float_blend",
                "EXT_frag_depth",
                "EXT_polygon_offset_clamp",
                "EXT_shader_texture_lod",
                "EXT_texture_compression_bptc",
                "EXT_texture_compression_rgtc",
                "EXT_texture_filter_anisotropic",
                "EXT_texture_mirror_clamp_to_edge",
                "EXT_sRGB",
                "KHR_parallel_shader_compile",
                "OES_element_index_uint",
                "OES_fbo_render_mipmap",
                "OES_standard_derivatives",
                "OES_texture_float",
                "OES_texture_float_linear",
                "OES_texture_half_float",
                "OES_texture_half_float_linear",
                "OES_vertex_array_object",
                "WEBGL_blend_func_extended",
                "WEBGL_color_buffer_float",
                "WEBGL_compressed_texture_s3tc",
                "WEBGL_compressed_texture_s3tc_srgb",
                "WEBGL_debug_renderer_info",
                "WEBGL_debug_shaders",
                "WEBGL_depth_texture",
                "WEBGL_draw_buffers",
                "WEBGL_lose_context",
                "WEBGL_multi_draw",
                "WEBGL_polygon_mode",
            ],
            "contextAttributes": {
                "alpha": True,
                "antialias": True,
                "depth": True,
                "desynchronized": False,
                "failIfMajorPerformanceCaveat": False,
                "powerPreference": "default",
                "premultipliedAlpha": True,
                "preserveDrawingBuffer": False,
                "stencil": False,
                "xrCompatible": False,
            },
            "VENDOR": "WebKit",
            "RENDERER": "WebKit WebGL",
            "VERSION": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
            "SHADING_LANGUAGE_VERSION": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
            "MAX_TEXTURE_SIZE": 16384,
            "MAX_CUBE_MAP_TEXTURE_SIZE": 16384,
            "MAX_TEXTURE_IMAGE_UNITS": 16,
            "MAX_COMBINED_TEXTURE_IMAGE_UNITS": 32,
            "MAX_VERTEX_TEXTURE_IMAGE_UNITS": 16,
            "MAX_VERTEX_ATTRIBS": 16,
            "MAX_VERTEX_UNIFORM_VECTORS": 4095,
            "MAX_VARYING_VECTORS": 30,
            "MAX_FRAGMENT_UNIFORM_VECTORS": 1024,
            "MAX_RENDERBUFFER_SIZE": 16384,
            "MAX_VIEWPORT_DIMS": [32767, 32767],
            "RED_BITS": 8,
            "GREEN_BITS": 8,
            "BLUE_BITS": 8,
            "ALPHA_BITS": 8,
            "DEPTH_BITS": 24,
            "STENCIL_BITS": 0,
            "SUBPIXEL_BITS": 4,
            "ALIASED_LINE_WIDTH_RANGE": [1, 1],
            "ALIASED_POINT_SIZE_RANGE": [1, 1024],
            "UNMASKED_VENDOR_WEBGL": "Google Inc. (NVIDIA)",
            "UNMASKED_RENDERER_WEBGL": "ANGLE (NVIDIA, NVIDIA GeForce "
            "RTX 5060 Laptop GPU "
            "(0x00002D59) Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)",
        },
        "webgl2": {
            "supportedExtensions": [
                "EXT_clip_control",
                "EXT_color_buffer_float",
                "EXT_color_buffer_half_float",
                "EXT_conservative_depth",
                "EXT_depth_clamp",
                "EXT_disjoint_timer_query_webgl2",
                "EXT_float_blend",
                "EXT_polygon_offset_clamp",
                "EXT_render_snorm",
                "EXT_texture_compression_bptc",
                "EXT_texture_compression_rgtc",
                "EXT_texture_filter_anisotropic",
                "EXT_texture_mirror_clamp_to_edge",
                "EXT_texture_norm16",
                "KHR_parallel_shader_compile",
                "NV_shader_noperspective_interpolation",
                "OES_draw_buffers_indexed",
                "OES_sample_variables",
                "OES_shader_multisample_interpolation",
                "OES_texture_float_linear",
                "OVR_multiview2",
                "WEBGL_blend_func_extended",
                "WEBGL_clip_cull_distance",
                "WEBGL_compressed_texture_s3tc",
                "WEBGL_compressed_texture_s3tc_srgb",
                "WEBGL_debug_renderer_info",
                "WEBGL_debug_shaders",
                "WEBGL_lose_context",
                "WEBGL_multi_draw",
                "WEBGL_polygon_mode",
                "WEBGL_provoking_vertex",
                "WEBGL_stencil_texturing",
            ],
            "contextAttributes": {
                "alpha": True,
                "antialias": True,
                "depth": True,
                "desynchronized": False,
                "failIfMajorPerformanceCaveat": False,
                "powerPreference": "default",
                "premultipliedAlpha": True,
                "preserveDrawingBuffer": False,
                "stencil": False,
                "xrCompatible": False,
            },
            "VENDOR": "WebKit",
            "RENDERER": "WebKit WebGL",
            "VERSION": "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
            "SHADING_LANGUAGE_VERSION": "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)",
            "MAX_TEXTURE_SIZE": 16384,
            "MAX_CUBE_MAP_TEXTURE_SIZE": 16384,
            "MAX_TEXTURE_IMAGE_UNITS": 16,
            "MAX_COMBINED_TEXTURE_IMAGE_UNITS": 32,
            "MAX_VERTEX_TEXTURE_IMAGE_UNITS": 16,
            "MAX_VERTEX_ATTRIBS": 16,
            "MAX_VERTEX_UNIFORM_VECTORS": 4095,
            "MAX_VARYING_VECTORS": 30,
            "MAX_FRAGMENT_UNIFORM_VECTORS": 1024,
            "MAX_RENDERBUFFER_SIZE": 16384,
            "MAX_VIEWPORT_DIMS": [32767, 32767],
            "RED_BITS": 8,
            "GREEN_BITS": 8,
            "BLUE_BITS": 8,
            "ALPHA_BITS": 8,
            "DEPTH_BITS": 24,
            "STENCIL_BITS": 0,
            "SUBPIXEL_BITS": 4,
            "ALIASED_LINE_WIDTH_RANGE": [1, 1],
            "ALIASED_POINT_SIZE_RANGE": [1, 1024],
            "UNMASKED_VENDOR_WEBGL": "Google Inc. (NVIDIA)",
            "UNMASKED_RENDERER_WEBGL": "ANGLE (NVIDIA, NVIDIA GeForce "
            "RTX 5060 Laptop GPU "
            "(0x00002D59) Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)",
        },
        "fonts": {
            "measureText": [
                {
                    "font": "10px sans-serif",
                    "text": "mmmmmmmmmmlli",
                    "metrics": {
                        "width": 101.02993774414062,
                        "actualBoundingBoxAscent": 8,
                        "actualBoundingBoxDescent": 1,
                        "fontBoundingBoxAscent": 12,
                        "fontBoundingBoxDescent": 3,
                    },
                },
                {
                    "font": "10px sans-serif",
                    "text": "reCAPTCHA",
                    "metrics": {
                        "width": 53.209197998046875,
                        "actualBoundingBoxAscent": 8,
                        "actualBoundingBoxDescent": 1,
                        "fontBoundingBoxAscent": 12,
                        "fontBoundingBoxDescent": 3,
                    },
                },
                {
                    "font": "10px sans-serif",
                    "text": "examples/v3scores",
                    "metrics": {
                        "width": 88.63638305664062,
                        "actualBoundingBoxAscent": 8,
                        "actualBoundingBoxDescent": 3,
                        "fontBoundingBoxAscent": 12,
                        "fontBoundingBoxDescent": 3,
                    },
                },
                {
                    "font": "16px Arial",
                    "text": "mmmmmmmmmmlli",
                    "metrics": {
                        "width": 143.9453125,
                        "actualBoundingBoxAscent": 12,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 14,
                        "fontBoundingBoxDescent": 3,
                    },
                },
                {
                    "font": "16px Arial",
                    "text": "reCAPTCHA",
                    "metrics": {
                        "width": 90.6796875,
                        "actualBoundingBoxAscent": 12,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 14,
                        "fontBoundingBoxDescent": 3,
                    },
                },
                {
                    "font": "16px Arial",
                    "text": "examples/v3scores",
                    "metrics": {
                        "width": 136.9453125,
                        "actualBoundingBoxAscent": 12,
                        "actualBoundingBoxDescent": 3,
                        "fontBoundingBoxAscent": 14,
                        "fontBoundingBoxDescent": 3,
                    },
                },
                {
                    "font": '16px "Microsoft YaHei"',
                    "text": "mmmmmmmmmmlli",
                    "metrics": {
                        "width": 162.6953125,
                        "actualBoundingBoxAscent": 13,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 17,
                        "fontBoundingBoxDescent": 4,
                    },
                },
                {
                    "font": '16px "Microsoft YaHei"',
                    "text": "reCAPTCHA",
                    "metrics": {
                        "width": 89.4453125,
                        "actualBoundingBoxAscent": 12,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 17,
                        "fontBoundingBoxDescent": 4,
                    },
                },
                {
                    "font": '16px "Microsoft YaHei"',
                    "text": "examples/v3scores",
                    "metrics": {
                        "width": 144.5703125,
                        "actualBoundingBoxAscent": 13,
                        "actualBoundingBoxDescent": 4,
                        "fontBoundingBoxAscent": 17,
                        "fontBoundingBoxDescent": 4,
                    },
                },
                {
                    "font": "16px serif",
                    "text": "mmmmmmmmmmlli",
                    "metrics": {
                        "width": 171.83998107910156,
                        "actualBoundingBoxAscent": 13,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 18,
                        "fontBoundingBoxDescent": 5,
                    },
                },
                {
                    "font": "16px serif",
                    "text": "reCAPTCHA",
                    "metrics": {
                        "width": 95.2540283203125,
                        "actualBoundingBoxAscent": 12,
                        "actualBoundingBoxDescent": 1,
                        "fontBoundingBoxAscent": 18,
                        "fontBoundingBoxDescent": 5,
                    },
                },
                {
                    "font": "16px serif",
                    "text": "examples/v3scores",
                    "metrics": {
                        "width": 146.1844940185547,
                        "actualBoundingBoxAscent": 13,
                        "actualBoundingBoxDescent": 5,
                        "fontBoundingBoxAscent": 18,
                        "fontBoundingBoxDescent": 5,
                    },
                },
                {
                    "font": "16px monospace",
                    "text": "mmmmmmmmmmlli",
                    "metrics": {
                        "width": 104,
                        "actualBoundingBoxAscent": 11,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 14,
                        "fontBoundingBoxDescent": 2,
                    },
                },
                {
                    "font": "16px monospace",
                    "text": "reCAPTCHA",
                    "metrics": {
                        "width": 72,
                        "actualBoundingBoxAscent": 11,
                        "actualBoundingBoxDescent": 0,
                        "fontBoundingBoxAscent": 14,
                        "fontBoundingBoxDescent": 2,
                    },
                },
                {
                    "font": "16px monospace",
                    "text": "examples/v3scores",
                    "metrics": {
                        "width": 136,
                        "actualBoundingBoxAscent": 12,
                        "actualBoundingBoxDescent": 2,
                        "fontBoundingBoxAscent": 14,
                        "fontBoundingBoxDescent": 2,
                    },
                },
            ]
        },
    }
}

# SG_SS 请求头及模拟浏览器环境的完整基线。
SGSS_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "available-dictionary": ":5ZzuMsi/NiQlXSx+miNhV5mDFcm2/UoutbFPjwfLwLU=:",
    "cache-control": "no-cache",
    "downlink": "1.3",
    "pragma": "no-cache",
    "priority": "u=0, i",
    "rtt": "400",
    "sec-ch-prefers-color-scheme": "light",
    "sec-ch-ua": '"Google Chrome";v="155", "Chromium";v="155", "Not)A;Brand";v="24"',
    "sec-ch-ua-arch": '"arm"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-form-factors": '"Desktop"',
    "sec-ch-ua-full-version": '"155.0.7827.104"',
    "sec-ch-ua-full-version-list": '"Google Chrome";v="155.0.7827.104", '
    '"Chromium";v="155.0.7827.104", "Not)A;Brand";v="24.0.0.0"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"macOS"',
    "sec-ch-ua-platform-version": '"14.6.0"',
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like "
    "Gecko) Chrome/155.0.0.0 Safari/537.36",
    "x-browser-channel": "stable",
    "x-browser-copyright": "Copyright 2026 Google LLC. All Rights Reserved.",
    "x-browser-validation": "rDexa0VxSGod7UzQ3NAJdokcges=",
    "x-browser-year": "2026",
    "x-client-data": "CKmdygEIlqHLAQiGoM0BCJHOlDAIls+UMBiLy5QwGIrNlDA=",
}

SGSS_BROWSER_ENVIRONMENT = {
    "navigator": {
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
        "appVersion": "5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, "
        "like Gecko) Chrome/155.0.0.0 Safari/537.36",
        "platform": "macOS",
        "vendor": "Google Inc.",
        "language": "zh-CN",
        "languages": ["zh-CN", "zh"],
        "hardwareConcurrency": 8,
        "deviceMemory": 16,
        "maxTouchPoints": 0,
        "webdriver": False,
        "cookieEnabled": True,
        "onLine": True,
        "product": "Gecko",
        "productSub": "20030107",
        "pdfViewerEnabled": True,
        "userAgentData": {
            "brands": [
                {"brand": "Google Chrome", "version": "155"},
                {"brand": "Chromium", "version": "155"},
                {"brand": "Not)A;Brand", "version": "24"},
            ],
            "uaFullVersion": "155.0.7827.104",
            "platform": "macOS",
            "platformVersion": "14.6.0",
            "architecture": "arm",
            "bitness": "64",
            "model": "",
            "mobile": False,
            "wow64": False,
        },
        "connection": {"downlink": 1.5, "rtt": 200, "effectiveType": "4g", "saveData": False},
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
    "window": {
        "innerWidth": 1365,
        "innerHeight": 813,
        "outerWidth": 1365,
        "outerHeight": 900,
        "devicePixelRatio": 1,
    },
    "webgl": {
        "VENDOR": "WebKit",
        "RENDERER": "WebKit WebGL",
        "VERSION": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
        "SHADING_LANGUAGE_VERSION": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
        "MAX_TEXTURE_SIZE": 16384,
        "MAX_CUBE_MAP_TEXTURE_SIZE": 16384,
        "MAX_RENDERBUFFER_SIZE": 16384,
        "MAX_VERTEX_ATTRIBS": 16,
        "MAX_VERTEX_TEXTURE_IMAGE_UNITS": 16,
        "MAX_TEXTURE_IMAGE_UNITS": 16,
        "MAX_COMBINED_TEXTURE_IMAGE_UNITS": 32,
        "MAX_VIEWPORT_DIMS": {"0": 16384, "1": 16384},
        "ALIASED_LINE_WIDTH_RANGE": {"0": 1, "1": 1},
        "ALIASED_POINT_SIZE_RANGE": {"0": 1, "1": 511},
        "RED_BITS": 8,
        "GREEN_BITS": 8,
        "BLUE_BITS": 8,
        "ALPHA_BITS": 8,
        "DEPTH_BITS": 24,
        "STENCIL_BITS": 0,
        "MAX_VERTEX_UNIFORM_VECTORS": 1024,
        "MAX_FRAGMENT_UNIFORM_VECTORS": 1024,
        "MAX_VARYING_VECTORS": 30,
        "SUBPIXEL_BITS": 4,
        "UNMASKED_VENDOR_WEBGL": "Google Inc. (Apple)",
        "UNMASKED_RENDERER_WEBGL": "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified "
        "Version)",
    },
    "webgpu": {
        "adapterInfo": {
            "vendor": "apple",
            "architecture": "metal-3",
            "device": "",
            "description": "",
        }
    },
    "history": {"length": 2},
}

SGSS_UA_DATA = {
    "brands": [
        {"brand": "Google Chrome", "version": "155"},
        {"brand": "Chromium", "version": "155"},
        {"brand": "Not)A;Brand", "version": "24"},
    ],
    "mobile": False,
    "platform": "macOS",
    "architecture": "arm",
    "bitness": "64",
    "model": "",
    "platformVersion": "14.6.0",
    "fullVersionList": [
        {"brand": "Google Chrome", "version": "155.0.7827.104"},
        {"brand": "Chromium", "version": "155.0.7827.104"},
        {"brand": "Not)A;Brand", "version": "24.0.0.0"},
    ],
    "uaFullVersion": "155.0.7827.104",
    "wow64": False,
    "highEntropyValues": {
        "architecture": "arm",
        "bitness": "64",
        "brands": [
            {"brand": "Google Chrome", "version": "155.0.7827.104"},
            {"brand": "Chromium", "version": "155.0.7827.104"},
            {"brand": "Not)A;Brand", "version": "24.0.0.0"},
        ],
        "mobile": False,
        "model": "",
        "platform": "macOS",
        "platformVersion": "14.6.0",
        "uaFullVersion": "155.0.7827.104",
        "fullVersionList": [
            {"brand": "Google Chrome", "version": "155.0.7827.104"},
            {"brand": "Chromium", "version": "155.0.7827.104"},
            {"brand": "Not)A;Brand", "version": "24.0.0.0"},
        ],
        "wow64": False,
    },
}

IMAGE_CELL_SIZE = 180
ENTERPRISE_PAYLOAD_TIMEOUT = 45
ENTERPRISE_FORWARD_TIMEOUT = 90
