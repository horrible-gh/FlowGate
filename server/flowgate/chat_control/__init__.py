"""Chat control tower, stage 1 (flowgate.v02test.0023 0002-CH turns 26~43).

A chat in edit mode may place work cards on its own Flow and start the Flow's run, so card AIs do the work
(instruction -> work -> review) and every card result is committed and merged by the card path that already exists.
The chat keeps its direct source edits (turn 32: both paths stay); this package only adds the card path.

- ``placement``  pure: where the new cards go (after which card, before which unstarted confirm card)
- ``service``    the placement as one Graph draft save (``graph_service.save_draft``) and the run start
                 (draft confirmed, then ``run_service.start_run``)
- ``authz``      a live chat token of this Flow's group in edit mode, with the Flow write permission
- ``routes``     CLI transport: ``POST /api/v2/chat/flows/{flow_id}/cards`` and ``.../runs``
- ``tools``      API chat tools ``place_flow_cards`` / ``start_flow_run`` and the CLI mention section

The instruction of a placed card is written by the chat with the existing card document edit
(``card_document_edit``: slot ``instruction``); a written instruction skips the instruction AI at run time
(``node_dispatch._instruction_job``). Waking the chat when the run stops is stage 2 and not here.
"""
