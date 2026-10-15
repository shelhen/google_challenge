"""PyCharm 直接运行：读取当前挑战、人工验证、提交业务表单并保存 HTML。"""

from google_challenge import GoogleChallengeFlow, config
from google_challenge.debug import DebugRun, IDEAnswerProvider

# 填入当前可用的 Google 挑战页或目标地址；普通页面不会强行发起验证码。
TARGET_URL = "https://www.google.com/sorry/index?continue=https://www.google.com.hk/search%3Fq%3Diphone%26udm%3D2%26sei%3D1pXQau6yGPCM8L0Ppf3voAs&q=EhAmEAFQwAEABQQy7wapPZXbGNerwtYGIjB4OAH3iV_L6-kJJ1Si0C3MYmA1-UgsG-9EvCFR6OWxlwmtJSwvYLhjv-wpZVs8SM8yAVJaAUNg363o19azj5_3AQ"
PROXY = config.PROXY
OPEN_CHALLENGE_IMAGES = True
PRINT_FULL_RESULT = False


def main() -> dict:
    """复用当前会话完成业务提交，保留完整响应和最终页面供调试。"""
    with DebugRun("business") as debug:
        with IDEAnswerProvider(debug, open_images=OPEN_CHALLENGE_IMAGES) as answers:
            with GoogleChallengeFlow(proxy=PROXY, on_event=debug.record) as flow:
                result = flow.run(
                    TARGET_URL,
                    answer_provider=answers,
                    output_file=debug.directory / "final_response.html",
                )
        debug.finish(result, print_full=PRINT_FULL_RESULT)
        return result


if __name__ == "__main__":
    result = main()
