// Orchestra's model endpoint: OpenAI chat completions with the caller's tools,
// answered by an allowlisted Bedrock model in the tenant account. Injected
// dependencies keep it testable without AWS.
import { createHash, randomUUID } from "node:crypto"
import { aggregate, ChatError, parseChat, toConverse } from "./openai.mjs"

const hash = (value) => (value ? createHash("sha256").update(value).digest("hex").slice(0, 16) : undefined)

const UPSTREAM = {
  ThrottlingException: ["MODEL_UNAVAILABLE", 503, "The model is busy; retry shortly"],
  ServiceQuotaExceededException: ["MODEL_UNAVAILABLE", 503, "The model is busy; retry shortly"],
  ServiceUnavailableException: ["MODEL_UNAVAILABLE", 503, "The model is temporarily unavailable"],
  ModelNotReadyException: ["MODEL_UNAVAILABLE", 503, "The model is temporarily unavailable"],
  AccessDeniedException: ["MODEL_UNAVAILABLE", 503, "The model is not available to this account"],
  ModelStreamErrorException: ["MODEL_UNAVAILABLE", 503, "The model stopped unexpectedly"],
  ModelTimeoutException: ["UPSTREAM_TIMEOUT", 504, "The model did not answer in time"],
  TimeoutError: ["UPSTREAM_TIMEOUT", 504, "The model did not answer in time"],
  ValidationException: ["INVALID_REQUEST", 400, "The model rejected the request"],
}

const errorBody = (code, message) => JSON.stringify({ error: { message, type: code, code } })

/** Tools Orchestra runs itself, in the tenant account, as the signed-in person. */
export const CLOUD_TOOLS = [
  {
    name: "orchestra_search_documents",
    description: "Search the business's approved documents in its private cloud. Returns matching passages with their document keys. Use this for questions about company policies, records, or data files.",
    schema: { type: "object", properties: { query: { type: "string" }, max_results: { type: "integer", minimum: 1, maximum: 5 } }, required: ["query"] },
  },
  {
    name: "orchestra_browse",
    description: "Open a public web page in Orchestra's private, recorded browser (allowlisted sites only) and answer a question about it. Use this for anything on the web; it is the only way to read web pages.",
    schema: { type: "object", properties: { url: { type: "string" }, question: { type: "string" } }, required: ["url", "question"] },
  },
  {
    name: "orchestra_analyze",
    description: "Run Python in Orchestra's sandbox (no internet) to calculate or analyze data you provide. Pass the data in full.",
    schema: { type: "object", properties: { task: { type: "string" }, data: { type: "string" } }, required: ["task"] },
  },
]
const CLOUD = new Set(CLOUD_TOOLS.map((t) => t.name))
export const MAX_CLOUD_ROUNDS = 1

const parseArgs = (raw) => {
  try {
    const v = JSON.parse(raw || "{}")
    return v && typeof v === "object" ? v : {}
  } catch {
    return {}
  }
}

/**
 * One model turn as seen by the client, with Orchestra's tools run in between.
 * Yields { text } as it streams, then { end: { stop, calls } } once.
 */
async function* rounds(input, { deps, auth, usage, counters }) {
  for (let round = 0; ; round++) {
    const blocks = new Map()
    let stop = "end_turn"
    let text = ""
    for await (const event of deps.converseStream(input)) {
      const d = event.contentBlockDelta
      if (typeof d?.delta?.text === "string" && d.delta.text) {
        text += d.delta.text
        yield { text: d.delta.text }
      } else if (typeof d?.delta?.toolUse?.input === "string" && blocks.has(d.contentBlockIndex)) {
        blocks.get(d.contentBlockIndex).args += d.delta.toolUse.input
      }
      const tool = event.contentBlockStart?.start?.toolUse
      if (tool) blocks.set(event.contentBlockStart.contentBlockIndex, { id: tool.toolUseId, name: tool.name, args: "" })
      if (event.messageStop) stop = event.messageStop.stopReason
      if (event.metadata?.usage) {
        usage.input += event.metadata.usage.inputTokens ?? 0
        usage.output += event.metadata.usage.outputTokens ?? 0
        usage.cache_read += event.metadata.usage.cacheReadInputTokens ?? 0
      }
    }
    const calls = [...blocks.values()]
    const cloud = calls.filter((c) => CLOUD.has(c.name))
    const client = calls.filter((c) => !CLOUD.has(c.name))
    if (stop !== "tool_use" || client.length || !cloud.length) {
      yield { end: { stop, calls: client } }
      return
    }
    if (round >= MAX_CLOUD_ROUNDS) {
      yield { text: "\n\n(Orchestra stopped after one tool step; ask again to continue.)" }
      yield { end: { stop: "end_turn", calls: [] } }
      return
    }
    const results = []
    for (const call of cloud) {
      counters.cloudTools += 1
      let output
      try {
        const result = await deps.runCloudTool(call.name, parseArgs(call.args), auth)
        usage.input += result.usage?.input ?? 0
        usage.output += result.usage?.output ?? 0
        output = result.text || "(no result)"
      } catch (error) {
        counters.errors.push(`${call.name}:${error?.name ?? "Error"}`)
        output = "This Orchestra tool is unavailable right now."
      }
      results.push({ toolResult: { toolUseId: call.id, content: [{ text: output.slice(0, 20000) }] } })
    }
    input.messages.push(
      { role: "assistant", content: [...(text ? [{ text }] : []), ...cloud.map((c) => ({ toolUse: { toolUseId: c.id, name: c.name, input: parseArgs(c.args) } }))] },
      { role: "user", content: results },
    )
  }
}

