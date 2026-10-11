// Device + user authorization for the API Gateway REQUEST authorizer.
export class AuthError extends Error {
  constructor(code, detail = {}) {
    super(code);
    this.code = code;
    this.detail = detail;
  }
}

/** deps: { getDevice(serial) -> item|null, verifyToken(token) -> claims, now() }. */
export async function authorize({ clientCert, authorization, tenantId, deps }) {
  const serial = normalizeSerial(clientCert?.serialNumber);
  if (!serial) throw new AuthError("DEVICE_REVOKED", { has_cert: Boolean(clientCert) });
  const device = await deps.getDevice(serial);
  if (!device || device.status !== "active" || device.tenant_id !== tenantId || Date.parse(device.not_after) <= deps.now()) {
    // Serials and status are not secret; they tell an operator which device was refused and why.
    throw new AuthError("DEVICE_REVOKED", { serial, has_cert: true, registered: Boolean(device), ...(device ? { status: device.status } : {}) });
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

/**
 * Registry key: lowercase hex without leading zeros. API Gateway's mTLS context reports the
 * serial as a decimal integer; colon-separated hex is accepted too.
 */
export function normalizeSerial(serial) {
  if (!serial) return null;
  const hex = /^[0-9]+$/.test(serial) ? BigInt(serial).toString(16) : serial.replaceAll(":", "").toLowerCase();
  return hex.replace(/^0+(?=.)/, "");
}
