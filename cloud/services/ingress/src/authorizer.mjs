// Lambda REQUEST authorizer: device certificate (mTLS) + Cognito user token.
import { DynamoDBClient, GetItemCommand } from "@aws-sdk/client-dynamodb";
import { CognitoJwtVerifier } from "aws-jwt-verify";
import { authorize } from "./authorize.mjs";

const env = process.env;
const dynamodb = new DynamoDBClient({});
const verifier = CognitoJwtVerifier.create({ userPoolId: env.USER_POOL_ID, tokenUse: "access", clientId: env.APP_CLIENT_ID });

const deps = {
  now: () => Date.now(),
  verifyToken: (token) => verifier.verify(token),
  getDevice: async (serial) => {
    const out = await dynamodb.send(new GetItemCommand({ TableName: env.DEVICE_REGISTRY_TABLE, Key: { cert_serial: { S: serial } } }));
    if (!out.Item) return null;
    return Object.fromEntries(Object.entries(out.Item).map(([k, v]) => [k, v.S ?? v.N]));
  },
};

export const handler = async (event) => {
  try {
    const result = await authorize({
      clientCert: event.requestContext?.identity?.clientCert,
      authorization: event.headers?.Authorization ?? event.headers?.authorization,
      tenantId: env.TENANT_ID,
      deps,
    });
    return { principalId: result.principalId, policyDocument: policy("Allow", event.methodArn), context: result.context };
  } catch (error) {
    // Deny with a reason the gateway response maps to DEVICE_REVOKED / USER_UNAUTHORIZED.
    console.log(JSON.stringify({ type: "symphony.ingress.denied", reason: error.code ?? "INTERNAL", ...(error.detail ?? {}), ...(error.code ? {} : { error: error.name }) }));
    if (error.code === "USER_UNAUTHORIZED") throw new Error("Unauthorized");
    return { principalId: "denied", policyDocument: policy("Deny", event.methodArn), context: { reason: error.code ?? "DEVICE_REVOKED" } };
  }
};

function policy(effect, resource) {
  return { Version: "2012-10-17", Statement: [{ Action: "execute-api:Invoke", Effect: effect, Resource: resource }] };
}
