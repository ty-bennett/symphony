import { test } from "node:test"
import assert from "node:assert/strict"
import { handleChat } from "../src/chat.mjs"

const config = { tenantId: "demo", models: { default: "moonshotai.kimi-k2.5", stepUp: "us.anthropic.claude-opus-4-5-20251101-v1:0" } }
const auth = { tenantId: "demo", deviceId: "jetson-01", userSub: "user-123" }
const body = (over = {}) =>
  JSON.stringify({ model: "orchestra", stream: true, messages: [{ role: "user", content: "Fix the failing test in src/app.ts" }], tools: [{ type: "function", function: { name: "bash", parameters: { type: "object" } } }], ...over })

function deps(events, over = {}) {
  const calls = { converse: [], logs: [] }
  return {
    calls,
    converseStream: async function* (input) {
      calls.converse.push(input)
      for (const e of events) yield e
    },
    log: (line) => calls.logs.push(line),
    now: () => 1000,
    ...over,
  }
}

async function run(d, { b = body(), headers = {}, a = auth } = {}) {
  const out = []
  let head
  await handleChat({ body: b, headers, auth: a, deps: d, config, start: (status, contentType) => (head = { status, contentType }), write: (s) => out.push(s) })
  return { head, text: out.join(""), audit: d.calls.logs[0] }
}

const toolTurn = [
  { messageStart: { role: "assistant" } },
  { contentBlockStart: { contentBlockIndex: 0, start: { toolUse: { toolUseId: "t1", name: "bash" } } } },
  { contentBlockDelta: { contentBlockIndex: 0, delta: { toolUse: { input: '{"command":"npm test"}' } } } },
  { messageStop: { stopReason: "tool_use" } },
  { metadata: { usage: { inputTokens: 900, outputTokens: 20, totalTokens: 920 } } },
]

test("streams OpenAI chunks ending in [DONE] and calls the default model with OpenCode's tools", async () => {
  const d = deps(toolTurn)
  const { head, text, audit } = await run(d, { headers: { "x-opencode-session-id": "ses_abc" } })
  assert.deepEqual(head, { status: 200, contentType: "text/event-stream" })
  assert.match(text, /^data: \{"id":"chatcmpl-/)
  assert.match(text, /"tool_calls":\[\{"index":0,"id":"t1","type":"function","function":\{"name":"bash","arguments":""\}\}\]/)
  assert.match(text, /"finish_reason":"tool_calls"/)
  assert.match(text, /data: \[DONE\]\n\n$/)
  assert.equal(d.calls.converse[0].modelId, "moonshotai.kimi-k2.5")
  assert.equal(d.calls.converse[0].toolConfig.tools[0].toolSpec.name, "bash")
  assert.match(text, /"model":"orchestra\/moonshotai.kimi-k2.5"/)
  assert.equal(audit.status, "ok")
  assert.equal(audit.tool_calls, 1)
  assert.equal(audit.tools_offered, 1)
  assert.deepEqual(audit.usage, { input: 900, output: 20, cache_read: 0 })
  const logged = JSON.stringify(audit)
  assert.ok(!logged.includes("failing test") && !logged.includes("npm test") && !logged.includes("user-123") && !logged.includes("ses_abc"))
})

test("returns one JSON completion when stream is false", async () => {
  const { head, text } = await run(deps(toolTurn), { b: body({ stream: false }) })
  assert.deepEqual(head, { status: 200, contentType: "application/json" })
  const out = JSON.parse(text)
  assert.equal(out.choices[0].message.tool_calls[0].function.arguments, '{"command":"npm test"}')
  assert.equal(out.usage.prompt_tokens, 900)
})

test("refuses before calling the model: local-only, wrong tenant, step-up without frontier tier", async () => {
  for (const [opts, status, code] of [
    [{ headers: { "x-symphony-data-tier": "local-only" } }, 403, "DATA_TIER_DENIED"],
    [{ a: { ...auth, tenantId: "other" } }, 401, "USER_UNAUTHORIZED"],
    [{ b: body({ model: "orchestra-step-up" }) }, 403, "DATA_TIER_DENIED"],
  ]) {
    const d = deps(toolTurn)
    const { head, text, audit } = await run(d, opts)
    assert.equal(head.status, status)
    assert.equal(JSON.parse(text).error.code, code)
    assert.equal(d.calls.converse.length, 0)
    assert.equal(audit.status, code)
  }
})

test("step-up with the frontier tier uses the step-up model", async () => {
  const d = deps(toolTurn)
  await run(d, { b: body({ model: "orchestra-step-up" }), headers: { "x-symphony-data-tier": "frontier-eligible" } })
  assert.equal(d.calls.converse[0].modelId, config.models.stepUp)
})

test("maps Bedrock errors to stable codes, before and after streaming starts", async () => {
  const failing = (name, afterFirst = false) =>
    deps([], {
      converseStream: async function* () {
        if (afterFirst) yield { messageStart: { role: "assistant" } }
        const e = new Error("details that may echo input")
        e.name = name
        throw e
      },
    })
  const before = await run(failing("ThrottlingException"))
  assert.equal(before.head.status, 503)
  assert.equal(JSON.parse(before.text).error.code, "MODEL_UNAVAILABLE")
  const after = await run(failing("ModelStreamErrorException", true))
  assert.equal(after.head.status, 200)
  assert.match(after.text, /data: \{"error":\{"message":"The model stopped unexpectedly","type":"MODEL_UNAVAILABLE","code":"MODEL_UNAVAILABLE"\}\}/)
  assert.ok(!after.text.includes("echo input"))
  const invalid = await run(failing("ValidationException"))
  assert.equal(invalid.head.status, 400)
  assert.equal(JSON.parse(invalid.text).error.code, "INVALID_REQUEST")
})
