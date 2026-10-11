// Orchestra's model endpoint: OpenAI chat completions with the caller's tools,
// answered by an allowlisted Bedrock model in the tenant account. Injected
// dependencies keep it testable without AWS.
import { createHash, randomUUID } from "node:crypto"
import { aggregate, ChatError, parseChat, streamChunks, toConverse, translator } from "./openai.mjs"

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

/**
 * start(status, contentType) opens the response; write(text) appends to it.
 * deps: { converseStream(input) -> async iterable of ConverseStream events, log(line), now() }.
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
    Object.assign(audit, { model: request.modelId, tools_offered: input.toolConfig?.tools.length ?? 0, stream: request.stream })
    const meta = { id: `chatcmpl-${randomUUID()}`, model: `orchestra/${request.modelId}`, created: Math.floor(began / 1000) }

    let toolCalls = 0
    const observe = (event) => {
      if (event.contentBlockStart?.start?.toolUse) toolCalls += 1
      if (event.messageStop) audit.stop = event.messageStop.stopReason
      if (event.metadata?.usage) {
        const u = event.metadata.usage
        audit.usage = { input: u.inputTokens ?? 0, output: u.outputTokens ?? 0, cache_read: u.cacheReadInputTokens ?? 0 }
      }
    }

    const iterator = deps.converseStream(input)[Symbol.asyncIterator]()
    if (request.stream) {
      // Wait for the first event before opening, so an upfront Bedrock error gets a real status code.
      let next = await iterator.next()
      open(200, "text/event-stream")
      const translate = translator(meta)
      for (; !next.done; next = await iterator.next()) {
        observe(next.value)
        for (const chunk of translate(next.value)) {
          audit.ttfb_ms ??= deps.now() - began
          write(`data: ${JSON.stringify(chunk)}\n\n`)
        }
      }
      write("data: [DONE]\n\n")
    } else {
      const events = []
      for (let next = await iterator.next(); !next.done; next = await iterator.next()) {
        observe(next.value)
        events.push(next.value)
      }
      open(200, "application/json")
      write(JSON.stringify(aggregate(streamChunks(events, meta))))
    }
    audit.tool_calls = toolCalls
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
