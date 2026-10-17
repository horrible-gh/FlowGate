-- 147_graph_node_settings_after_review.sql — flowgate.v02.0056 T#1 (card approval flow)
-- The card setting after review (follow / human) is gone: what follows a card's result is always the former
-- follow (approved when the card review passed, or at once with no review), and a result that was not
-- approved waits for a person's card approval. No code reads the stored value any more; its rows go.
BEGIN;

DELETE FROM graph_node_settings WHERE field = 'after_review';

COMMIT;
