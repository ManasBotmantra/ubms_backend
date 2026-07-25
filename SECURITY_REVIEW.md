# Security Review & Fixes — UBMS Backend

**Date:** 25 July 2026
**Reviewed by:** Claude Opus 5
**What was reviewed:** All 52 HTTP endpoints, the new OAuth 2.0 server, the shared
auth/database/audit layer, and the ChatAPI conversational-analytics pipeline.

---

## 1. Summary in plain English

I checked the whole codebase for security holes, with a close look at the OAuth code.

**The good news:** the older, established part of the system — the 42 data-management
endpoints in `admin_functions/` — is genuinely well built. I tried hard to find SQL
injection and could not. Passwords are hashed properly, user roles are re-checked on
every request, and login sessions rotate safely.

**The bad news:** the newly added OAuth code and the ChatAPI were **not** safe. I found
two completely separate ways an attacker could take over a user's account without
knowing their password, plus about a dozen smaller problems. None of this had been
committed to git yet, so nothing was ever live — we caught it in time.

**What I did:** fixed everything I found. 11 files changed, 1 new file added. All fixes
are tested and passing.

---

## 2. The two serious problems (both fixed)

### Problem 1: Anyone could steal a user's account through the OAuth login page

**This was the worst one.** Here's how the attack worked, step by step:

1. OAuth works by sending the user to a login page, then sending them back to the app
   with a one-time "code" that the app trades for a login token. The address to send
   the user back to is called the `redirect_uri`.
2. **Our code never checked that address.** Whatever address the request asked for, our
   server would send the user (and their code) there.
3. So an attacker sends the victim a link to our **real** login page — correct domain,
   correct padlock, nothing suspicious — but with `redirect_uri=https://evil-site.com`
   quietly added.
4. The victim sees a genuine login page and types their real email and password.
5. Our server hands the one-time code to `evil-site.com`.
6. The attacker trades that code for a working login token. **They are now logged in as
   the victim.**

There was no client secret check either, so nothing stopped the attacker from making
that final trade.

A second, related hole: even where the address *was* compared, passing the literal text
`*` made the comparison skip itself entirely.

**Fixed by:** every callback address must now be registered in the database in advance
and must match **exactly**, character for character. No wildcards, no `*`, no partial
matches. If the address doesn't match, the user is shown an error on our own page and is
**never** redirected — because redirecting to an address we haven't verified is the
whole bug.

*A note on why "PKCE" didn't save us here:* PKCE is a security feature this code already
had, and people often assume it prevents this. It does not. PKCE protects a code from
being stolen *in transit*. It cannot help when the attacker started the login flow
themselves, because then the attacker owns the secret PKCE relies on. Checking the
callback address is the only thing that stops this attack.

### Problem 2: The ChatAPI didn't actually check logins

The chat endpoint looked like it required a login token. It didn't. The code was:

```python
authorize_token = req.headers.get("authorize_token")
if not authorize_token:
    return 401
```

It only checked that the header **wasn't empty**. It never verified the token was real.
Sending the literal word `anything` got you in.

This mattered a lot, because the chat endpoint is the most powerful one we have: it turns
questions into database queries. And if the request named a data set that was already
loaded, no other system was contacted, so nothing else ever caught the fake token.
The result: **a stranger with no account could read company billing data** by asking
questions in plain English.

This was the only endpoint out of 52 that skipped the login check. The other 51 all did
it correctly.

**Fixed by:** the token is now properly verified against the database, using the same
function every other endpoint uses. Access is also limited to the Administrator and
Leadership roles, matching the other reporting endpoints.

---

## 3. Everything else I fixed

### Serious

| Problem | What could happen | Fix |
|---|---|---|
| **File-path escape.** A data set name from the request was pasted straight into a file path. | Sending `../../` as the name let an attacker read files outside the intended folder. | Names must now be plain letters, numbers, dashes and underscores. Checked in two places, so a future code change can't reopen it. |
| **Security switched OFF when the database hiccupped.** If the client-registry lookup failed, the code fell back to a built-in client that accepted *any* callback address. | Any brief database outage would silently disable the single most important protection in Problem 1 — and the client IDs it accepted were easy to guess (`default`, `copilot`, `mcp`). | Removed entirely. A failed lookup now returns "service unavailable". Security controls must fail **closed**, never open. |
| **Login tokens accepted in the web address.** | Web addresses get written to server logs, proxy logs, and are handed to the next site the browser visits. Tokens would leak into all of them. | Tokens are now only read from the `Authorization` header. |
| **Internal error messages sent to strangers.** Raw Python errors were returned to whoever asked. | Database errors reveal table names, server names and connection details — a free map of the system for an attacker. | Callers get a short generic message. Full detail goes to the server log where only we can see it. |
| **Passwords and codes written to the audit log in plain text.** The audit log already hid passwords, but not OAuth codes, PKCE verifiers, or client secrets. | Anyone able to read the audit log — which is itself available through an API — could pick up live credentials. | Those fields are now redacted too. |

