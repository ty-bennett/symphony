// Lambda entrypoint for /v1/admin/users (API Gateway REST, AWS_PROXY).
import {
  AdminAddUserToGroupCommand,
  AdminCreateUserCommand,
  AdminDisableUserCommand,
  AdminUserGlobalSignOutCommand,
  CognitoIdentityProviderClient,
  ListUsersCommand,
  ListUsersInGroupCommand,
} from "@aws-sdk/client-cognito-identity-provider"
import { handleAdmin } from "./admin.mjs"

const env = process.env
const cognito = new CognitoIdentityProviderClient({})
const pool = env.USER_POOL_ID
const attr = (user, name) => (user.Attributes ?? user.UserAttributes ?? []).find((a) => a.Name === name)?.Value

async function all(command, key) {
  const out = []
  let token
  do {
    const page = await cognito.send(new command({ UserPoolId: pool, ...(key ?? {}), ...(token ? { NextToken: token } : {}) }))
    out.push(...(page.Users ?? []))
    token = page.NextToken ?? page.PaginationToken
  } while (token)
  return out
}

// Cognito admin calls address users by username; the API addresses them by sub.
async function usernameFor(sub) {
  const page = await cognito.send(new ListUsersCommand({ UserPoolId: pool, Filter: `sub = "${sub.replace(/"/g, "")}"`, Limit: 1 }))
  const user = page.Users?.[0]
  if (!user) throw Object.assign(new Error("not found"), { name: "UserNotFoundException" })
  return user.Username
}

const deps = {
  log: (line) => console.log(JSON.stringify({ type: "symphony.admin.audit", ...line })),
  listUsers: async () => {
    const [users, owners] = await Promise.all([all(ListUsersCommand), all(ListUsersInGroupCommand, { GroupName: "owner" })])
    const ownerSubs = new Set(owners.map((u) => attr(u, "sub")))
    return users.map((u) => ({
      sub: attr(u, "sub"),
      email: attr(u, "email") ?? "",
      name: attr(u, "name") ?? "",
      enabled: u.Enabled !== false,
      status: u.UserStatus,
      groups: ownerSubs.has(attr(u, "sub")) ? ["owner"] : [],
    }))
  },
  createUser: async ({ email, name }) => {
    const out = await cognito.send(
      new AdminCreateUserCommand({
        UserPoolId: pool,
        Username: email,
        DesiredDeliveryMediums: ["EMAIL"],
        UserAttributes: [
          { Name: "email", Value: email },
          { Name: "email_verified", Value: "true" },
          ...(name ? [{ Name: "name", Value: name }] : []),
        ],
      }),
    )
    return { sub: attr(out.User, "sub") }
  },
  addToGroup: async (sub, group) =>
    cognito.send(new AdminAddUserToGroupCommand({ UserPoolId: pool, Username: await usernameFor(sub), GroupName: group })),
  disableUser: async (sub) => cognito.send(new AdminDisableUserCommand({ UserPoolId: pool, Username: await usernameFor(sub) })),
  signOut: async (sub) => cognito.send(new AdminUserGlobalSignOutCommand({ UserPoolId: pool, Username: await usernameFor(sub) })),
}

export const handler = async (event) => {
  const auth = event.requestContext?.authorizer ?? {}
  const result = await handleAdmin({
    method: event.httpMethod,
    path: event.path,
    body: event.body,
    auth: { tenantId: auth.tenant_id, userSub: auth.user_sub, groups: auth.groups },
    deps,
    config: { tenantId: env.TENANT_ID },
  })
  return { statusCode: result.status, headers: { "Content-Type": "application/json" }, body: result.body }
}
