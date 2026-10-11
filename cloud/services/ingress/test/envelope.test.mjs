import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEnvelope, RequestError } from "../src/envelope.mjs";
import { choose_route } from "../src/policy.mjs";
import { sse } from "../src/sse.mjs";

const good = { version: 1, request_id: "req-00000001", conversation_id: "conv-0000001", data_tier: "private-cloud", messages: [{ role: "user", text: "hi" }] };

test("accepts a minimal private-cloud request with defaults", () => {
  const parsed = parseEnvelope(JSON.stringify(good));
  assert.equal(parsed.modelPreference, "default");
  assert.equal(parsed.deadlineMs, 120000);
});

test("rejects tenant or unknown fields, bad tiers, and oversized input", () => {
  for (const bad of [
    { ...good, tenant_id: "other" },
    { ...good, data_tier: "public" },
    { ...good, version: 2 },
    { ...good, request_id: "x" },
    { ...good, messages: [] },
    { ...good, messages: [{ role: "assistant", text: "last" }] },
    { ...good, messages: [{ role: "user", text: "x".repeat(16001) }] },
    { ...good, deadline_ms: 999999 },
  ]) {
    assert.throws(() => parseEnvelope(bad), (e) => e instanceof RequestError && e.code === "INVALID_REQUEST");
  }
  assert.throws(() => parseEnvelope("{not json"), (e) => e.code === "INVALID_REQUEST");
});

test("route policy keeps local-only on the device and gates step-up", () => {
  const models = { default: "moonshotai.kimi-k2.5", stepUp: "us.anthropic.claude-opus-4-5-20251101-v1:0" };
  assert.throws(() => choose_route({ dataTier: "local-only", modelPreference: "default" }, models), (e) => e.code === "DATA_TIER_DENIED");
  assert.throws(() => choose_route({ dataTier: "private-cloud", modelPreference: "step-up" }, models), (e) => e.code === "DATA_TIER_DENIED");
  assert.deepEqual(choose_route({ dataTier: "private-cloud", modelPreference: "default" }, models), { route: "private-cloud", modelId: models.default });
  assert.equal(choose_route({ dataTier: "frontier-eligible", modelPreference: "step-up" }, models).modelId, models.stepUp);
});

test("frames server-sent events", () => {
  assert.equal(sse("token", { text: "hi" }), 'event: token\ndata: {"text":"hi"}\n\n');
});
