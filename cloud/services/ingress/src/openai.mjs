// OpenAI chat completions <-> Bedrock ConverseStream, for Orchestra's model endpoint.
// OpenCode sends its own tools (edit files, run commands); the model returns tool
// calls and OpenCode runs them on the box. No I/O here.

export class ChatError extends Error {
  constructor(code, status, message) {
    super(message)
    this.code = code
    this.status = status
  }
}

const MODELS = new Set(["orchestra", "orchestra-step-up"])
const MAX_MESSAGES = 500

/** Validate the body and resolve the Bedrock model from the requested Orchestra model and the data tier. */
export function parseChat(body, { tier, models }) {
  let value
  try {
    value = typeof body === "string" ? JSON.parse(body) : body
  } catch {
    throw new ChatError("INVALID_REQUEST", 400, "Body must be JSON")
  }
  if (!value || typeof value !== "object") throw new ChatError("INVALID_REQUEST", 400, "Body must be an object")
  if (tier !== "private-cloud" && tier !== "frontier-eligible") {
    throw new ChatError("DATA_TIER_DENIED", 403, "This request must stay on the device")
  }
  const model = value.model ?? "orchestra"
  if (!MODELS.has(model)) throw new ChatError("INVALID_REQUEST", 400, "model must be orchestra or orchestra-step-up")
  if (model === "orchestra-step-up" && tier !== "frontier-eligible") {
    throw new ChatError("DATA_TIER_DENIED", 403, "The step-up model needs data tier frontier-eligible")
  }
  if (!Array.isArray(value.messages) || value.messages.length === 0 || value.messages.length > MAX_MESSAGES) {
    throw new ChatError("INVALID_REQUEST", 400, `messages must hold 1-${MAX_MESSAGES} items`)
  }
  for (const m of value.messages) {
    if (Array.isArray(m?.content) && m.content.some((p) => p?.type && p.type !== "text")) {
      throw new ChatError("INVALID_REQUEST", 400, "Only text content is supported")
    }
  }
  return {
    body: value,
    stream: value.stream === true,
    modelName: model,
    modelId: model === "orchestra-step-up" ? models.stepUp : models.default,
  }
}

function textOf(content) {
  if (typeof content === "string") return content
  if (Array.isArray(content)) return content.map((p) => (typeof p?.text === "string" ? p.text : "")).join("")
  return ""
}

function parseArgs(raw) {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" ? parsed : { value: parsed }
  } catch {
    return { raw }
  }
}

/** OpenAI request body -> ConverseStream input (without modelId). */
export function toConverse(body) {
  const system = []
  const messages = []
  const push = (role, blocks) => {
    if (!blocks.length) return
    const last = messages.at(-1)
    if (last?.role === role) last.content.push(...blocks)
    else messages.push({ role, content: blocks })
  }
  for (const m of body.messages) {
    if (m.role === "system" || m.role === "developer") {
      const text = textOf(m.content)
      if (text) system.push({ text })
    } else if (m.role === "user") {
      const text = textOf(m.content)
      push("user", text ? [{ text }] : [])
    } else if (m.role === "assistant") {
      const blocks = []
      const text = textOf(m.content)
      if (text) blocks.push({ text })
      for (const call of m.tool_calls ?? []) {
        blocks.push({ toolUse: { toolUseId: call.id, name: call.function?.name, input: parseArgs(call.function?.arguments) } })
      }
      push("assistant", blocks)
    } else if (m.role === "tool") {
      push("user", [{ toolResult: { toolUseId: m.tool_call_id, content: [{ text: textOf(m.content) || "(no output)" }] } }])
    }
  }

  const out = { messages, ...(system.length ? { system } : {}) }
  const tools = (body.tools ?? []).filter((t) => t?.type === "function" && t.function?.name)
  if (tools.length) {
    out.toolConfig = {
      tools: tools.map((t) => ({
        toolSpec: {
          name: t.function.name,
          ...(t.function.description ? { description: t.function.description } : {}),
          inputSchema: { json: t.function.parameters ?? { type: "object", properties: {} } },
        },
      })),
    }
    const choice = body.tool_choice
    if (choice === "required") out.toolConfig.toolChoice = { any: {} }
    else if (choice?.type === "function" && choice.function?.name) out.toolConfig.toolChoice = { tool: { name: choice.function.name } }
  }

  const inference = {}
  const maxTokens = body.max_completion_tokens ?? body.max_tokens
  if (Number.isInteger(maxTokens) && maxTokens > 0) inference.maxTokens = maxTokens
  if (typeof body.temperature === "number") inference.temperature = body.temperature
  if (typeof body.top_p === "number") inference.topP = body.top_p
  const stop = typeof body.stop === "string" ? [body.stop] : Array.isArray(body.stop) ? body.stop : []
  if (stop.length) inference.stopSequences = stop.slice(0, 4)
  if (Object.keys(inference).length) out.inferenceConfig = inference
  return out
}

