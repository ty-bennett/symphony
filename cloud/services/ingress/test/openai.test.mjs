import { test } from "node:test"
import assert from "node:assert/strict"
import { toConverse, streamChunks, aggregate, parseChat, ChatError } from "../src/openai.mjs"

const tools = [
  {
    type: "function",
    function: { name: "bash", description: "Run a shell command", parameters: { type: "object", properties: { command: { type: "string" } }, required: ["command"] } },
  },
]

test("translates system, user, assistant tool calls, and tool results into Converse", () => {
  const req = toConverse({
    messages: [
      { role: "system", content: "You are a coding agent." },
      { role: "user", content: [{ type: "text", text: "Run the tests" }] },
      { role: "assistant", content: "Running them.", tool_calls: [{ id: "call_1", type: "function", function: { name: "bash", arguments: '{"command":"npm test"}' } }] },
      { role: "tool", tool_call_id: "call_1", content: "3 passing" },
      { role: "tool", tool_call_id: "call_2", content: [{ type: "text", text: "ok" }] },
      { role: "user", content: "Great, now lint" },
    ],
    tools,
    max_tokens: 4096,
    temperature: 0.2,
    stop: ["</done>"],
  })
  assert.deepEqual(req.system, [{ text: "You are a coding agent." }])
  assert.deepEqual(req.messages[0], { role: "user", content: [{ text: "Run the tests" }] })
  assert.deepEqual(req.messages[1], {
    role: "assistant",
    content: [{ text: "Running them." }, { toolUse: { toolUseId: "call_1", name: "bash", input: { command: "npm test" } } }],
  })
  // Consecutive tool results and the next user turn merge into one user message (Converse alternates roles).
  assert.deepEqual(req.messages[2], {
    role: "user",
    content: [
      { toolResult: { toolUseId: "call_1", content: [{ text: "3 passing" }] } },
      { toolResult: { toolUseId: "call_2", content: [{ text: "ok" }] } },
      { text: "Great, now lint" },
    ],
  })
  assert.equal(req.messages.length, 3)
  assert.deepEqual(req.toolConfig.tools[0], {
    toolSpec: { name: "bash", description: "Run a shell command", inputSchema: { json: tools[0].function.parameters } },
  })
  assert.deepEqual(req.inferenceConfig, { maxTokens: 4096, temperature: 0.2, stopSequences: ["</done>"] })
})

test("maps tool_choice and drops empty text blocks", () => {
  assert.deepEqual(toConverse({ messages: [{ role: "user", content: "x" }], tools, tool_choice: "required" }).toolConfig.toolChoice, { any: {} })
  assert.deepEqual(toConverse({ messages: [{ role: "user", content: "x" }], tools, tool_choice: { type: "function", function: { name: "bash" } } }).toolConfig.toolChoice, { tool: { name: "bash" } })
  const r = toConverse({ messages: [{ role: "user", content: "x" }, { role: "assistant", content: "", tool_calls: [{ id: "c", type: "function", function: { name: "bash", arguments: "" } }] }], tools })
  assert.deepEqual(r.messages[1].content, [{ toolUse: { toolUseId: "c", name: "bash", input: {} } }])
  assert.equal(toConverse({ messages: [{ role: "user", content: "x" }] }).toolConfig, undefined)
})

