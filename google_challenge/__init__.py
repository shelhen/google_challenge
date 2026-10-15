"""Google 挑战执行工具；导入时不会联网或启动浏览器。"""

from .sgss.chrome import BrowserRpcError, ChromeService, ChromeSgssEngine
from .sgss.client import GoogleSgssClient

__all__ = [
    "GoogleSgssClient",
    "ChromeSgssEngine",
    "ChromeService",
    "BrowserRpcError",
    "EnterpriseUserVerifyClient",
    "EnterpriseUserVerifyError",
    "GoogleChallengeFlow",
    "Iv8SgssOptions",
    "Iv8SgssRuntime",
    "Iv8SgssEngine",
]


def __getattr__(name: str):
    """延迟加载 Enterprise 图片依赖和业务接口。"""
    if name in {"Iv8SgssOptions", "Iv8SgssRuntime", "Iv8SgssEngine"}:
        from .sgss import iv8

        return getattr(iv8, name)
    if name == "EnterpriseUserVerifyClient":
        from .enterprise.client import EnterpriseUserVerifyClient

        return EnterpriseUserVerifyClient
    if name == "EnterpriseUserVerifyError":
        from .enterprise.protocol import EnterpriseUserVerifyError

        return EnterpriseUserVerifyError
    if name == "GoogleChallengeFlow":
        from .flow import GoogleChallengeFlow

        return GoogleChallengeFlow
    raise AttributeError(name)
