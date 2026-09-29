-- 0565 T0030 §2: user-facing names T2 = 반영지시 (Apply Instruction), TR2 = 반영안 (Apply Proposal).
-- Only rows still holding the 113 seed text are renamed; a project's own relabel is kept.
UPDATE document_types SET type_name='반영지시' WHERE project_id IS NULL AND type_code='T2' AND type_name='변경제안 지시';
UPDATE document_types SET type_name='반영안' WHERE project_id IS NULL AND type_code='TR2' AND type_name='변경제안';
UPDATE document_type_names SET type_name='반영지시' WHERE locale='ko' AND type_name='변경제안 지시' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='T2');
UPDATE document_type_descriptions SET description='실제 반영 전에 반영안을 작성합니다.' WHERE locale='ko' AND description='TR2 변경제안을 작성하고 승인 후 적용할 변경을 지시한다.' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='T2');
UPDATE document_type_names SET type_name='Apply Instruction' WHERE locale='en' AND type_name='Change Proposal Instruction' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='T2');
UPDATE document_type_descriptions SET description='Prepares an apply proposal before anything is applied.' WHERE locale='en' AND description='Direct a TR2 proposal; changes apply after approval.' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='T2');
UPDATE document_type_names SET type_name='反映指示' WHERE locale='ja' AND type_name='変更提案指示' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='T2');
UPDATE document_type_descriptions SET description='実際に反映する前に反映案を作成します。' WHERE locale='ja' AND description='TR2 の変更提案を指示し、承認後に変更を適用する。' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='T2');
UPDATE document_type_names SET type_name='반영안' WHERE locale='ko' AND type_name='변경제안' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='TR2');
UPDATE document_type_descriptions SET description='승인하면 이 내용이 실제 작업물에 반영됩니다.' WHERE locale='ko' AND description='소스 변경을 제안하고 사람 승인 후 적용하는 문서.' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='TR2');
UPDATE document_type_names SET type_name='Apply Proposal' WHERE locale='en' AND type_name='Change Proposal' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='TR2');
UPDATE document_type_descriptions SET description='When approved, this content is applied to the actual work.' WHERE locale='en' AND description='Propose source changes for application after human approval.' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='TR2');
UPDATE document_type_names SET type_name='反映案' WHERE locale='ja' AND type_name='変更提案' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='TR2');
UPDATE document_type_descriptions SET description='承認すると、この内容が実際の作業物に反映されます。' WHERE locale='ja' AND description='ソース変更を提案し、人の承認後に適用する文書。' AND document_type_id IN (SELECT id FROM document_types WHERE project_id IS NULL AND type_code='TR2');
