"""Chat control tower, stage 1 (turns 26~43).

A chat in edit mode may place work cards on its own Flow and start the Flow's run, so card AIs do the work
(instruction -> work -> review) and every card result is committed and merged by the card path that already exists.
The chat keeps its direct source edits (turn 32: both paths stay); this package only adds the card path.

- ``placement``  pure: where the new cards go (after which card, before which unstarted confirm card)
- ``service``    the placement as one Graph draft save (``graph_service.save_draft``) and the run start
                 (draft confirmed, then ``run_service.start_run``)
- ``authz``      a live chat token of this Flow's group in edit mode, with the Flow write permission
- ``routes``     CLI transport: ``POST /api/v2/chat/flows/{flow_id}/cards`` and ``.../runs``
- ``tools``      API chat tools ``place_flow_cards`` / ``start_flow_run`` and the CLI mention section
- ``card_roles`` the earlier change: view / change the instruction, execution and review AI of a work card this
                 chat placed (``GET`` / ``PATCH .../flows/{flow_id}/nodes/{node_key}/roles``, tools
                 ``get_card_roles`` / ``set_card_roles``); ``card_role_store`` keeps the placement records and the
                 change audit (migration 190)
- ``placement_roles`` the earlier change: the AI roles a chat chooses for the cards it places (``roles`` per card,
                 written in the draft save's guard), the AI list before a placement (``GET .../ai-options``, tool
                 ``get_ai_options``) and a named AI group's preset (``from_group`` of the card role change)
- ``run_settings`` the earlier change (CH seq 15-17): the review count, restarts and time limit of a card the
                 chat placed (``settings`` of a placement and of the card role change; audit ``chat_card_setting_audit``,
                 migration 191) — nothing else of the card's settings

The instruction of a placed card is written by the chat with the existing card document edit
(``card_document_edit``: slot ``instruction``); a written instruction skips the instruction AI at run time
(``node_dispatch._instruction_job``). Waking the chat when the run stops is stage 2 and not here.
"""
