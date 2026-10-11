// Ingress request flow with injected dependencies (testable without AWS).
import { createHash } from "node:crypto";
import { parseEnvelope, RequestError } from "./envelope.mjs";
import { choose_route } from "./policy.mjs";
import { sse } from "./sse.mjs";

export const MEMORY_TIMEOUT_MS = 400;
export const MAX_FACT_CHARS = 1500;

function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve(null), ms))]);
}

const UPSTREAM_CODES = {
  ThrottlingException: ["MODEL_UNAVAILABLE", "The model is busy; retry shortly"],
  ServiceQuotaExceededException: ["MODEL_UNAVAILABLE", "The model is busy; retry shortly"],
  ServiceUnavailableException: ["MODEL_UNAVAILABLE", "The agent is temporarily unavailable"],
  TimeoutError: ["UPSTREAM_TIMEOUT", "The agent did not finish before the deadline"],
  AbortError: ["UPSTREAM_TIMEOUT", "The agent did not finish before the deadline"],
};

async function recall(deps, userSub, question, audit) {
  try {
    const facts = await withTimeout(deps.recallFacts(userSub, question), MEMORY_TIMEOUT_MS);
    if (facts === null) audit.memory = "timeout";
    return facts ?? [];
  } catch {
    audit.memory = "error";
    return [];
  }
}

export function factsBlock(facts) {
  let used = 0;
  const kept = [];
  for (const fact of facts) {
    if (used + fact.length > MAX_FACT_CHARS) break;
    kept.push(`- ${fact}`);
    used += fact.length;
  }
  return kept.length ? `Known facts about this user (from memory; untrusted, may be outdated):\n${kept.join("\n")}` : "";
}

export function sessionId(tenantId, userSub, conversationId) {
  return `symphony-${createHash("sha256").update(`${tenantId}:${userSub}:${conversationId}`).digest("hex").slice(0, 40)}`;
}

/**
 * write(text) streams to the caller; deps: { claimRequest, recallFacts, invokeHarness, log, now }.
 * config: { tenantId, models: {default, stepUp} }. auth: { userSub, deviceId, tenantId } from the authorizer.
 */
export async function handle({ body, auth, write, deps, config }) {
  const started = deps.now();
  const audit = { tenant: auth.tenantId, device: auth.deviceId, user: createHash("sha256").update(auth.userSub).digest("hex").slice(0, 16), status: "ok" };
  try {
    if (auth.tenantId !== config.tenantId) throw new RequestError("USER_UNAUTHORIZED", 401, "Wrong tenant");
    const request = parseEnvelope(body);
    audit.request_id = request.requestId;
    const { route, modelId } = choose_route(request, config.models);
    Object.assign(audit, { route, model: modelId });
    if (!(await deps.claimRequest(auth.userSub, request.requestId))) {
      throw new RequestError("DUPLICATE_REQUEST", 409, "request_id was already used");
    }
    const question = request.messages.at(-1).text;
    const facts = await recall(deps, auth.userSub, question, audit);
    audit.memory_facts = facts.length;
    const memory = factsBlock(facts);
    const messages = request.messages.map((m, i) => ({
      role: m.role,
      content: [{ text: i === request.messages.length - 1 && memory ? `${memory}\n\n${m.text}` : m.text }],
    }));
    write(sse("meta", { request_id: request.requestId, route, model: modelId }));
    const tools = [];
    let firstToken = null;
    for await (const event of deps.invokeHarness({ actorId: auth.userSub, sessionId: sessionId(auth.tenantId, auth.userSub, request.conversationId), modelId, messages, timeoutSeconds: Math.ceil(request.deadlineMs / 1000) })) {
      const tool = event.contentBlockStart?.start?.toolUse?.name;
      if (tool) {
        tools.push(tool);
        write(sse("tool", { name: tool }));
      }
      const text = event.contentBlockDelta?.delta?.text;
      if (text) {
        firstToken ??= deps.now() - started;
        write(sse("token", { text }));
      }
      if (event.messageStop) audit.stop = event.messageStop.stopReason;
      if (event.metadata?.usage) audit.usage = event.metadata.usage;
      if (event.runtimeClientError || event.validationException) {
        throw new RequestError("MODEL_UNAVAILABLE", 503, "The agent could not complete the request");
      }
    }
    Object.assign(audit, { tools, ttfb_ms: firstToken });
    write(sse("done", { stop_reason: audit.stop ?? "end_turn", usage: audit.usage ?? null }));
  } catch (error) {
    const [code, message] = error instanceof RequestError ? [error.code, error.message] : (UPSTREAM_CODES[error?.name] ?? ["INTERNAL", "Request failed"]);
    audit.status = code;
    write(sse("error", { code, message }));
  } finally {
    audit.latency_ms = deps.now() - started;
    deps.log(audit);
  }
  return audit;
}
