// Lambda entrypoint for Orchestra's model endpoint (API Gateway REST, AWS_PROXY, STREAM).
import { BedrockRuntimeClient, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime"
import { handleChat } from "./chat.mjs"

const env = process.env
const bedrock = new BedrockRuntimeClient({})
const config = { tenantId: env.TENANT_ID, models: { default: env.DEFAULT_MODEL_ID, stepUp: env.STEP_UP_MODEL_ID } }

const deps = {
  now: () => Date.now(),
  log: (line) => console.log(JSON.stringify({ type: "symphony.chat.audit", ...line })),
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
