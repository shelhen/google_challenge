'use strict';
const { createHash } = require('node:crypto');

function tileWaitSnapshot(snapshot, tileIndex, beforeTile) {
  if (!snapshot) return null;
  const tile = (snapshot.tiles || [])[tileIndex];
  return {
    ready: Boolean(snapshot.ready),
    imageResourcesReady: Boolean(snapshot.imageResourcesReady),
    verifyButtonDisabled: Boolean(snapshot.verifyButtonDisabled),
    tableCount: snapshot.tableCount,
    allTileCount: snapshot.allTileCount,
    tile: tile ? {
      index: tile.index,
      selected: Boolean(tile.selected),
      domSelected: tile.domSelected,
      imageCount: tile.imageCount,
      imageComplete: tile.imageComplete,
      naturalWidth: tile.naturalWidth,
      naturalHeight: tile.naturalHeight,
      className: tile.className,
      sourceChanged: Boolean(beforeTile && tile.src && tile.src !== beforeTile.src),
      sourceSha256: createHash('sha256').update(String(tile.src || '')).digest('hex'),
    } : null,
  };
}

// Observe only: never enable controls, click again, or manufacture SDK state.
async function waitForTileReplacement(options, hooks) {
  const { state, beforeCount, beforeSnapshot, tileIndex,
    networkTimeoutMs = 90500, settleTimeoutMs = 15000, discoveryTimeoutMs = 1000 } = options;
  const { requests, snapshot, delay, now = Date.now, token = () => '' } = hooks;
  const started = now();
  const beforeTile = (beforeSnapshot && beforeSnapshot.tiles || [])[tileIndex];
  let phase = 'request-discovery';
  let networkFinishedAt = null;
  let observedCount = 0;
  let latest = null;
  let currentRequests = [];
  const transitions = [];
  let lastSignature = '';
  function observe() {
    latest = snapshot();
    const observation = { phase, snapshot: tileWaitSnapshot(latest, tileIndex, beforeTile),
      requests: currentRequests.map(item => ({ status: Number(item.status || 0),
        completed: item.completedAtMs != null || Number(item.status || 0) > 0 || Boolean(item.networkError),
        networkError: Boolean(item.networkError), elapsedMs: item.elapsedMs,
        bridgeTotalSeconds: item.bridgeTotalSeconds })) };
    const signature = JSON.stringify(observation);
    if (signature !== lastSignature) {
      transitions.push({ atMs: now() - started, ...observation });
      if (transitions.length > 40) transitions.shift();
      lastSignature = signature;
    }
  }
  function finish(outcome) {
    observe();
    const result = { outcome, phase, tileIndex: tileIndex + 1,
      elapsedMs: now() - started,
      networkElapsedMs: networkFinishedAt == null ? null : networkFinishedAt - started,
      settleElapsedMs: networkFinishedAt == null ? null : now() - networkFinishedAt,
      lastSnapshot: tileWaitSnapshot(latest, tileIndex, beforeTile), transitions };
    state.lastTileWait = result;
    if (!Array.isArray(state.tileWaitHistory)) state.tileWaitHistory = [];
    state.tileWaitHistory.push(result);
    if (state.tileWaitHistory.length > 30) state.tileWaitHistory.shift();
    return result;
  }
  while (true) {
    if (token()) return finish('callback-observed');
    currentRequests = requests().slice(beforeCount);
    if (!currentRequests.length) {
      observe();
      if (now() - started >= discoveryTimeoutMs) return finish('no-replacement-request');
    } else {
      const completed = currentRequests.every(item => item.completedAtMs != null
        || Number(item.status || 0) > 0 || item.networkError);
      if (!completed) {
        phase = 'network';
        networkFinishedAt = null;
        observe();
        if (now() - started >= networkTimeoutMs) return finish('timeout');
      } else {
        if (currentRequests.some(item => item.networkError || Number(item.status) < 200 || Number(item.status) >= 400)) {
          phase = 'network';
          return finish('network-error');
        }
        if (networkFinishedAt == null || observedCount !== currentRequests.length) networkFinishedAt = now();
        observedCount = currentRequests.length;
        phase = 'dom-settle';
        observe();
        if (state.replaceimageRetryPending) return finish('server-retry-signal');
        const tile = (latest && latest.tiles || [])[tileIndex];
        const sourceChanged = Boolean(tile && beforeTile && tile.src && tile.src !== beforeTile.src);
        const tableChanged = Boolean(latest && beforeSnapshot && (
          latest.tableCount !== beforeSnapshot.tableCount || latest.allTileCount !== beforeSnapshot.allTileCount));
        const imageLoaded = tile && (tile.imageComplete === undefined ||
          (tile.imageComplete && Number(tile.naturalWidth) > 0));
        if (latest && latest.ready && tile && !tile.selected && imageLoaded && (sourceChanged || tableChanged)) {
          return finish('settled');
        }
        if (now() - networkFinishedAt >= settleTimeoutMs) return finish('timeout');
      }
    }
    await delay(100);
  }
}
module.exports = { waitForTileReplacement, tileWaitSnapshot };
