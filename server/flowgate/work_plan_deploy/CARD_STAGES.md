# v0.2 card-internal stage contract — T0017 / D0006 revision

Implementation contract following the latest rejection, rej_01M5C0BVVSBAWARX. The following replaces the single design step in D0006 §3.3·3.4·7.7, the interpretation of design-result pre-instructions as an independent DS structure, and the deferral of note forwarding. Existing deployment transactions, document protection, and approval authority contracts remain in force.

## Storage

- v0.2 WP uses `card_stage_model: paired`. Each D/P/L/DB design card stores two steps, instruction/result, sharing a card_id. The design instruction key is `DS{설계종류}#{ordinal}` (where 설계종류 is the design type), its type is DS for compatibility with existing document storage, and pair_key is the result key. Preserve the result's existing key/type. Do not create independent DS cards.
- Instructions and results each retain their own provider_id, reviewer_provider_id, review_count, note, edited text, and attachments. The design-document pencil edits the result step's inputs independently of the instruction pencil.
- Convert existing single design steps into pairs on their first v0.2 edit/save. Preserve card_id, result key, and existing data. Copy previously shared pre-instructions to the instruction once, retaining edited result values; subsequent edits are independent. Preserve existing independent DS records as compatibility history, without linking them by ordinal to new cards' instructions.

## Deployment and execution

- Map instruction AI, review AI, review count, and note to instruction_ai / instruction_review_ai / instruction_review_count / instruction_note; map result settings to run_ai / review_ai / review_count / run_note. Pass the design result's edited text and attachments through run_pre_instruction.
- Redeployment reflects changes and removals of settings for stages that have not started. Changes to settings of started cards block deployment. Instruction-omission changes also apply the existing Graph start lock during save, AI save, and version restoration.
- If the instruction has no body, write it with the instruction AI; otherwise preserve the existing body. If review count is nonzero, run the instruction's own review/rework loop. Do not proceed to result writing after rejection, failure, or exhausted review. After review passes, continue through existing approval-authority handling and result execution. Zero follows the existing automatic approval path.
- Review execution belongs to the same Graph attempt. Preserve Stop/stale-attempt protection and execution leases. Do not add an independent DS workflow or separate card engine.
- Include a one-line note only in the corresponding stage's AI input. Do not duplicate the instruction's one-line note in result input.

## Omission and UI

- D/P/L/DB and N/T all store instruction_mode=use/omit on the instruction step. use means two stages; omit means only the result stage. Omission does not delete instruction/design-document/report IDs, bodies, attachments, or settings. An omit run starts with the result, without instruction writing, review, or reference.
- Removing a previous omit selection or saving null normalizes to use; reads, redeployment, and execution use that same value.
- The design-instruction row uses the same AI, review, one-line note, and editing controls as ordinary instructions. Preserve the design-document pencil, existing approval behavior, and instruction-writing shortcut (✓✓). Keep ✓✓ separate from the execution-stage omission checkbox because their existing meanings differ.

## Verification boundaries

`test_work_plan_paired_stages_0074.py` uses the real save routes, DB, deployment, Graph execution, and review follow-up handling, with a substitute for external AI execution. Vue component tests use API fixtures. Passing these tests does not replace verification with a real browser and server or successful external AI CLI execution. Record execution results and browser-environment blockers in TR0018.