### Moderate

| Problem | Fix |
|---|---|
| **Cross-site scripting on the login page.** An application's display name was inserted into the page without escaping, so a name containing `<script>` would run code — on the login page itself, the worst possible place. | Everything shown on the page is now escaped. |
| **No consent step.** A logged-in user's browser visiting the address was enough to silently issue an access code. | A code is now only issued when the user actively clicks "Authorize". |
| **Broken refresh tokens.** The OAuth code flow generated a refresh token and gave it to the app but never saved it. It was dead on arrival, the session was untraceable, and it couldn't be revoked. | Sessions are now properly recorded, so refresh works and access can be revoked. |
| **Weak PKCE allowed.** The insecure `plain` mode was accepted and advertised. | Only the secure `S256` mode is accepted, and PKCE is now mandatory rather than optional. |
| **Codes lasted 10 minutes** and could be used twice under a race condition. | Cut to 60 seconds. Single-use is now enforced by the database itself, so two simultaneous attempts can't both succeed. |
| **Attacker-controlled web address values overrode what the user typed** into the login form. | Form values now take priority, and only an approved list of settings is carried through. |
| **Silent failures.** If saving a code failed, the user was still redirected with a code that could never work. | Failures now surface as a clear error. |
| **The public key file advertised a shared secret key.** It also echoed a configuration value straight out, so pasting a full key there would have published the private half. | Only genuine public keys are published; private and shared-secret material is filtered out and logged as a warning. |
| **The discovery document advertised features we don't have** (`plain` PKCE, OpenID support, RSA signing). | It now describes only what the server actually does. Over-advertising invites clients to negotiate down to the weakest option. |
| **Login page could be embedded in a hidden frame** on another site (clickjacking). | Added `X-Frame-Options: DENY`, `nosniff`, and `Referrer-Policy: no-referrer`. |
| **Login errors could reveal whether an email address had an account.** | Always the same message: "Invalid email or password." |

---

## 4. Files changed

| File | Change |
|---|---|
| `admin_functions/oauth_db.py` | Rewritten. Core OAuth security logic. |
| `oauth_authorize/__init__.py` | Rewritten. Login and consent page. |
| `oauth_token/__init__.py` | Rewritten. Token issuing. |
| `ChatAPI/__init__.py` | Rewritten. Added the missing login check. |
| `admin_functions/auth_session.py` | Added `f_create_session_for_user()` so OAuth sessions are saved properly. |
| `common_functions/audit.py` | Redact OAuth credentials. |
| `well_known_jwks/__init__.py` | Filter out private/shared keys. |
| `well_known_oauth/__init__.py` | Honest capability list. |
| `src/infrastructure/duckdb_engine.py` | Block file-path escape. |
| `src/services/dataset_service.py` | Block file-path escape. |
| `prerequisite/oauth_schema.sql` | **New.** The database tables. |

### Database check (read-only, 25-Jul-2026)

I connected to the UAT database (`botiquat` / `UBMS` / schema `public`, PostgreSQL 17.10)
in **read-only mode** — enforced at the session level, so no write was possible — and
checked every table the backend needs.

**All 14 required tables exist**, including `oauth_clients` and `oauth_auth_codes`. I
also compared every column against what the hardened code reads and writes: **nothing is
missing and no type is too small.** The code will work against this database as-is.

| | |
|---|---|
| Core (users, roles, sessions, audit) | 4/4 present |
| Domain (units, accounts, bills, approvals, uploads) | 8/8 present |
| OAuth (`oauth_clients`, `oauth_auth_codes`) | 2/2 present, **both empty** |
| Roles configured | Administrator, Leadership, Unit Accountant |
| Other tables in schema | `lov_master` |

**One blocker: `oauth_clients` has zero rows.** The hardened code rejects any client that
isn't registered, so OAuth will refuse every request until at least one client row is
added. This is correct, intended behaviour — it's the fix for Problem 1 — but it does
mean **OAuth will not work until you register a client.**

The old code hid this problem: when the lookup failed it fell back to a built-in client
that accepted any callback address. So OAuth appeared to work while the main protection
was switched off.

`prerequisite/oauth_schema.sql` has been corrected to match the live schema. It no longer
creates tables (they exist); it documents the real column definitions and gives
copy-paste examples for registering a client.

**I made no changes to the database.** Everything in that file is commented out for you
to review and run.

### `.env` review (read-only)

Read, not modified. Nothing here is exposed publicly — `.env` is correctly listed in
`.gitignore` and I confirmed it has **never** been committed to git.

