const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { createServer } = require("node:http");
const { test } = require("node:test");
const { invokeUpdateAction } = require("../scripts/ota-drive.js");

function fakeSocket(t, afterSend, beforeSend = (ws) => ws.onopen()) {
  class FakeSocket {
    constructor() { queueMicrotask(() => beforeSend(this)); }
    send(data) { const request = JSON.parse(data); queueMicrotask(() => afterSend(this, request)); }
    // Closing our one-shot socket ourselves must not override the result.
    close() { this.onclose({ code: 1000 }); }
    reply(body) { this.onmessage({ data: JSON.stringify({ id: 1, ...body }) }); }
  }
  t.mock.method(globalThis, "WebSocket", function () { return new FakeSocket(); });
}

const url = "ws://localhost/devtools/page/test";
const value = (ws, payload) => ws.reply({ result: { result: { type: "object", value: payload } } });

for (const action of ["install", "relaunch"]) {
  test(`${action} requires the IPC acknowledgement`, async (t) => {
    fakeSocket(t, (ws, request) => {
      assert.equal(request.params.expression, `window.updateAPI.${action}()`);
      value(ws, { ok: true });
    });
    await invokeUpdateAction(url, action);
  });
}

test("relaunch reports an explicit IPC refusal instead of claiming quit", async (t) => {
  fakeSocket(t, (ws) => value(ws, { ok: false, error: "relaunch is only available from the local dashboard" }));
  await assert.rejects(invokeUpdateAction(url, "relaunch"), /not acknowledged: relaunch is only available/);
});

test("missing install acknowledgement prevents advancing to relaunch", async (t) => {
  fakeSocket(t, (ws) => ws.reply({ result: { result: { type: "undefined" } } }));
  await assert.rejects(invokeUpdateAction(url, "install"), /not acknowledged: expected/);
});

test("a connection close after sending relaunch may interrupt the reply", async (t) => {
  fakeSocket(t, (ws) => ws.onclose({ code: 1006 }));
  await invokeUpdateAction(url, "relaunch");
});

test("Node WebSocket accepts a real peer shutdown after sending relaunch", async (t) => {
  const server = createServer();
  server.on("upgrade", (request, socket) => {
    const accept = createHash("sha1")
      .update(request.headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
      .digest("base64");
    socket.once("data", () => socket.destroy());
    socket.write("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\n" +
      `Upgrade: websocket\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  t.after(() => server.close());
  await invokeUpdateAction(`ws://127.0.0.1:${server.address().port}/devtools/page/test`, "relaunch");
});

test("disconnect is not accepted before sending relaunch or during install", async (t) => {
  fakeSocket(t, (ws) => ws.onclose({ code: 1006 }));
  await assert.rejects(invokeUpdateAction(url, "install"), /socket closed before its reply/);
  t.mock.restoreAll();
  fakeSocket(t, () => assert.fail("command should not have been sent"), (ws) => ws.onclose({ code: 1006 }));
  await assert.rejects(invokeUpdateAction(url, "relaunch"), /before the request was sent/);
});

test("CDP errors, evaluation exceptions, and WebSocket errors are not quit events", async (t) => {
  for (const [event, expected] of [
    [(ws) => ws.reply({ error: { code: -32000, message: "target unavailable" } }), /target unavailable/],
    [(ws) => ws.reply({ result: { exceptionDetails: { text: "Uncaught exception" } } }), /Uncaught exception/],
    [(ws) => { ws.onerror({}); ws.onclose({ code: 1006 }); }, /websocket error/],
  ]) {
    fakeSocket(t, event);
    await assert.rejects(invokeUpdateAction(url, "relaunch"), expected);
    t.mock.restoreAll();
  }
});

test("a relaunch evaluation timeout is a failure even if cleanup closes the socket", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  fakeSocket(t, () => {});
  const failure = assert.rejects(invokeUpdateAction(url, "relaunch"), /evaluate timed out/);
  await new Promise(queueMicrotask);
  t.mock.timers.tick(15000);
  await failure;
});