test("streams Converse events as OpenAI chunks with tool calls, finish reason, and usage", () => {
  const events = [
    { messageStart: { role: "assistant" } },
    { contentBlockDelta: { contentBlockIndex: 0, delta: { text: "Let me run it." } } },
    { contentBlockStart: { contentBlockIndex: 1, start: { toolUse: { toolUseId: "tooluse_9", name: "bash" } } } },
    { contentBlockDelta: { contentBlockIndex: 1, delta: { toolUse: { input: '{"command":' } } } },
    { contentBlockDelta: { contentBlockIndex: 1, delta: { toolUse: { input: '"npm test"}' } } } },
    { contentBlockDelta: { contentBlockIndex: 2, delta: { reasoningContent: { text: "thinking" } } } },
    { messageStop: { stopReason: "tool_use" } },
    { metadata: { usage: { inputTokens: 1200, outputTokens: 40, totalTokens: 1240, cacheReadInputTokens: 800 } } },
  ]
  const chunks = [...streamChunks(events, { id: "chatcmpl-1", model: "orchestra/kimi-k2.5", created: 1 })]
  const deltas = chunks.map((c) => c.choices[0]?.delta)
  assert.deepEqual(deltas[0], { role: "assistant", content: "" })
  assert.deepEqual(deltas[1], { content: "Let me run it." })
  assert.deepEqual(deltas[2], { tool_calls: [{ index: 0, id: "tooluse_9", type: "function", function: { name: "bash", arguments: "" } }] })
  assert.deepEqual(deltas[3], { tool_calls: [{ index: 0, function: { arguments: '{"command":' } }] })
  assert.deepEqual(deltas[4], { tool_calls: [{ index: 0, function: { arguments: '"npm test"}' } }] })
  const finish = chunks.find((c) => c.choices[0]?.finish_reason)
  assert.equal(finish.choices[0].finish_reason, "tool_calls")
  const last = chunks.at(-1)
  assert.deepEqual(last.choices, [])
  assert.deepEqual(last.usage, { prompt_tokens: 1200, completion_tokens: 40, total_tokens: 1240, prompt_tokens_details: { cached_tokens: 800 } })
  assert.ok(chunks.every((c) => c.id === "chatcmpl-1" && c.object === "chat.completion.chunk" && c.model === "orchestra/kimi-k2.5"))
  assert.ok(!JSON.stringify(chunks).includes("thinking"), "reasoning text is not sent")
})

test("aggregates a stream into one chat.completion for stream: false", () => {
  const events = [
    { contentBlockDelta: { contentBlockIndex: 0, delta: { text: "Hi " } } },
    { contentBlockDelta: { contentBlockIndex: 0, delta: { text: "there" } } },
    { messageStop: { stopReason: "end_turn" } },
    { metadata: { usage: { inputTokens: 5, outputTokens: 2, totalTokens: 7 } } },
  ]
  const out = aggregate(streamChunks(events, { id: "c", model: "m", created: 1 }))
  assert.equal(out.object, "chat.completion")
  assert.deepEqual(out.choices[0].message, { role: "assistant", content: "Hi there" })
  assert.equal(out.choices[0].finish_reason, "stop")
  assert.equal(out.usage.total_tokens, 7)
})

test("parseChat validates the body and picks the model and tier", () => {
  const models = { default: "moonshotai.kimi-k2.5", stepUp: "us.anthropic.claude-opus-4-5-20251101-v1:0" }
  const ok = parseChat(JSON.stringify({ model: "orchestra", messages: [{ role: "user", content: "hi" }], stream: true }), { tier: "private-cloud", models })
  assert.equal(ok.modelId, "moonshotai.kimi-k2.5")
  assert.equal(ok.stream, true)
  const up = parseChat(JSON.stringify({ model: "orchestra-step-up", messages: [{ role: "user", content: "hi" }] }), { tier: "frontier-eligible", models })
  assert.equal(up.modelId, models.stepUp)
  const refuse = (body, tier, code, status) =>
    assert.throws(() => parseChat(body, { tier, models }), (e) => e instanceof ChatError && e.code === code && e.status === status)
  refuse(JSON.stringify({ model: "orchestra-step-up", messages: [{ role: "user", content: "hi" }] }), "private-cloud", "DATA_TIER_DENIED", 403)
  refuse(JSON.stringify({ model: "orchestra", messages: [{ role: "user", content: "hi" }] }), "local-only", "DATA_TIER_DENIED", 403)
  refuse(JSON.stringify({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] }), "private-cloud", "INVALID_REQUEST", 400)
  refuse(JSON.stringify({ model: "orchestra", messages: [] }), "private-cloud", "INVALID_REQUEST", 400)
  refuse("{nope", "private-cloud", "INVALID_REQUEST", 400)
  refuse(JSON.stringify({ model: "orchestra", messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "x" } }] }] }), "private-cloud", "INVALID_REQUEST", 400)
})