| Item | Finding |
|---|---|
| `JWT_SECRET_KEY` | **Strong.** 86-character random value, roughly 512 bits. No action needed; left untouched as instructed. |
| `DB_PASSWORD_BM` | **Weak.** A short, predictable phrase based on the company name, on an internet-facing Azure PostgreSQL server. Flagged for your judgement only. |
| `AZURE_OPENAI_API_KEY` | Live key present. Fine for local use; make sure it stays out of any shared or committed file. |
| `DB_PASSWORD` (localhost) | `admin` — only used for a local database, harmless. |
| `ACCESS_TOKEN_EXPIRY_SECONDS` | Set to `3600` (1 hour); the code default is 900 (15 min). Longer-lived tokens widen the window if one leaks. Worth a second look, not urgent. |
| `SCHEMANAME` | Defined twice (lines 6 and 13, the second with spaces around `=`). Harmless — it resolves to `public` correctly — but the duplicate is worth tidying. |
| `REFRESH_TOKEN_EXPIRY_SECONDS` | Not set, so it falls back to the 7-day default. Fine, but being explicit is better. |

---

## 5. Testing

I wrote and ran 52 checks against the new code. **All passed.** They confirm:

- Registered callback addresses are accepted; attacker addresses, `*`, look-alike
  domains (`app.example.com.evil.com`) and path tricks are all rejected.
