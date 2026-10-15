'use strict';

// Contract v3: protocol completion, not an SDK callback or business acceptance.
function parseEnterpriseUserverifyResponse(text) {
  let raw = String(text || '').trim();
  if (raw.startsWith(")]}'")) raw = raw.slice(4).trimStart();
  try {
    const value = JSON.parse(raw);
    if (!Array.isArray(value) || value[0] !== 'uvresp') return null;
    const hasNextChallenge = Array.isArray(value[7]) && value[7][0] === 'rresp';
    return {
      value,
      token: typeof value[1] === 'string' ? value[1] : '',
      code: value.length > 2 ? value[2] : null,
      codeIsOne: value[2] === 1,
      hasNextChallenge,
      nextChallengeType: hasNextChallenge ? (value[7][5] ?? null) : null,
    };
  } catch { return null; }
}

function recordEnterpriseProtocolResponse(summary, text) {
  const parsed = parseEnterpriseUserverifyResponse(text);
  summary.responseText = text;
  summary.protocolResponseParsed = Boolean(parsed);
  summary.protocolToken = parsed ? parsed.token : '';
  summary.userverifyCode = parsed ? parsed.code : null;
  summary.userverifyCodeIsOne = Boolean(parsed && parsed.codeIsOne);
  summary.hasNextChallenge = Boolean(parsed && parsed.hasNextChallenge);
  summary.nextChallengeType = parsed ? parsed.nextChallengeType : null;
}

function enterpriseProtocolOutcome(state) {
  const responses = state.userverifyRequests || [];
  const response = responses.length ? responses[responses.length - 1] : null;
  if (!response || (response.completedAtMs == null && !response.networkError)) {
    return { response, terminal: false, ok: false, error: null };
  }
  const failure = error => ({ response, terminal: true, ok: false, error });
  if (response.networkError) return failure('userverify network request failed');
  if (!(response.status >= 200 && response.status < 300)) {
    return failure('userverify HTTP ' + response.status);
  }
  if (!response.protocolResponseParsed) return failure('userverify response is not valid uvresp JSON');
  if (response.hasNextChallenge) return { response, terminal: false, ok: false, error: null };
  if (!response.userverifyCodeIsOne) return failure('userverify ended without code=1');
  if (!response.protocolToken.trim()) return failure('userverify code=1 returned an empty token');
  return { response, terminal: true, ok: true, error: null };
}

function createEnterpriseCompletion(state) {
  let resolve, completed = false;
  const promise = new Promise(done => { resolve = done; });
  return {
    promise,
    notify() {
      const outcome = enterpriseProtocolOutcome(state);
      if (!completed && outcome.terminal) {
        completed = true;
        resolve(outcome);
      }
    },
  };
}

function enterpriseEvidence(state) {
  const { response: protocol, terminal, ok, error } = enterpriseProtocolOutcome(state);
  const protocolToken = protocol && protocol.protocolToken || '';
  return {
    token_evidence_version: 3,
    success_criterion: 'userverify_response',
    ok,
    error,
    protocol_completed: ok,
    protocol_terminal: terminal,
    responseToken: ok ? protocolToken : '',
    responseTokenLength: ok ? protocolToken.length : 0,
    token_source: ok ? 'userverify' : null,
    protocol_token: protocolToken,
    protocol_token_length: protocolToken.length,
    protocol_response_observed: Boolean(protocol && protocol.protocolResponseParsed),
    protocol_request_url: protocol ? protocol.url : null,
    protocol_http_status: protocol ? protocol.status : null,
    protocol_response_code: protocol ? protocol.userverifyCode : null,
    protocol_has_next_challenge: protocol ? protocol.hasNextChallenge : null,
    protocol_next_challenge_type: protocol ? protocol.nextChallengeType : null,
    sdk_callback_checked: false,
    business_verified: null,
    business_verification_status: 'not_performed',
  };
}

module.exports = {
  parseEnterpriseUserverifyResponse, recordEnterpriseProtocolResponse,
  enterpriseProtocolOutcome, createEnterpriseCompletion, enterpriseEvidence,
};
