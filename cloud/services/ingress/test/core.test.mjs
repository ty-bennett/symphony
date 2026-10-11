import { test } from "node:test";
import assert from "node:assert/strict";
import { handle, factsBlock } from "../src/core.mjs";
import { authorize } from "../src/authorize.mjs";

const config = { tenantId: "demo", models: { default: "kimi", stepUp: "opus" }, systemPrompt: "You are Symphony." };
const auth = { tenantId: "demo", deviceId: "jetson-01", userSub: "user-123" };
const body = JSON.stringify({ version: 1, request_id: "req-00000001", conversation_id: "conv-0000001", data_tier: "private-cloud", messages: [{ role: "user", text: "What is our PTO policy?" }] });

function deps(overrides = {}) {
  const calls = { invoke: [], logs: [] };
  return {
    calls,
    claimRequest: async () => true,
    recallFacts: async () => ["Ty prefers answers formatted as tables."],
    invokeHarness: async function* (args) {
      calls.invoke.push(args);
      yield { contentBlockStart: { start: { toolUse: { name: "knowledge-search___search_documents" } } } };
      yield { contentBlockDelta: { delta: { text: "1.5 days " } } };
      yield { contentBlockDelta: { delta: { text: "per month." } } };
      yield { messageStop: { stopReason: "end_turn" } };
      yield { metadata: { usage: { inputTokens: 900, outputTokens: 12 } } };
    },
    log: (line) => calls.logs.push(line),
    now: () => 1000,
    ...overrides,
  };
}

function run(d, b = body, a = auth) {
  const out = [];
  return handle({ body: b, auth: a, write: (s) => out.push(s), deps: d, config }).then((audit) => ({ out: out.join(""), audit }));
}

test("streams meta, tool, tokens, and done; injects memory; audits without text", async () => {
  const d = deps();
  const { out, audit } = await run(d);
  assert.match(out, /^event: meta\n/);
  assert.match(out, /event: tool\ndata: {"name":"knowledge-search___search_documents"}/);
  assert.match(out, /event: token\ndata: {"text":"1.5 days "}/);
  assert.match(out, /event: done\n/);
  assert.equal(d.calls.invoke[0].actorId, "user-123");
  assert.equal(d.calls.invoke[0].modelId, "kimi");
  // Facts ride in the per-request system prompt, never in the user's message, so memory
  // extraction does not re-store them (observed live: injected facts were re-extracted).
  assert.deepEqual(d.calls.invoke[0].messages, [{ role: "user", content: [{ text: "What is our PTO policy?" }] }]);
  assert.equal(d.calls.invoke[0].systemPrompt[0].text, "You are Symphony.");
  assert.match(d.calls.invoke[0].systemPrompt[1].text, /^Known facts about this user[\s\S]*tables/);
  const logged = JSON.stringify(d.calls.logs[0]);
  assert.ok(!logged.includes("PTO") && !logged.includes("1.5 days") && !logged.includes("user-123"));
  assert.equal(audit.memory_facts, 1);
});

