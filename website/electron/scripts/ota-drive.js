#!/usr/bin/env node
/**
 * Unattended driver for the end-to-end OTA update test.
 *
 * The update flow is deliberately consent-gated (autoDownload=false), so it
 * cannot be exercised by launching the app and waiting -- something has to
 * press "Download" and "Restart & Update". Rather than add a test-only
 * auto-consent hook to shipped code, this drives the REAL renderer over the
 * Chrome DevTools Protocol and calls the SAME preload-exposed API a click
 * calls (window.updateAPI.download / .install / .relaunch). The code path under test is
 * therefore identical to the one users hit.
 *
 * Requires: the app launched with --remote-debugging-port=<port>, a local feed
 * serving a newer version (scripts/local-feed-server.js), and a `kirocrew`
 * gateway resolvable on PATH (the app only reaches initAutoUpdate after the
 * gateway connects).
 *
 * Usage:
 *   node scripts/ota-drive.js --cdp 9222 --expect 1.0.1 [--timeout 180]
 *
 * Exit 0 only if the updater downloaded the release, acknowledged arming
 * install, and either acknowledged relaunch or disconnected after the
 * relaunch request was sent. The caller must still verify the on-disk bundle
 * version; a disconnected renderer does not prove that the app updated.
 */
const http = require("http");

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : def;
}
const CDP_PORT = parseInt(arg("cdp", "9222"), 10);
const EXPECT = arg("expect", null);
const TIMEOUT_MS = parseInt(arg("timeout", "180"), 10) * 1000;

const log = (...a) => console.log("[ota-drive]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getJson(path) {
  return new Promise((resolve, reject) => {
    http
      .get({ host: "127.0.0.1", port: CDP_PORT, path }, (res) => {
        let b = "";
        res.on("data", (c) => (b += c));
        res.on("end", () => {
          try { resolve(JSON.parse(b)); } catch (e) { reject(e); }
        });
      })
      .on("error", reject);
  });
}

/** Find the renderer target that actually has the update bridge attached. */
async function findUpdateTarget(deadline) {
  while (Date.now() < deadline) {
    let targets = [];
    try { targets = await getJson("/json/list"); } catch { /* devtools not up yet */ }
    for (const t of targets.filter((x) => x.type === "page" && x.webSocketDebuggerUrl)) {
      const probe = await evaluate(t.webSocketDebuggerUrl, "typeof window.updateAPI").catch(() => null);
      if (probe && probe.value === "object") {
        log(`found renderer with updateAPI: ${t.url.slice(0, 80)}`);
        return t.webSocketDebuggerUrl;
      }
    }
    await sleep(1000);
  }
  throw new Error("no renderer exposing window.updateAPI appeared before the timeout");
}

class EvaluationDisconnected extends Error {}

/** One-shot CDP Runtime.evaluate over a fresh socket (keeps state simple). */
function evaluate(wsUrl, expression) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let sent = false;
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(result);
      try { ws.close(); } catch {}
    };
    const timer = setTimeout(() => finish(new Error("evaluate timed out")), 15000);
    ws.onopen = () => {
      if (settled) return;
      try {
        ws.send(JSON.stringify({
          id: 1,
          method: "Runtime.evaluate",
          params: { expression, awaitPromise: true, returnByValue: true },
        }));
        sent = true;
      } catch (error) { finish(error); }
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg?.id !== 1) return;
      if (msg.error) { finish(new Error(JSON.stringify(msg.error))); return; }
      if (msg.result && msg.result.exceptionDetails) {
        finish(new Error(msg.result.exceptionDetails.text || "evaluate threw"));
        return;
      }
      finish(null, msg.result && msg.result.result);
    };
    ws.onerror = (event) => {
      // Node 24 emits an empty error before close(1006) when an established
      // peer goes away without a close frame. Let onclose classify that case;
      // a connection or protocol error is not an expected quit.
      if (
        sent && ws.readyState === WebSocket.CLOSED &&
        event.error instanceof TypeError && event.error.message === ""
      ) return;
      finish(new Error(event.error?.message || event.message || "websocket error"));
    };
    ws.onclose = (event) => finish(sent
      ? new EvaluationDisconnected(`CDP socket closed before its reply (code ${event.code})`)
      : new Error("CDP socket closed before the request was sent"));
  });
}

async function invokeUpdateAction(wsUrl, action) {
  let reply;
  try {
    reply = await evaluate(wsUrl, `window.updateAPI.${action}()`);
  } catch (error) {
    if (action === "relaunch" && error instanceof EvaluationDisconnected) {
      log(`relaunch response interrupted after send: ${error.message}`);
      return;
    }
    throw new Error(`updateAPI.${action}() failed: ${error.message}`, { cause: error });
  }
  if (reply?.value?.ok !== true) {
    throw new Error(`updateAPI.${action}() not acknowledged: ${reply?.value?.error || "expected { ok: true }"}`);
  }
}

