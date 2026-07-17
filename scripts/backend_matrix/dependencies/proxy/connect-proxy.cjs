"use strict";

// Reviewed, deliberately non-configurable CONNECT proxy for funded dependency
// provisioning. TLS remains end-to-end between npm and registry.npmjs.org.
const http = require("node:http");
const net = require("node:net");

const HOST = "registry.npmjs.org";
const PORT = 443;
const AUTHORITY = `${HOST}:${PORT}`;
const LISTEN_PORT = 3128;
const MAX_CONNECTIONS = 64;
const MAX_HEAD_BYTES = 16 * 1024;
const IDLE_TIMEOUT_MS = 5 * 60 * 1000;

const sockets = new Set();
let activeTunnels = 0;

function audit(event, detail = {}) {
  process.stdout.write(`${JSON.stringify({event, ...detail})}\n`);
}

function closeWith(socket, status, reason) {
  if (!socket.destroyed) {
    socket.end(
      `HTTP/1.1 ${status} ${reason}\r\n` +
      "Connection: close\r\n" +
      "Content-Length: 0\r\n\r\n"
    );
  }
}

const server = http.createServer({maxHeaderSize: 8192}, (_request, response) => {
  response.writeHead(405, {Connection: "close", "Content-Length": "0"});
  response.end();
});

server.maxConnections = MAX_CONNECTIONS;
server.headersTimeout = 10_000;
server.requestTimeout = 10_000;
server.keepAliveTimeout = 5_000;

server.on("connection", (socket) => {
  sockets.add(socket);
  socket.setTimeout(IDLE_TIMEOUT_MS, () => socket.destroy());
  socket.once("close", () => sockets.delete(socket));
});

server.on("connect", (request, client, head) => {
  const authority = typeof request.url === "string" ? request.url : "";
  const hostHeader = typeof request.headers.host === "string" ? request.headers.host : "";
  if (
    authority.length !== AUTHORITY.length ||
    authority.toLowerCase() !== AUTHORITY ||
    (hostHeader && hostHeader.toLowerCase() !== AUTHORITY) ||
    head.length > MAX_HEAD_BYTES
  ) {
    audit("connect_denied", {authority: authority.slice(0, 256)});
    closeWith(client, 403, "Forbidden");
    return;
  }
  if (activeTunnels >= MAX_CONNECTIONS) {
    closeWith(client, 503, "Service Unavailable");
    return;
  }

  // Ignore the request destination after validation. The upstream target is a
  // code constant, never a client-controlled hostname or address.
  const upstream = net.connect({host: HOST, port: PORT});
  sockets.add(upstream);
  activeTunnels += 1;
  let established = false;

  const finish = () => {
    if (activeTunnels > 0) activeTunnels -= 1;
    sockets.delete(upstream);
  };
  // Bound the upstream TCP connect itself (not just the post-connect idle):
  // a registry edge that accepts then stalls the handshake must fail fast and
  // deterministically so npm's own fetch-retries can re-attempt, rather than
  // hanging until the idle timeout.
  const CONNECT_TIMEOUT_MS = 15_000;
  const connectTimer = setTimeout(() => {
    if (!established) {
      audit("upstream_connect_timeout", {authority: AUTHORITY});
      upstream.destroy();
    }
  }, CONNECT_TIMEOUT_MS);
  connectTimer.unref();
  upstream.setTimeout(IDLE_TIMEOUT_MS, () => upstream.destroy());
  upstream.once("close", finish);
  upstream.once("error", (error) => {
    // A pre-tunnel failure is the ONLY 502 THIS proxy can emit; use a reason
    // that names the proxy so it can never be confused with registry.npmjs.org
    // returning 502 for a GET (which rides the established TLS tunnel and the
    // proxy never sees). Audited so the failure is captured in run evidence.
    audit("upstream_error", {
      established,
      code: String((error && error.code) || "UNKNOWN"),
    });
    if (!established) closeWith(client, 502, "SignalOS Proxy Upstream Connect Failed");
    client.destroy();
  });
  client.once("error", () => upstream.destroy());
  client.once("close", () => upstream.destroy());

  upstream.once("connect", () => {
    established = true;
    clearTimeout(connectTimer);
    client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
    if (head.length) upstream.write(head);
    client.pipe(upstream);
    upstream.pipe(client);
    audit("connect_allowed", {authority: AUTHORITY});
  });
});

server.on("clientError", (_error, socket) => closeWith(socket, 400, "Bad Request"));
server.on("error", (error) => {
  audit("fatal", {code: String(error && error.code || "UNKNOWN")});
  process.exitCode = 1;
});

function shutdown(signal) {
  audit("shutdown", {signal});
  server.close(() => process.exit(0));
  for (const socket of sockets) socket.destroy();
  setTimeout(() => process.exit(1), 5_000).unref();
}

process.once("SIGTERM", () => shutdown("SIGTERM"));
process.once("SIGINT", () => shutdown("SIGINT"));
server.listen(LISTEN_PORT, "0.0.0.0", () => {
  audit("ready", {authority: AUTHORITY, port: LISTEN_PORT});
});
