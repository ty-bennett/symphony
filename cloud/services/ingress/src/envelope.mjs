// Request envelope v1 validation. The tenant never comes from the body.
export const DATA_TIERS = new Set(["local-only", "private-cloud", "frontier-eligible"]);
export const MODEL_PREFERENCES = new Set(["default", "step-up"]);
const ID = /^[A-Za-z0-9][A-Za-z0-9-]{7,63}$/;
const MAX_MESSAGES = 20;
const MAX_TEXT = 16000;

export class RequestError extends Error {
  constructor(code, status, message) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export function parseEnvelope(body) {
  let value;
  try {
    value = typeof body === "string" ? JSON.parse(body) : body;
  } catch {
    throw new RequestError("INVALID_REQUEST", 400, "Body must be JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new RequestError("INVALID_REQUEST", 400, "Body must be an object");
  }
  const allowed = new Set(["version", "request_id", "conversation_id", "data_tier", "deadline_ms", "model_preference", "messages"]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new RequestError("INVALID_REQUEST", 400, `Unknown field ${key}`);
  }
  if (value.version !== 1) throw new RequestError("INVALID_REQUEST", 400, "version must be 1");
  for (const key of ["request_id", "conversation_id"]) {
    if (typeof value[key] !== "string" || !ID.test(value[key])) {
      throw new RequestError("INVALID_REQUEST", 400, `${key} must be 8-64 letters, digits, or hyphens`);
    }
  }
  if (!DATA_TIERS.has(value.data_tier)) throw new RequestError("INVALID_REQUEST", 400, "Unknown data_tier");
  const preference = value.model_preference ?? "default";
  if (!MODEL_PREFERENCES.has(preference)) throw new RequestError("INVALID_REQUEST", 400, "Unknown model_preference");
  const deadline = value.deadline_ms ?? 120000;
  if (!Number.isInteger(deadline) || deadline < 1000 || deadline > 300000) {
    throw new RequestError("INVALID_REQUEST", 400, "deadline_ms must be 1000-300000");
  }
  const messages = value.messages;
  if (!Array.isArray(messages) || messages.length < 1 || messages.length > MAX_MESSAGES) {
    throw new RequestError("INVALID_REQUEST", 400, "messages must hold 1-20 items");
  }
  for (const message of messages) {
    if (!message || typeof message !== "object" || Object.keys(message).some((k) => k !== "role" && k !== "text")) {
      throw new RequestError("INVALID_REQUEST", 400, "Each message has only role and text");
    }
    if (!["user", "assistant"].includes(message.role) || typeof message.text !== "string" || !message.text || message.text.length > MAX_TEXT) {
      throw new RequestError("INVALID_REQUEST", 400, "Each message needs role user|assistant and 1-16000 chars of text");
    }
  }
  if (messages.at(-1).role !== "user") throw new RequestError("INVALID_REQUEST", 400, "The last message must be from the user");
  return { requestId: value.request_id, conversationId: value.conversation_id, dataTier: value.data_tier, modelPreference: preference, deadlineMs: deadline, messages };
}