// Installs a recorder in the page so update states can be polled. Idempotent:
// re-running must not stack listeners or lose already-seen states.
const RECORDER = `
(() => {
  if (!window.__ota) {
    window.__ota = { states: [] };
    window.updateAPI.onState((s) => window.__ota.states.push(s));
  }
  return window.__ota.states.length;
})()`;

const POLL = `JSON.stringify(window.__ota ? window.__ota.states : [])`;

async function waitForState(wsUrl, wanted, deadline) {
  let last = "";
  while (Date.now() < deadline) {
    const r = await evaluate(wsUrl, POLL);
    const states = JSON.parse(r.value || "[]");
    const names = states.map((s) => s.state).join(",");
    if (names !== last) { log(`states: ${names || "(none)"}`); last = names; }
    const hit = states.find((s) => s.state === wanted);
    if (hit) return hit;
    const err = states.find((s) => s.state === "error");
    if (err) throw new Error(`updater reported error: ${err.message}`);
    await sleep(2000);
  }
  throw new Error(`state "${wanted}" not reached before the timeout`);
}

async function main() {
  const deadline = Date.now() + TIMEOUT_MS;
  const ws = await findUpdateTarget(deadline);
  await evaluate(ws, RECORDER);

  // Wait for the update IPC to actually ANSWER before driving anything.
  //
  // preload.js exposes window.updateAPI unconditionally, so findUpdateTarget's
  // `typeof window.updateAPI` probe succeeds while main.js may not have called
  // ipcMain.handle("update:*") yet. Driving straight into that produced a bare
  // "No handler registered for 'update:check'" and killed the run before the
  // swap was ever attempted. main.js now registers the handlers before the
  // awaited gateway boot, so this should pass on the first poll -- but keep the
  // gate: if it ever regresses we get a named failure at the right step instead
  // of an unhandled rejection three lines later.
  //
  // NOTE the .then(): the expression is stringified in-page, and
  // JSON.stringify(aPromise) is "{}" -- awaitPromise cannot rescue that, because
  // the value handed back is already a string. This is why every previous run
  // logged "app info: {}" and told us nothing.
  const infoExpr = "window.updateAPI.getInfo().then(i => JSON.stringify(i))";
  let info = null;
  for (let attempt = 1; Date.now() < deadline; attempt++) {
    const r = await evaluate(ws, infoExpr).catch((e) => ({ __err: e.message }));
    if (r && !r.__err && typeof r.value === "string" && r.value !== "{}") { info = r.value; break; }
    const why = r && r.__err ? r.__err : `unusable result ${JSON.stringify(r && r.value)}`;
    if (attempt === 1 || attempt % 5 === 0) log(`update IPC not answering yet (${why})`);
    await sleep(2000);
  }
  if (info === null) {
    throw new Error(
      "update IPC never answered: window.updateAPI exists but no ipcMain handler responded. " +
      "Check that main.js registers ipcMain.handle(\"update:*\") BEFORE `await startGateway()`.",
    );
  }
  log("app info:", info);

  log("triggering a check (the launch-delay check may already have run)");
  await evaluate(ws, "window.updateAPI.check()");
  const found = await waitForState(ws, "found", deadline);
  log(`DISCOVERY ok -> found ${found.version} (no download yet: consent gate held)`);
  if (EXPECT && found.version !== EXPECT) {
    throw new Error(`expected to find ${EXPECT}, feed offered ${found.version}`);
  }

  log("granting consent -> updateAPI.download()");
  await evaluate(ws, "window.updateAPI.download()");
  const downloaded = await waitForState(ws, "downloaded", deadline);
  log(`DOWNLOAD ok -> staged ${downloaded.version}`);

  // install() only ARMS: an update is applied on the app's own quit, never while
  // it runs. So arm it, then quit the way the Restart now button does
  // (updateAPI.relaunch(): a plain quit whose quit-time install swaps the bundle
  // and brings the app back). That keeps the real quit-time install under test.
  log("arming the install -> updateAPI.install() (nothing quits yet)");
  await invokeUpdateAction(ws, "install");

  log("quitting the way Restart now does -> updateAPI.relaunch() (the quit installs, then the app returns)");
  await invokeUpdateAction(ws, "relaunch");
  log("OK: discovery -> consent -> download -> armed -> relaunch requested; verify the on-disk version");
}

if (require.main === module) {
  main().then(() => process.exit(0), (e) => {
    console.error("[ota-drive] FAIL:", e.message);
    process.exit(1);
  });
}

module.exports = { invokeUpdateAction };