const FINISH = { end_turn: "stop", stop_sequence: "stop", tool_use: "tool_calls", max_tokens: "length", content_filtered: "content_filter", guardrail_intervened: "content_filter" }

/**
 * start(status, contentType) opens the response; write(text) appends to it.
 * deps: { converseStream(input), runCloudTool(name, args, auth) -> {text, usage}, log(line), now() }.
 */
export async function handleChat({ body, headers, auth, deps, config, start, write }) {
  const began = deps.now()
  const header = (name) => headers?.[name] ?? headers?.[name.toLowerCase()]
  const audit = {
    tenant: auth.tenantId,
    device: auth.deviceId,
    user: hash(auth.userSub),
    session: hash(header("x-opencode-session-id")),
    status: "ok",
  }
  let opened = false
  const open = (status, type) => {
    opened = true
    start(status, type)
  }
  try {
    if (auth.tenantId !== config.tenantId) throw new ChatError("USER_UNAUTHORIZED", 401, "Wrong tenant")
    const request = parseChat(body, { tier: header("x-symphony-data-tier") ?? "private-cloud", models: config.models })
    const input = { modelId: request.modelId, ...toConverse(request.body) }
    const clientTools = input.toolConfig?.tools ?? []
    input.toolConfig = {
      ...(input.toolConfig ?? {}),
      tools: [...clientTools, ...CLOUD_TOOLS.map((t) => ({ toolSpec: { name: t.name, description: t.description, inputSchema: { json: t.schema } } }))],
    }
    Object.assign(audit, { model: request.modelId, tools_offered: input.toolConfig.tools.length, stream: request.stream })
    const meta = { id: `chatcmpl-${randomUUID()}`, model: `orchestra/${request.modelId}`, created: Math.floor(began / 1000) }
    const chunk = (choices, extra = {}) => ({ id: meta.id, object: "chat.completion.chunk", created: meta.created, model: meta.model, choices, ...extra })
    const usage = { input: 0, output: 0, cache_read: 0 }
    const counters = { cloudTools: 0, errors: [] }

    const turn = rounds(input, { deps, auth, usage, counters })[Symbol.asyncIterator]()
    // Wait for the first piece before opening, so an upfront Bedrock error gets a real status code.
    let next = await turn.next()
    const chunks = []
    const emit = (c) => {
      if (request.stream) {
        audit.ttfb_ms ??= deps.now() - began
        write(`data: ${JSON.stringify(c)}\n\n`)
      } else chunks.push(c)
    }
    if (request.stream) open(200, "text/event-stream")
    emit(chunk([{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }]))
    let end = { stop: "end_turn", calls: [] }
    for (; !next.done; next = await turn.next()) {
      if (next.value.text) emit(chunk([{ index: 0, delta: { content: next.value.text }, finish_reason: null }]))
      if (next.value.end) end = next.value.end
    }
    if (end.calls.length) {
      emit(chunk([{ index: 0, delta: { tool_calls: end.calls.map((c, i) => ({ index: i, id: c.id, type: "function", function: { name: c.name, arguments: c.args } })) }, finish_reason: null }]))
    }
    emit(chunk([{ index: 0, delta: {}, finish_reason: end.calls.length ? "tool_calls" : (FINISH[end.stop] ?? "stop") }]))
    emit(
      chunk([], {
        usage: {
          prompt_tokens: usage.input,
          completion_tokens: usage.output,
          total_tokens: usage.input + usage.output,
          ...(usage.cache_read ? { prompt_tokens_details: { cached_tokens: usage.cache_read } } : {}),
        },
      }),
    )
    if (request.stream) write("data: [DONE]\n\n")
    else {
      open(200, "application/json")
      write(JSON.stringify(aggregate(chunks)))
    }
    Object.assign(audit, { tool_calls: end.calls.length, cloud_tools: counters.cloudTools, stop: end.stop, usage, ...(counters.errors.length ? { cloud_tool_errors: counters.errors } : {}) })
  } catch (error) {
    const [code, status, message] =
      error instanceof ChatError ? [error.code, error.status, error.message] : (UPSTREAM[error?.name] ?? ["INTERNAL", 500, "Request failed"])
    audit.status = code
    if (opened) {
      write(`data: ${errorBody(code, message)}\n\n`)
    } else {
      open(status, "application/json")
      write(errorBody(code, message))
    }
  } finally {
    audit.latency_ms = deps.now() - began
    deps.log(audit)
  }
}
