# Connect a customer's SSO (Microsoft Entra ID, Google Workspace, Okta)

With SSO, people sign in to Symphony (and OpenCode, through the Jetson) with their work account; their company's MFA and offboarding apply. About 30 minutes: 15 for the customer's IT admin, 15 for the operator.

Values for the demo tenant (from `terraform -chdir=cloud/terraform/tenant-workload output`):

- Redirect URI to register at the identity provider: `https://symphony-demo-98c55150.auth.us-east-1.amazoncognito.com/oauth2/idpresponse` (`hosted_login_domain` + `/oauth2/idpresponse`)
- SAML only: entity ID `urn:amazon:cognito:sp:us-east-1_VaWrIhDfF` (`urn:amazon:cognito:sp:` + `user_pool_id`)

## 1. Customer IT admin

### Microsoft Entra ID (OIDC)

1. Entra admin center → **App registrations** → **New registration**: name `Symphony`, single tenant, redirect URI type **Web** = the redirect URI above.
2. **Certificates & secrets** → **New client secret**; copy its value.
3. Note the **Application (client) ID** and the **Directory (tenant) ID**.
4. **Enterprise applications** → Symphony → **Properties** → *Assignment required* = **Yes**, then **Users and groups** → assign who may use Symphony.
5. Send the operator the client ID and tenant ID, and the client secret through a password manager share (never email or chat).

### Google Workspace (OIDC)

1. Google Cloud console → **APIs & Services** → **Credentials** → **Create OAuth client ID** → *Web application*; authorized redirect URI = the redirect URI above. Set the consent screen to **Internal**, so only the Workspace's users can sign in.
2. Send the client ID and secret (password manager share). The issuer is `https://accounts.google.com`.

### Okta or any SAML 2.0 provider

Create a SAML app with ACS URL = the redirect URI above with `saml2/idpresponse` instead of `oauth2/idpresponse`, audience = the entity ID above, NameID = email, and an `email` attribute. Assign the users, then send the metadata URL.

## 2. Operator

Add the provider to the tenant's gitignored `cloud/terraform/tenant-workload/terraform.tfvars`. The key (`Microsoft`) is the button label on the login page:

```hcl
sso_identity_providers = {
  Microsoft = {
    type = "OIDC"
    provider_details = {
      client_id                 = "<application client id>"
      oidc_issuer               = "https://login.microsoftonline.com/<directory tenant id>/v2.0"
      authorize_scopes          = "openid email profile"
      attributes_request_method = "GET"
    }
    attribute_mapping = { email = "email", username = "sub" }
  }
}

sso_client_secrets = { Microsoft = "<client secret>" }
```

SAML instead: `type = "SAML"`, `provider_details = { MetadataURL = "<metadata url>" }`, `attribute_mapping = { email = "email" }`, and no client secret.

Then plan and apply (expect one identity provider created and the Jetson client updated in place, nothing destroyed):

```bash
terraform -chdir=cloud/terraform/tenant-workload plan -out=sso.tfplan
terraform -chdir=cloud/terraform/tenant-workload apply sso.tfplan
```

## 3. Check it

1. Open `<hosted_login_domain>/login?response_type=code&client_id=<jetson_app_client_id>&redirect_uri=http%3A%2F%2Flocalhost%3A53682%2Fcallback&scope=openid` and confirm a **Microsoft** (or Google, Okta) button appears.
2. Sign in as an assigned user; the browser lands on `localhost:53682/callback?code=…` (the page itself will not load unless the sign-in tool is running).
3. An unassigned user is refused by the identity provider.

## Notes

- Federated users are created in the pool on first sign-in; who may sign in is controlled by the assignment in step 1. Their MFA is the company's; the pool's TOTP requirement applies only to email/password accounts.
- Offboarding: disabling the person at the identity provider stops new sign-ins. Their existing session ends when the access token expires (at most 60 minutes), or immediately if the operator also revokes their tokens in Cognito.
- To keep only SSO, stop creating email/password accounts and remove `COGNITO` from the client's providers (a later change).
- The client secret is stored in Terraform state (encrypted with KMS in the state bucket) and in the gitignored tfvars; rotate it at the identity provider and re-apply.
