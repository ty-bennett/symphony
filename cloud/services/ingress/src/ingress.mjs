// Lambda entrypoint: API Gateway REST (AWS_PROXY, responseTransferMode STREAM).
import { BedrockAgentCoreClient, InvokeHarnessCommand, RetrieveMemoryRecordsCommand } from "@aws-sdk/client-bedrock-agentcore";
import { DynamoDBClient, PutItemCommand } from "@aws-sdk/client-dynamodb";
import { handle } from "./core.mjs";

const env = process.env;
const agentcore = new BedrockAgentCoreClient({});
const dynamodb = new DynamoDBClient({});
const config = { tenantId: env.TENANT_ID, models: { default: env.DEFAULT_MODEL_ID, stepUp: env.STEP_UP_MODEL_ID }, systemPrompt: env.SYSTEM_PROMPT };
const ERROR_STATUS = { DATA_TIER_DENIED: 403, USER_UNAUTHORIZED: 401, INVALID_REQUEST: 400, DUPLICATE_REQUEST: 409, MODEL_UNAVAILABLE: 503, UPSTREAM_TIMEOUT: 504 };

const deps = {
  now: () => Date.now(),
  log: (line) => console.log(JSON.stringify({ type: "symphony.ingress.audit", ...line })),
  claimRequest: async (userSub, requestId) => {
    try {
      await dynamodb.send(new PutItemCommand({
        TableName: env.IDEMPOTENCY_TABLE,
        Item: { pk: { S: `${userSub}#${requestId}` }, expires_at: { N: String(Math.floor(Date.now() / 1000) + 86400) } },
        ConditionExpression: "attribute_not_exists(pk)",
      }));
      return true;
    } catch (error) {
      if (error.name === "ConditionalCheckFailedException") return false;
      throw error;
    }
  },
  recallFacts: async (userSub, question) => {
    const out = await agentcore.send(new RetrieveMemoryRecordsCommand({
      memoryId: env.MEMORY_ID,
      namespace: `/users/${userSub}/facts`,
      searchCriteria: { searchQuery: question.slice(0, 500), topK: 5 },
    }));
    return (out.memoryRecordSummaries ?? []).map((r) => r.content?.text).filter(Boolean);
  },
  invokeHarness: async function* ({ actorId, sessionId, modelId, messages, systemPrompt, timeoutSeconds }) {
    const out = await agentcore.send(new InvokeHarnessCommand({
      harnessArn: env.HARNESS_ARN,
      runtimeSessionId: sessionId,
      actorId,
      messages,
      timeoutSeconds,
      ...(systemPrompt ? { systemPrompt } : {}),
      ...(modelId === env.DEFAULT_MODEL_ID ? {} : { model: { bedrockModelConfig: { modelId } } }),
    }));
    for await (const event of out.stream) yield event;
  },
};

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const auth = event.requestContext?.authorizer ?? {};
  let stream = null;
  const write = (text) => {
    if (!stream) {
      // Pre-stream errors get their HTTP status; once streaming starts, errors are SSE events.
      const code = /"code":"([A-Z_]+)"/.exec(text)?.[1];
      const status = text.startsWith("event: error") ? (ERROR_STATUS[code] ?? 500) : 200;
      stream = awslambda.HttpResponseStream.from(responseStream, {
        statusCode: status,
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no" },
      });
    }
    stream.write(text);
  };
  await handle({ body: event.body, auth: { tenantId: auth.tenant_id, deviceId: auth.device_id, userSub: auth.user_sub }, write, deps, config });
  (stream ?? responseStream).end();
});
