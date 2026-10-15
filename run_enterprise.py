"""PyCharm 直接运行：真实 Enterprise 协议及人工图片交互。"""

from google_challenge import EnterpriseUserVerifyClient, config
from google_challenge.debug import DebugRun, IDEAnswerProvider

# 独立组件可直接使用默认站点配置；真实挑战需填入本次页面的绑定参数。
SITE_KEY = config.DEFAULT_SITE_KEY
ORIGIN = config.DEFAULT_ORIGIN
CHALLENGE_S = None
PAGE_URL = None
PROXY = config.PROXY
TRANSPORT = config.ENTERPRISE_TRANSPORT
TIMEOUT_SECONDS = config.ENTERPRISE_TIMEOUT
OPEN_CHALLENGE_IMAGES = True
PRINT_FULL_RESULT = False
QUIET = False
DEBUG_ENCODER = False


def main() -> dict:
    """通过 IDE 控制台作答，查看真实 SDK 请求和最终 userverify 响应。"""
    with DebugRun("enterprise") as debug:
        with IDEAnswerProvider(debug, open_images=OPEN_CHALLENGE_IMAGES) as answers:
            with EnterpriseUserVerifyClient(
                site_key=SITE_KEY,
                origin=ORIGIN,
                challenge_s=CHALLENGE_S,
                page_url=PAGE_URL,
                proxy=PROXY,
                transport=TRANSPORT,
                timeout=TIMEOUT_SECONDS,
                quiet=QUIET,
                debug_encoder=DEBUG_ENCODER,
                challenge_dir=debug.directory / "challenges",
            ) as client:
                # 在 solve 内可跟踪网络请求；答案入口为 IDEAnswerProvider.__call__。
                result = client.solve(answer_provider=answers)
        debug.finish(result, print_full=PRINT_FULL_RESULT)
        return result


if __name__ == "__main__":
    result = main()
