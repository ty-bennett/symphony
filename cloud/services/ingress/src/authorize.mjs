// Device + user authorization for the API Gateway REQUEST authorizer.
export class AuthError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

/** deps: { getDevice(serial) -> item|null, verifyToken(token) -> claims, now() }. */
export async function authorize({ clientCert, authorization, tenantId, deps }) {
  const serial = normalizeSerial(clientCert?.serialNumber);
  if (!serial) throw new AuthError("DEVICE_REVOKED");
  const device = await deps.getDevice(serial);
  if (!device || device.status !== "active" || device.tenant_id !== tenantId || Date.parse(device.not_after) <= deps.now()) {
    throw new AuthError("DEVICE_REVOKED");
  }
  const token = /^Bearer (\S+)$/.exec(authorization ?? "")?.[1];
  if (!token) throw new AuthError("USER_UNAUTHORIZED");
  let claims;
  try {
    claims = await deps.verifyToken(token);
  } catch {
    throw new AuthError("USER_UNAUTHORIZED");
  }
  return { principalId: claims.sub, context: { user_sub: claims.sub, device_id: device.device_id, tenant_id: tenantId } };
}

/** API Gateway reports serials as colon-separated hex; the registry stores lowercase hex without leading zeros. */
export function normalizeSerial(serial) {
  return serial ? serial.replaceAll(":", "").toLowerCase().replace(/^0+(?=.)/, "") : null;
}
