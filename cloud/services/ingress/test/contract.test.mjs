// The published contract in cloud/schemas is the on-prem team's source of truth;
// these tests fail if the Lambda's validator drifts from it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseEnvelope, DATA_TIERS, MODEL_PREFERENCES, RequestError } from "../src/envelope.mjs";
import { choose_route } from "../src/policy.mjs";

const schemas = new URL("../../../schemas/", import.meta.url);
const schema = JSON.parse(readFileSync(new URL("request.v1.schema.json", schemas)));
const example = (name) => readFileSync(new URL(`examples/${name}`, schemas), "utf8");
const models = { default: "kimi", stepUp: "opus" };

test("validator enums match the schema", () => {
  assert.deepEqual([...DATA_TIERS].sort(), [...schema.properties.data_tier.enum].sort());
  assert.deepEqual([...MODEL_PREFERENCES].sort(), [...schema.properties.model_preference.enum].sort());
});

test("example requests parse and route as documented", () => {
  assert.equal(choose_route(parseEnvelope(example("request-private-cloud.json")), models).route, "private-cloud");
  assert.equal(choose_route(parseEnvelope(example("request-step-up.json")), models).modelId, "opus");
  const local = parseEnvelope(example("request-local-only.json"));
  assert.throws(() => choose_route(local, models), (e) => e.code === "DATA_TIER_DENIED" && e.status === 403);
});

test("messages reject fields the schema does not allow", () => {
  const body = JSON.parse(example("request-private-cloud.json"));
  body.messages[0].tenant_id = "other";
  assert.throws(() => parseEnvelope(body), (e) => e instanceof RequestError && e.code === "INVALID_REQUEST");
});

test("example streams use only documented events", () => {
  for (const name of ["stream-ok.txt", "stream-error.txt"]) {
    const events = [...example(name).matchAll(/^event: (\w+)$/gm)].map((m) => m[1]);
    assert.ok(events.every((e) => ["meta", "tool", "token", "done", "error"].includes(e)), name);
  }
});
