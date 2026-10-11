// Lambda entrypoint for Orchestra's model endpoint (API Gateway REST, AWS_PROXY, STREAM).
import { createHash, randomUUID } from "node:crypto"
import { BedrockAgentCoreClient, InvokeHarnessCommand } from "@aws-sdk/client-bedrock-agentcore"
import { BedrockAgentRuntimeClient, RetrieveCommand } from "@aws-sdk/client-bedrock-agent-runtime"
import { BedrockRuntimeClient, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime"
import { handleChat } from "./chat.mjs"

const env = process.env
const bedrock = new BedrockRuntimeClient({})
const agentcore = new BedrockAgentCoreClient({})
const knowledge = new BedrockAgentRuntimeClient({})

// Browse and analyze go through the tenant agent (its Browser and Code Interpreter), as the caller.
async function askAgent(prompt, auth) {
  const out = await agentcore.send(
    new InvokeHarnessCommand({
      harnessArn: env.HARNESS_ARN,
      runtimeSessionId: `symphony-tool-${createHash("sha256").update(auth.userSub).digest("hex").slice(0, 12)}-${randomUUID()}`,
      actorId: auth.userSub,
      messages: [{ role: "user", content: [{ text: prompt }] }],
    }),
  )
  let text = ""
  const usage = { input: 0, output: 0 }
  for await (const event of out.stream) {
    text += event.contentBlockDelta?.delta?.text ?? ""
    if (event.metadata?.usage) {
      usage.input += event.metadata.usage.inputTokens ?? 0
      usage.output += event.metadata.usage.outputTokens ?? 0
    }
  }
  return { text: text.trim(), usage }
}

const cloudTools = {
  orchestra_search_documents: async ({ query, max_results }) => {
    const out = await knowledge.send(
      new RetrieveCommand({
        knowledgeBaseId: env.KNOWLEDGE_BASE_ID,
        retrievalQuery: { text: String(query ?? "").slice(0, 1000) },
        retrievalConfiguration: { vectorSearchConfiguration: { numberOfResults: Math.min(Math.max(Number(max_results) || 5, 1), 5) } },
      }),
    )
    const passages = (out.retrievalResults ?? []).map((r) => {
      const uri = r.location?.s3Location?.uri ?? ""
      const key = uri.replace(/^s3:\/\/[^/]+\//, "")
      return `${key}:\n${r.content?.text ?? ""}`
    })
    return { text: passages.join("\n\n") || "No matching documents.", usage: { input: 0, output: 0 } }
  },
  orchestra_browse: ({ url, question }, auth) =>
    askAgent(`Use the web tool to open ${url} and answer this from the page: ${question}\nGive the answer and the URL. If the site is blocked, say so.`, auth),
  orchestra_analyze: ({ task, data }, auth) =>
    askAgent(`Use the code tool to do this and report the result:\n${task}${data ? `\n\nData:\n${data}` : ""}`, auth),
}
const config = { tenantId: env.TENANT_ID, models: { default: env.DEFAULT_MODEL_ID, stepUp: env.STEP_UP_MODEL_ID } }

const deps = {
  now: () => Date.now(),
  log: (line) => console.log(JSON.stringify({ type: "symphony.chat.audit", ...line })),
  runCloudTool: (name, args, auth) => cloudTools[name](args, auth),
  converseStream: async function* (input) {
    const out = await bedrock.send(new ConverseStreamCommand(input))
    for await (const event of out.stream) yield event
  },
}

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const auth = event.requestContext?.authorizer ?? {}
  let stream = responseStream
  await handleChat({
    body: event.body,
    headers: Object.fromEntries(Object.entries(event.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v])),
    auth: { tenantId: auth.tenant_id, deviceId: auth.device_id, userSub: auth.user_sub },
    deps,
    config,
    start: (statusCode, contentType) => {
      stream = awslambda.HttpResponseStream.from(responseStream, {
        statusCode,
        headers: { "Content-Type": contentType, "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
      })
    },
    write: (text) => stream.write(text),
  })
  stream.end()
})
