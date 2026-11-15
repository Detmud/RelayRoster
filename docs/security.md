# Security

- **Sign-in via OIDC only** (authorization code flow with PKCE, confidential client). The ID token is verified
  locally against the provider's JWKS (asymmetric algorithms only; `iss`, `aud`, `azp`, `exp`, `iat`, `nonce`). After
  sign-in a signed session cookie is valid for 30 days (HMAC-SHA256, HttpOnly, SameSite=Lax, Secure over HTTPS, also
  behind a proxy via `X-Forwarded-Proto`). The provider is only asked at sign-in, so a blocked user or removed group
  takes effect when the session expires. Without `OIDC_ISSUER` the UI is open: the log says so at start-up and every
  page shows a warning.
- Every response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`
  and a `Content-Security-Policy` with `frame-ancestors 'none'`, so the UI cannot be embedded in foreign pages.
- State-changing API calls require the header `X-Requested-With: relayroster`, which a foreign website cannot set
  without CORS permission.
- Write functions are locked behind their switches; without the switch the endpoints answer 403.
- Container: read-only root file system, `cap_drop: ALL`, `no-new-privileges`, own user (UID 10003).
- "Remove from RelayRoster" only deletes RelayRoster's own records of a device, never anything on the device.

## Setting up OIDC

Any OpenID Connect provider works (e.g. Pocket ID, Authentik, Keycloak, Authelia). Create a confidential client with
PKCE and the callback URL `https://<your-host>/auth/callback`, then set:

```
OIDC_ISSUER=https://id.example.com          # exactly the issuer from the provider's discovery document
OIDC_CLIENT_ID=<client id>
OIDC_CLIENT_SECRET=<client secret>
OIDC_REDIRECT_URI=https://relayroster.example.com/auth/callback
OIDC_PROVIDER_NAME=Pocket ID                # optional, label of the sign-in button
```

If your provider uses a certificate from a private CA, put the root certificate into `OIDC_CA_CERT`. Line breaks,
`\n` or spaces between the lines all work. If the value is unusable, RelayRoster still starts and shows the error on
the sign-in page. The container must reach the provider under the same address as the browser. The user name is
`preferred_username`, falling back to `email` or `sub`.

There is no password fallback. If your provider is down, clear `OIDC_ISSUER` and restart; the UI is then reachable
without sign-in, so only do this in a trusted network.
