# Google sign-in

A "Continue with Google" button on the login and register pages. It
appears only when the Firebase settings are in `keys.env`.

## Who does what

```
browser                         Google / Firebase                Stellar server
-------                         -----------------                --------------
click the button  ───────────▶  account picker popup
                  ◀───────────  ID token (a JWT Google signed)
POST /auth/google {id_token} ─────────────────────────────────▶  check signature,
                                                                 audience, issuer
                  ◀─────────────────────────────────────────────  Stellar session cookie
```

The **ID token** is a JWT: three base64 parts, header.claims.signature.
The claims say who this is (`email`, `email_verified`, `name`), how they
signed in (`firebase.sign_in_provider`), and for which project (`aud`,
`iss`). Google signs it with a private key and publishes the matching
public keys. `google.oauth2.id_token.verify_firebase_token` downloads those
and checks the signature, the expiry and the audience. Stellar adds a check
on the issuer.

This is why the server needs **no Google secret**. It only needs to know
which project to expect tokens for. Everything the browser needs (apiKey,
authDomain, projectId, appId) is public by design. Google's docs say
outright that the web apiKey is not a secret.

After the check, the token is discarded and Stellar's own session cookie
takes over, exactly as after a password login. The page also signs out of
Firebase, so logging out of Stellar leaves nothing behind.

## The decisions

- **Google only, and only with a verified email.** Firebase can sign people
  in in other ways too. Accounts are matched on email, so the route
  accepts only `sign_in_provider == "google.com"` with `email_verified`.
- **Matched by Google id first, then by email.** `google_sub` stores the
  Google account's id, which never changes, whereas the email can.
- **Linking removes the password.** If `you@gmail.com` already has a
  password account, the first Google sign-in links it. The register form
  never checks email ownership, so anyone could have registered your
  address first. Google has just proved the address is yours. So the
  password goes, and with it anyone else's key to the account.
- **A linked email cannot be claimed by a different Google account** (409).
- **JSON only.** A cross-site form cannot send `application/json` without
  a CORS preflight, so another site cannot quietly sign you in to an
  account of its choosing.
- **The same approval rules as passwords.** Nobody is approved by signing up.
  An address listed in ADMIN_EMAILS becomes admin when Google proves it, and only then.
  Everyone else waits for approval.

## Compared with N1kky's

Same idea: Firebase in the browser, `verify_firebase_token` on the server.
The differences:
- the project is configuration, not a constant in the code
- the provider and `email_verified` are checked
- the Google id is stored, not only the email
- a password found on the account is removed when Google links it
- `verify_env.py` checks the Firebase project, the Google provider and the
  authorized domains before you ever open the page
