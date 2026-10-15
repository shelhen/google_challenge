"""验证码响应解析和最终协议证据校验。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class EnterpriseUserVerifyError(RuntimeError):
    """表示协议响应无效或流程无法继续的异常。"""


def parse_userverify_response(text: str) -> dict[str, Any]:
    """解析验证响应，区分最终令牌和后续挑战。"""
    raw = str(text or "").strip()
    if raw.startswith(")]}'"):
        raw = raw[4:].lstrip()
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise EnterpriseUserVerifyError("userverify 响应不是有效 JSON") from exc
    kind = str(value[0]) if isinstance(value, list) and value else ""
    token = value[1] if kind == "uvresp" and len(value) > 1 and isinstance(value[1], str) else ""
    code = value[2] if isinstance(value, list) and len(value) > 2 else None
    has_next = (
        kind == "uvresp"
        and len(value) > 7
        and isinstance(value[7], list)
        and bool(value[7])
        and (value[7][0] == "rresp")
    )
    return {
        "kind": kind,
        "token": token,
        "code": code,
        "code_is_one": kind == "uvresp" and type(code) is int and (code == 1),
        "has_next_challenge": has_next,
        "next_challenge_type": value[7][5] if has_next and len(value[7]) > 5 else None,
        "value": value,
    }


def normalize_result_evidence(value: dict[str, Any]) -> dict[str, Any]:
    """根据完整原始响应校验协议成功证据。"""
    result = dict(value)
    request = result.get("userverifyRequest")
    request = request if isinstance(request, dict) else {}
    raw = str(request.get("responseText") or "")
    parsed = None
    try:
        parsed = parse_userverify_response(raw)
    except EnterpriseUserVerifyError:
        pass
    observed = bool(parsed and parsed["kind"] == "uvresp")
    token = parsed["token"] if observed else ""
    status = request.get("status")
    http_ok = type(status) is int and 200 <= status < 300
    completed = bool(
        observed
        and http_ok
        and (not request.get("networkError"))
        and parsed["code_is_one"]
        and token.strip()
        and (not parsed["has_next_challenge"])
    )
    current_contract = (
        result.get("token_evidence_version") == 3
        and result.get("success_criterion") == "userverify_response"
    )
    ok = bool(
        current_contract
        and completed
        and (result.get("ok") is True)
        and (not result.get("error"))
        and ((result.get("process") or {}).get("returncode", 0) == 0)
    )
    result.update(
        {
            "token_evidence_version": 3,
            "success_criterion": "userverify_response",
            "legacy_result_contract": not current_contract
            or bool(result.get("legacy_result_contract")),
            "ok": ok,
            "protocol_completed": completed,
            "token_source": "userverify" if ok else None,
            "protocol_token": token,
            "protocol_token_length": len(token),
            "protocol_token_sha256": hashlib.sha256(token.encode()).hexdigest() if token else None,
            "protocol_response_observed": observed,
            "protocol_request_url": request.get("url"),
            "protocol_http_status": status,
            "protocol_response_code": parsed["code"] if observed else None,
            "protocol_has_next_challenge": parsed["has_next_challenge"] if observed else None,
            "protocol_next_challenge_type": parsed["next_challenge_type"] if observed else None,
            "userverify_response": parsed,
            "userverify_response_raw": raw,
            "responseToken": token if ok else "",
            "responseTokenLength": len(token) if ok else 0,
            "response_token": token if ok else "",
            "response_token_length": len(token) if ok else 0,
            "business_verified": None,
            "business_verification_status": "not_performed",
        }
    )
    if not ok and (not result.get("error")):
        result["error"] = "未取得最终 userverify 响应，或 runner 未按当前协议正常完成"
    return result
