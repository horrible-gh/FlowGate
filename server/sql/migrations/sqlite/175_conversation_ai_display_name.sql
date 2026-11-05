-- flowgate.v02.0135 CH0006: AI turns/participants of a personal AI were stored with the bare
-- provider id as display_name (the name lookup skipped owner_scope='user' rows). Restore the
-- provider name where display_name is exactly that turn's own provider id.
UPDATE conversation_turns
SET display_name = (SELECT p.name FROM ai_providers p WHERE p.provider_id = conversation_turns.display_name)
WHERE speaker = 'ai'
  AND participant_key = 'provider:' || conversation_turns.display_name
  AND EXISTS (SELECT 1 FROM ai_providers p
              WHERE p.provider_id = conversation_turns.display_name AND p.name IS NOT NULL AND p.name <> '');
UPDATE conversation_participants
SET display_name = (SELECT p.name FROM ai_providers p WHERE p.provider_id = conversation_participants.display_name)
WHERE kind = 'ai'
  AND participant_key = 'provider:' || conversation_participants.display_name
  AND EXISTS (SELECT 1 FROM ai_providers p
              WHERE p.provider_id = conversation_participants.display_name AND p.name IS NOT NULL AND p.name <> '');
