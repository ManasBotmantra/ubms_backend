-- ============================================================================
-- OAuth 2.0 — REQUIRED migration
-- ============================================================================
-- Run this ONCE before starting the backend after the OAuth work.
--
--   psql "host=... dbname=UBMS user=... sslmode=require" -f prerequisite/oauth_migration.sql
--
-- Verified against the UAT database (botiquat / UBMS / public) on 25-Jul-2026.
-- Nothing here was applied for you — review and run it yourself.
--
-- Two parts:
--   1. Two new columns on auth_session      -> REQUIRED, login breaks without them
--   2. Register at least one OAuth client   -> REQUIRED for OAuth to work at all
-- ============================================================================


-- ============================================================================
-- PART 1 — auth_session gains scope + client_id   (REQUIRED)
-- ============================================================================
-- Why these are needed:
--
--  scope      A refresh must re-issue the SAME access level it was granted. Without
--             somewhere to record it, a read-only agent could refresh its way up to
--             full write access and scope enforcement would be meaningless.
--
--  client_id  Binds a refresh token to the client it was issued to, so a leaked
--             token is useless to anyone else (RFC 6749 s10.4). It is also what
--             lets /oauth/revoke stop one client revoking another's sessions.
--
-- Both are nullable. Existing rows stay NULL, which the code reads as
-- "first-party frontend session, full access" — so sessions created before this
-- migration keep working until they expire naturally.

ALTER TABLE auth_session
    ADD COLUMN IF NOT EXISTS scope     TEXT,
    ADD COLUMN IF NOT EXISTS client_id VARCHAR(64);

-- Revocation and refresh both look sessions up by (refresh_token_hash, client_id).
CREATE INDEX IF NOT EXISTS idx_auth_session_client_id
    ON auth_session (client_id);

-- Optional but recommended: keep client_id honest.
-- Skip this if you ever want to delete a client row while leaving its sessions
-- readable for audit purposes.
--
--   ALTER TABLE auth_session
--       ADD CONSTRAINT auth_session_client_id_fkey
--       FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id) ON DELETE CASCADE;


-- ============================================================================
-- PART 2 — register your clients   (REQUIRED)
-- ============================================================================
-- oauth_clients is currently EMPTY. The hardened code rejects any client_id that
-- is not registered, so /oauth/authorize and /oauth/token refuse everything until
-- at least one row exists. This is intended: it is the fix for the account-takeover
-- vulnerability, where an unregistered redirect_uri was accepted.
--
-- There is no /oauth/register endpoint — registration is manual by choice.
--
-- RULES for redirect_uris:
--   * Compared byte-for-byte. Scheme, host, port and path must all match exactly.
--   * No wildcards. Never put '*' in this column.
--   * List every callback URL the client will actually use.

-- ---- Example A: a public client (no secret) --------------------------------
-- For desktop/CLI/MCP agents that cannot keep a secret. Protected by mandatory
-- PKCE plus exact redirect_uri matching. Note scope is NOT stored per client —
-- it is requested per authorization and consented to by the user.

INSERT INTO oauth_clients
    (client_id, client_secret_hash, client_name, redirect_uris, grant_types, is_active)
VALUES (
    'claude_desktop',
    NULL,
    'Claude Desktop',
    ARRAY['http://localhost:3000/callback'],
    ARRAY['authorization_code', 'refresh_token'],
    TRUE
)
ON CONFLICT (client_id) DO NOTHING;


-- ---- Example B: a confidential client (with a secret) ----------------------
-- For server-side integrations, e.g. a Copilot connector. Generate the secret and
-- its hash first:
--
--   python -c "import secrets,hashlib; s=secrets.token_urlsafe(32); \
--     print('secret:', s); print('hash:  ', hashlib.sha256(s.encode()).hexdigest())"
--
-- Store ONLY the hash below. Give the plaintext secret to the client once — it
-- cannot be recovered afterwards.
--
--   INSERT INTO oauth_clients
--       (client_id, client_secret_hash, client_name, redirect_uris, grant_types, is_active)
--   VALUES (
--       'copilot_connector',
--       '<paste the hash here>',
--       'Microsoft Copilot Connector',
--       ARRAY['https://your-copilot-host/oauth/callback'],
--       ARRAY['authorization_code', 'refresh_token'],
--       TRUE
--   );


-- ---- Example C: your existing frontend (optional) --------------------------
-- Only needed if you migrate the frontend from create_auth_session to OAuth.
-- Until then the frontend keeps using create_auth_session and needs no client row.
--
--   INSERT INTO oauth_clients
--       (client_id, client_secret_hash, client_name, redirect_uris, grant_types, is_active)
--   VALUES (
--       'ubms_frontend', NULL, 'UBMS Web Frontend',
--       ARRAY['https://your-frontend/oauth/callback', 'http://localhost:5173/oauth/callback'],
--       ARRAY['authorization_code', 'refresh_token'], TRUE
--   );


-- ============================================================================
-- PART 3 — recommended hardening (optional)
-- ============================================================================
-- Database-level guarantees so no future code path or manual INSERT can
-- reintroduce what was just fixed.
--
--   ALTER TABLE oauth_clients
--     ADD CONSTRAINT oauth_clients_no_wildcard_redirect
--     CHECK (NOT ('*' = ANY(redirect_uris)));
--
--   ALTER TABLE oauth_clients
--     ADD CONSTRAINT oauth_clients_redirect_uris_not_empty
--     CHECK (array_length(redirect_uris, 1) > 0);
--
--   ALTER TABLE oauth_auth_codes
--     ADD CONSTRAINT oauth_auth_codes_s256_only
--     CHECK (code_challenge_method = 'S256');
--
--   ALTER TABLE oauth_auth_codes
--     ADD CONSTRAINT oauth_auth_codes_user_id_fkey
--     FOREIGN KEY (user_id) REFERENCES user_master(user_id) ON DELETE CASCADE;


-- ============================================================================
-- Verify
-- ============================================================================
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='auth_session' AND column_name IN ('scope','client_id');
--   -- expect 2 rows
--
--   SELECT client_id, client_name, redirect_uris, grant_types FROM oauth_clients;
--   -- expect at least 1 row
--
-- Housekeeping — authorization codes live 60 seconds:
--   DELETE FROM oauth_auth_codes WHERE expires_at < now() - INTERVAL '1 day';