const FINISH = { end_turn: "stop", stop_sequence: "stop", tool_use: "tool_calls", max_tokens: "length", content_filtered: "content_filter", guardrail_intervened: "content_filter" }

/**
 * A stateful translator for one stream: each ConverseStream event in, the
 * OpenAI chat.completion.chunk objects it produces out. Reasoning text is not forwarded.
 */
export function translator({ id, model, created }) {
  const chunk = (choices, extra = {}) => ({ id, object: "chat.completion.chunk", created, model, choices, ...extra })
  const delta = (d, finish = null) => chunk([{ index: 0, delta: d, finish_reason: finish }])
  const toolIndex = new Map()
  let started = false
  return (event) => {
    const out = []
    const start = () => {
      if (!started) {
        started = true
        out.push(delta({ role: "assistant", content: "" }))
      }
    }
    if (event.messageStart) {
      start()
    } else if (event.contentBlockStart?.start?.toolUse) {
      start()
      const { toolUseId, name } = event.contentBlockStart.start.toolUse
      const index = toolIndex.size
      toolIndex.set(event.contentBlockStart.contentBlockIndex, index)
      out.push(delta({ tool_calls: [{ index, id: toolUseId, type: "function", function: { name, arguments: "" } }] }))
    } else if (event.contentBlockDelta) {
      const { contentBlockIndex, delta: d } = event.contentBlockDelta
      if (typeof d?.text === "string" && d.text) {
        start()
        out.push(delta({ content: d.text }))
      } else if (typeof d?.toolUse?.input === "string" && toolIndex.has(contentBlockIndex)) {
        out.push(delta({ tool_calls: [{ index: toolIndex.get(contentBlockIndex), function: { arguments: d.toolUse.input } }] }))
      }
    } else if (event.messageStop) {
      start()
      out.push(delta({}, FINISH[event.messageStop.stopReason] ?? "stop"))
    } else if (event.metadata?.usage) {
      const u = event.metadata.usage
      out.push(
        chunk([], {
          usage: {
            prompt_tokens: u.inputTokens ?? 0,
            completion_tokens: u.outputTokens ?? 0,
            total_tokens: u.totalTokens ?? (u.inputTokens ?? 0) + (u.outputTokens ?? 0),
            ...(u.cacheReadInputTokens ? { prompt_tokens_details: { cached_tokens: u.cacheReadInputTokens } } : {}),
          },
        }),
      )
    }
    return out
  }
}

/** ConverseStream events -> OpenAI chunks (synchronous, for tests and stream: false). */
export function* streamChunks(events, meta) {
  const translate = translator(meta)
  for (const event of events) yield* translate(event)
}

/** Collapse chunks into a single chat.completion (for stream: false). */
export function aggregate(chunks) {
  let base = null
  let content = ""
  let finish = null
  let usage
  const calls = []
  for (const c of chunks) {
    base ??= c
    if (c.usage) usage = c.usage
    const choice = c.choices[0]
    if (!choice) continue
    if (choice.delta?.content) content += choice.delta.content
    for (const t of choice.delta?.tool_calls ?? []) {
      calls[t.index] ??= { id: t.id, type: "function", function: { name: t.function?.name ?? "", arguments: "" } }
      if (t.id) calls[t.index].id = t.id
      if (t.function?.name) calls[t.index].function.name = t.function.name
      calls[t.index].function.arguments += t.function?.arguments ?? ""
    }
    if (choice.finish_reason) finish = choice.finish_reason
  }
  const message = { role: "assistant", content: calls.length && !content ? null : content, ...(calls.length ? { tool_calls: calls } : {}) }
  return {
    id: base?.id,
    object: "chat.completion",
    created: base?.created,
    model: base?.model,
    choices: [{ index: 0, message, finish_reason: finish ?? "stop" }],
    ...(usage ? { usage } : {}),
  }
}
