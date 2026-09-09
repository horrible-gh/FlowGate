-- 089_tokens_revoke_claim.sql
-- Add a per-call claim marker column so token_service.revoke() can tell, across
-- process boundaries, whether *this* caller was the one who caused the
-- revoked_at transition (0447 T0007 review rev1). db/tokens.py's guarded UPDATE
-- (`WHERE revoked_at IS NULL`) stamps revoke_claim atomically alongside
-- revoked_at in the same statement; a single UPDATE is atomic against
-- concurrent writers regardless of which process issues it, so only one
-- caller's claim value can ever land on a given token_id. Nullable, no
-- backfill needed (existing rows keep NULL, which cannot match any caller's
-- freshly generated claim).

ALTER TABLE tokens ADD COLUMN revoke_claim TEXT;
