'use strict';

// Interaction control only. SDK state and network responses remain authoritative.
// Dependencies are injected so regressions can run without the SDK or a network.
async function runEnterpriseInteraction({ payload, state, bridge, frame }, hooks) {
  const {
    snapshot: takeSnapshot, token: getToken, result: makeResult,
    requestCount, tileContext, waitForSubmission, waitForTile,
    delay, now = Date.now,
  } = hooks;
  const started = now();
  const budgetMs = Math.max(15000, Number(payload.protocol_timeout_ms || 180000));
  const stallMs = Math.max(1000, Number(payload.interaction_stall_ms || 15000));
  let deadline = started + budgetMs;
  let callerWaitMs = 0;
  let answerWaitingAt = null;
  let round = 0;
  let lastActionAt = started;
  let latest = null;
  let stopped = false;
  const actions = [];

  const summarize = (snapshot) => snapshot ? {
    ready: Boolean(snapshot.ready),
    interactionReady: Boolean(snapshot.interactionReady),
    imageResourcesReady: Boolean(snapshot.imageResourcesReady),
    verifyButtonDisabled: Boolean(snapshot.verifyButtonDisabled),
    verifyButtonText: snapshot.verifyButtonText || '',
    tileCount: snapshot.tileCount,
    selected: (snapshot.tiles || []).filter((tile) => tile.selected)
      .map((tile) => tile.index),
    blocked: Boolean(snapshot.blocked),
  } : null;
  const finish = (extra = {}) => {
    const elapsedMs = now() - started;
    const totalWaitMs = callerWaitMs + (answerWaitingAt == null ? 0 : Math.max(0, now() - answerWaitingAt));
    return makeResult(state, {
      interaction: {
        elapsedMs,
        callerWaitMs: totalWaitMs,
        activeElapsedMs: Math.max(0, elapsedMs - totalWaitMs),
        budgetMs,
        lastSnapshot: summarize(latest),
        actions: JSON.parse(JSON.stringify(actions)),
      },
      ...extra,
    });
  };
  const fingerprint = (snapshot) => (snapshot ? snapshot.fingerprint : '')
    + '|uv=' + state.userverifyRequests.length
    + '|ri=' + requestCount(state, 'replaceimage');

  const interact = async () => {
    try {
      while (!stopped && now() < deadline) {
        if (stopped || getToken() || (hooks.terminal && hooks.terminal())) return finish();
        latest = takeSnapshot(frame, round + 1);
        if (latest && latest.blocked) {
          return finish({ ok: false, error: 'Google returned try-again-later', challenge: latest });
        }
        const stalled = now() - lastActionAt >= stallMs;
        const liveReady = Boolean(latest && latest.ready);
        if (!liveReady && !stalled) {
          await delay(100);
          continue;
        }
        // A stale snapshot is for display only, never proof that a click is safe.
        const display = latest || state.lastChallengeSnapshot;
        const snapshot = display ? {
          ...display,
          errors: Array.from(display.errors || []),
          controlsOnly: !liveReady,
          staleSnapshot: !latest,
        } : {
          ready: false, tiles: [], errors: [], controlsOnly: true,
          staleSnapshot: true, imageResourcesReady: false,
        };
        if (snapshot.controlsOnly) {
          snapshot.errors.push('页面尚未恢复可交互状态；可输入 r 刷新或 q 退出。');
        }
        if (state.replaceimageRetryPending) {
          snapshot.errors.push('replaceimage 返回 code=2，未取得后续题面；原因尚未确认。');
        }
        if (state.submissionRetryPending) {
          snapshot.errors.push('上次操作未观察到进展，请检查页面状态。');
        }
        const previous = actions[actions.length - 1];
        if (previous && ['tile-wait-timeout', 'tile-network-error', 'no-change', 'invalid-action', 'not-ready', 'refresh-unavailable', 'document-unavailable'].includes(previous.outcome)) {
          snapshot.errors.push('上次操作结果：' + previous.outcome + '；可重新输入命令，或 r 刷新 / q 退出。');
        }
        snapshot.round = ++round;
        snapshot.userverifyCount = state.userverifyRequests.length;
        if (latest) state.lastChallengeSnapshot = latest;
        const waitingAt = now();
        answerWaitingAt = waitingAt;
        let answer;
        try {
          answer = await bridge.request('challenge', { challenge: snapshot },
            payload.answer_timeout_ms || 600000);
        } finally {
          const waited = Math.max(0, now() - waitingAt);
          callerWaitMs += waited;
          answerWaitingAt = null;
          // Only local timeout accounting changes; SDK/server token expiry does not.
          deadline += waited;
        }
        if (stopped || getToken() || (hooks.terminal && hooks.terminal())) return finish();
        const action = String(answer && answer.action || '').toLowerCase();
        const before = takeSnapshot(frame, round);
        latest = before;
        const beforeFingerprint = fingerprint(before);
        const entry = {
          round, action, indices: Array.from(answer && answer.indices || []),
          atMs: now() - started, callerWaitMs: now() - waitingAt,
          before: summarize(before), outcome: 'pending',
        };
        actions.push(entry);
        if (actions.length > 100) actions.shift();
        if (action === 'quit') {
          entry.outcome = 'cancelled';
          return finish({ ok: false, error: 'challenge cancelled by caller', challenge: snapshot });
        }
        if (before && before.blocked) {
          entry.outcome = 'blocked';
          return finish({ ok: false, error: 'Google returned try-again-later', challenge: before });
        }
        const actionStarted = now();
        try {
          const doc = frame.contentDocument;
          if (!doc) {
            entry.outcome = 'document-unavailable';
          } else if (action === 'refresh') {
            const button = doc.querySelector('#recaptcha-reload-button');
            if (!button || button.disabled) entry.outcome = 'refresh-unavailable';
            else { button.click(); entry.outcome = 'clicked'; }
          } else if (['select', 'verify', 'skip'].includes(action)) {
            // Re-read after human input: readiness may have changed while waiting.
            if (!before || !before.ready) {
              entry.outcome = 'not-ready';
            } else if (action === 'select') {
              state.submissionRetryPending = false;
              entry.appliedIndices = [];
              entry.invalidIndices = [];
              for (const rawIndex of answer.indices || []) {
                const current = takeSnapshot(frame, round);
                if (!current || !current.ready) { entry.outcome = 'not-ready'; break; }
                const tiles = tileContext(doc).tiles;
                const index = Number(rawIndex) - 1;
                if (!Number.isInteger(index) || index < 0 || index >= tiles.length) {
                  entry.invalidIndices.push(rawIndex);
                  continue;
                }
                const replaceCount = requestCount(state, 'replaceimage');
                tiles[index].click();
                entry.appliedIndices.push(index + 1);
                const wait = await waitForTile(state, frame, replaceCount, current, index, 15000);
                if (stopped) return finish();
                if (wait && typeof wait === 'object') {
                  if (!entry.tileWaits) entry.tileWaits = [];
                  entry.tileWaits.push(wait);
                  if (wait.outcome === 'timeout' || wait.outcome === 'network-error') {
                    entry.outcome = wait.outcome === 'timeout' ? 'tile-wait-timeout' : 'tile-network-error';
                    entry.waitPhase = wait.phase;
                    break; // Do not re-click or apply remaining indices to a changing grid.
                  }
                }
                if (state.replaceimageRetryPending) break;
              }
            } else {
              const button = doc.querySelector('#recaptcha-verify-button');
              if (!button || button.disabled) entry.outcome = 'not-ready';
              else {
                const uvCount = state.userverifyRequests.length;
                const replaceCount = requestCount(state, 'replaceimage');
                state.submissionRetryPending = false;
                // Do not force-enable a button or invent SDK success/retry state.
                button.click();
                const progressed = await waitForSubmission(state, frame, before,
                  uvCount, replaceCount, 2500);
                if (!progressed) {
                  state.submissionRetryPending = true;
                  state.submissionNoChangeCount += 1;
                }
              }
            }
          } else {
            entry.outcome = 'invalid-action';
          }
          await delay(500);
          if (stopped) return finish();
          latest = takeSnapshot(frame, round + 1);
          entry.after = summarize(latest);
          entry.changed = fingerprint(latest) !== beforeFingerprint;
          if (entry.outcome === 'pending' || entry.outcome === 'clicked') {
            entry.outcome = getToken() ? 'token-received' : entry.changed ? 'changed' : 'no-change';
          }
        } catch (error) {
          entry.outcome = 'error';
          entry.error = String(error && error.message || error);
          latest = takeSnapshot(frame, round + 1);
          entry.after = summarize(latest);
          throw error;
        } finally {
          entry.durationMs = now() - actionStarted;
        }
        // A consumed command always earns another prompt, even if its click was
        // ignored or indices were invalid. Never use DOM change as input admission.
        lastActionAt = now();
        if (stopped || getToken() || (hooks.terminal && hooks.terminal())) return finish();
      }
      latest = takeSnapshot(frame, round + 1);
      return finish({ ok: false, error: 'Enterprise protocol timeout' });
    } catch (error) {
      return finish({ ok: false, error: String(error && error.message || error) });
    }
  };
  // A final network response can arrive while a challenge/input or tile wait is
  // outstanding. Return immediately; the owner closes the bridge on completion.
  if (hooks.completion) {
    return Promise.race([interact(), hooks.completion.then(() => {
      stopped = true;
      return finish();
    })]);
  }
  return interact();
}

module.exports = { runEnterpriseInteraction };