test("denies local-only and wrong tenant before calling the agent", async () => {
  const d = deps();
  const local = await run(d, body.replace("private-cloud", "local-only"));
  assert.match(local.out, /event: error\ndata: {"code":"DATA_TIER_DENIED"/);
  const wrong = await run(d, body, { ...auth, tenantId: "other" });
  assert.match(wrong.out, /"code":"USER_UNAUTHORIZED"/);
  assert.equal(d.calls.invoke.length, 0);
});

test("rejects a replayed request_id", async () => {
  const d = deps({ claimRequest: async () => false });
  const { out } = await run(d);
  assert.match(out, /"code":"DUPLICATE_REQUEST"/);
  assert.equal(d.calls.invoke.length, 0);
});

test("continues without memory when recall is slow", async () => {
  const d = deps({ recallFacts: () => new Promise((r) => setTimeout(() => r(["late"]), 2000)) });
  const { audit } = await run(d);
  assert.equal(audit.memory_facts, 0);
  assert.equal(d.calls.invoke[0].messages[0].content[0].text, "What is our PTO policy?");
});

test("caps injected memory", () => {
  assert.ok(factsBlock(Array(50).fill("x".repeat(100))).length < 1700);
  assert.equal(factsBlock([]), "");
});

const device = { device_id: "jetson-01", tenant_id: "demo", status: "active", not_after: "2099-01-01T00:00:00Z" };
const authDeps = (o = {}) => ({ getDevice: async () => device, verifyToken: async () => ({ sub: "user-123" }), now: () => Date.parse("2026-10-11T00:00:00Z"), ...o });

test("authorizer allows an active device with a valid user token", async () => {
  const r = await authorize({ clientCert: { serialNumber: "AB12" }, authorization: "Bearer t", tenantId: "demo", deps: authDeps() });
  assert.deepEqual(r.context, { user_sub: "user-123", device_id: "jetson-01", tenant_id: "demo" });
});

test("authorizer rejects revoked, expired, foreign, or missing devices and bad tokens", async () => {
  const cases = [
    [{ getDevice: async () => ({ ...device, status: "revoked" }) }, "DEVICE_REVOKED"],
    [{ getDevice: async () => ({ ...device, not_after: "2020-01-01T00:00:00Z" }) }, "DEVICE_REVOKED"],
    [{ getDevice: async () => ({ ...device, tenant_id: "other" }) }, "DEVICE_REVOKED"],
    [{ getDevice: async () => null }, "DEVICE_REVOKED"],
    [{ verifyToken: async () => { throw new Error("expired"); } }, "USER_UNAUTHORIZED"],
  ];
  for (const [o, code] of cases) {
    await assert.rejects(authorize({ clientCert: { serialNumber: "AB12" }, authorization: "Bearer t", tenantId: "demo", deps: authDeps(o) }), (e) => e.code === code);
  }
  await assert.rejects(authorize({ clientCert: undefined, authorization: "Bearer t", tenantId: "demo", deps: authDeps() }), (e) => e.code === "DEVICE_REVOKED");
  await assert.rejects(authorize({ clientCert: { serialNumber: "AB12" }, authorization: "", tenantId: "demo", deps: authDeps() }), (e) => e.code === "USER_UNAUTHORIZED");
});

test("matches an API Gateway colon-hex serial to the registry's plain hex", async () => {
  const seen = [];
  const result = await authorize({
    clientCert: { serialNumber: "00:0A:1B:2C" },
    authorization: "Bearer tok",
    tenantId: "demo",
    deps: {
      getDevice: async (serial) => { seen.push(serial); return { status: "active", tenant_id: "demo", device_id: "jetson-01", not_after: "2099-01-01T00:00:00Z" }; },
      verifyToken: async () => ({ sub: "user-123" }),
      now: () => 0,
    },
  });
  assert.deepEqual(seen, ["a1b2c"]);
  assert.equal(result.context.device_id, "jetson-01");
});

test("maps upstream throttling and timeouts to stable codes", async () => {
  const failing = (name) => deps({ invokeHarness: async function* () { const e = new Error("x"); e.name = name; throw e; } });
  const throttled = await run(failing("ThrottlingException"));
  assert.match(throttled.out, /"code":"MODEL_UNAVAILABLE"/);
  const slow = await run(failing("TimeoutError"));
  assert.match(slow.out, /"code":"UPSTREAM_TIMEOUT"/);
});

test("continues without facts when memory recall fails", async () => {
  const d = deps({ recallFacts: async () => { const e = new Error("secret detail"); e.name = "AccessDeniedException"; throw e; } });
  const { out, audit } = await run(d);
  assert.match(out, /event: done\n/);
  assert.equal(audit.memory_facts, 0);
  assert.equal(audit.memory, "error");
  assert.equal(audit.memory_error, "AccessDeniedException");
  assert.ok(!JSON.stringify(audit).includes("secret detail"));
  assert.equal(d.calls.invoke[0].systemPrompt, undefined);
  assert.doesNotMatch(d.calls.invoke[0].messages[0].content[0].text, /Known facts/);
});

test("denials carry the serial for operators but never the token", async () => {
  const error = await authorize({
    clientCert: { serialNumber: "0A:1B" },
    authorization: "Bearer secret-token",
    tenantId: "demo",
    deps: { getDevice: async () => null, verifyToken: async () => ({ sub: "u" }), now: () => 0 },
  }).catch((e) => e);
  assert.equal(error.code, "DEVICE_REVOKED");
  assert.deepEqual(error.detail, { serial: "a1b", has_cert: true, registered: false });
  assert.ok(!JSON.stringify(error.detail).includes("secret-token"));
});

test("matches API Gateway's decimal serial to the registry's hex (observed live)", async () => {
  const { normalizeSerial } = await import("../src/authorize.mjs");
  assert.equal(normalizeSerial("458237401760167982303825506377863675654501280832"), "50441364c2eb2d6f1420cff52f572c140737ac40");
  assert.equal(normalizeSerial("00:0A:1B:2C"), "a1b2c");
});