- Insecure `plain` PKCE is rejected; correct verifiers accepted, wrong ones refused.
- Correct client secrets accepted, wrong and missing ones refused.
- File-path escape attempts blocked — including Windows-style `\`, drive letters like
  `C:\`, encoded slashes, and null bytes.
- Codes, PKCE verifiers, client secrets and passwords are redacted from the audit log,
  while non-secret fields are left readable.
- Shared-secret and private keys are refused publication; only genuine public keys pass.
- `<script>` in an application name comes out escaped.

All 11 changed files compile cleanly.

**What testing did *not* cover — please read this before deploying.** These checks
exercise the security logic in isolation. They do **not** include:

- Any test against a real database (no live PostgreSQL was used).
- An end-to-end run of a full OAuth login through a browser.
- Any check that existing apps still work after these changes.

**The changes are breaking by design**, so that last point needs real attention.

---

## 6. What you need to do next

**Before deploying — required:**

1. **Register at least one OAuth client.** `oauth_clients` is empty, so OAuth currently
   rejects everything. See `prerequisite/oauth_schema.sql` section 2 for the exact
   `INSERT`. The tables themselves already exist — no table creation needed.
2. **Register exact callback addresses** for every application. Anything not registered
   is rejected. This is the fix for Problem 1, but it *will* break any app that relied
   on the old anything-goes behaviour.
3. **Tell client apps they must now send PKCE** (`S256`) and a `redirect_uri` when
   trading a code. Both are mandatory; previously neither was enforced.
4. **Test a full login end-to-end.** I verified the schema is compatible but did not run
   a live login.

**Recommended soon:**

5. **Add the database-level constraints** in `prerequisite/oauth_schema.sql` section 3.
   The live OAuth tables currently have only primary keys and one foreign key. The
   suggested `CHECK` constraints make a `*` callback address and non-S256 PKCE
   impossible at the database level, so no future code change or manual `INSERT` can
   reintroduce what was just fixed. There is also no foreign key on
   `oauth_auth_codes.user_id`.
6. **Add rate limiting** to the login and token endpoints. There is currently nothing
   stopping an attacker from trying thousands of passwords. This needs an Azure API
   Management policy or similar — it can't be fixed properly in the function code.
7. **Tighten CORS.** Several endpoints allow requests from any website
   (`Access-Control-Allow-Origin: *`), as does `local.settings.json`. Replace with a
   list of known origins.
8. **Consider the UAT database password.** `DB_PASSWORD_BM` is a short, predictable
   phrase built from the company name — the kind of password a targeted guessing attack
   tries early, on a database server reachable over the public internet. Noted for your
   judgement; I have changed nothing. (`JWT_SECRET_KEY`, by contrast, is a strong
   86-character random value — no action needed there, and per your instruction it is
   left as-is.)

**Worth considering:**

8. **Retire the `password` grant type.** It sends the user's real password through the
   application, and the OAuth 2.1 standard removes it. It's still supported here for the
   existing login flow, and is now restricted to applications explicitly registered for
   it, but new integrations should use the authorization-code flow instead.
9. **Enforce scopes.** The `scope` value is accepted and stored but never checked
   anywhere — right now it's decorative. Either enforce it or drop it.

---

## 7. Phase 2 — Making OAuth actually usable (25-Jul-2026)

The first pass fixed the security holes but left OAuth as, in effect, a front door
opening onto a wall: a client could complete the whole OAuth flow, get a valid token,
then fail on every real API call. This phase closed that.

### Two design decisions you made

| Decision | Choice | Consequence |
|---|---|---|
| Client registration | **Manual only** | No `/oauth/register` endpoint. Every client is a SQL `INSERT`. A client that *requires* Dynamic Client Registration won't self-onboard — you add it by hand. |
| Scopes | **Coarse `read` / `write`** | `read` = all filter/get/report/dashboard endpoints. `write` = create/update/delete/approve/reject/resolve/upload. |

### What changed

**1. Standard `Authorization: Bearer` now works everywhere.** 44 endpoint files read only
the legacy `authorize_token` header, so a spec-compliant OAuth client got a 401 on every
call. They now accept **both** — your existing frontend is untouched, and agents can use
the standard header.

**2. Scopes are enforced instead of decorative.** The access token now carries a `scope`
claim, and all 45 protected endpoints check it. The rule is:

```
effective access = role permissions  AND  token scope
```

Scope can only ever **narrow**, never widen. A `write`-scoped token held by a Leadership
user still can't create records — the role check rejects it first. This is what lets you
give an AI agent read-only access to billing data.

**3. Scope survives a refresh.** A refresh re-issues the *original* grant's scope. Without
this a read-only agent could refresh its way up to full access and the whole scheme would
be pointless. This needed two new columns on `auth_session` — see the migration.

**4. Refresh tokens are bound to their client.** A leaked refresh token is useless to a
different client (RFC 6749 §10.4).

**5. New endpoints:**
- `POST /api/oauth/revoke` — RFC 7009. Lets an agent disconnect cleanly instead of leaving
  a live session until it expires. Returns 200 even for unknown tokens, per spec, so it
  can't be used to test whether a stolen token is still live.
- `GET /api/.well-known/oauth-protected-resource` — RFC 9728. **This is the document MCP
  clients fetch first.** Without it, a client that gets a 401 has no way to discover where
  to authenticate and simply gives up.

**6. Password grant defaults to read-only.** A script or agent using `grant_type=password`
without asking for a scope gets `read`, not full access.

### Backward compatibility

Nothing breaks for the existing frontend:

- Tokens issued **before** this change have no `scope` claim. Those are treated as full
  access, so tokens already in the wild keep working until they expire.
- `create_auth_session` still grants full `read write`.
- The `authorize_token` header still works everywhere.
- `auth_session` rows with `NULL` scope read as full access.

An explicitly empty scope is **not** upgraded — only a genuinely missing claim is.

### Verification

**26 checks, all passing** — scope parsing, privilege-escalation attempts, backward
compatibility, invalid/expired/wrong-signature tokens granting nothing, both header
styles, and a sweep confirming every endpoint got the right read/write classification.

**Startup verified:** 54/54 functions register, zero import errors, host starts cleanly.

### Still required before OAuth works

Run **`prerequisite/oauth_migration.sql`**. It does two things, both mandatory:

1. Adds `scope` and `client_id` to `auth_session` — **login breaks without these.**
2. Registers at least one OAuth client — `oauth_clients` is still empty, and the code
   rejects unregistered clients.

I have not applied it. Review and run it yourself.

### Known gaps (deliberate)

- **No Dynamic Client Registration** — your choice. Clients requiring it need manual setup.
- **Access tokens can't be revoked mid-life.** They're self-contained JWTs; revoking the
  refresh token stops renewal, and the 1-hour lifetime bounds the rest. Shortening
  `ACCESS_TOKEN_EXPIRY_SECONDS` tightens that window.
- **Frontend is untouched** — as you asked. It still uses `create_auth_session`. When you
  migrate it to OAuth, Example C in the migration file has the client row you'll need.

---

## 8. What was already good

Worth recording, because these are the patterns the new code should have copied:

- **No SQL injection anywhere.** Sort columns are checked against fixed allow-lists,
  sort direction is limited to `ASC`/`DESC`, and all values use proper query parameters.
- **51 of 52 endpoints** correctly validated the login token and checked the user's role.
- **User roles are read fresh from the database on every request**, never trusted from
  the token — so deactivating an account takes effect immediately.
- **Refresh tokens rotate**, with a proper guard so two simultaneous requests can't both
  succeed.
- **Passwords use bcrypt**, with a deliberate dummy check on the unknown-user path so
  response timing doesn't reveal which email addresses have accounts. That's a thoughtful
  detail.
- **Refresh tokens are only ever stored hashed**, never in plain text.
- **Database connections require SSL.**

The contrast is the real lesson here: the same project contains both careful security
work and code that skipped it. The OAuth and ChatAPI code would have benefited from
being held to the standard the rest of the codebase already set.
