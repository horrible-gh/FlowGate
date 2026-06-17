import sys; sys.path.insert(0, '.')
from modules.flow_gate.linter import parse_yaml_header, lint_header

# Scenario 1: approved_files with 2 items
content1 = """---
type: DC
project: server
title: test
group_id: server-mod-0001
target_id: server-mod-DC001
approved_files:
  - file_a.md
  - file_b.md
---
body
"""
h1, e1 = parse_yaml_header(content1)
print('S1 approved_files:', h1.get('approved_files'), '| expected: [file_a.md, file_b.md]')
assert h1['approved_files'] == ['file_a.md', 'file_b.md'], 'FAIL: ' + str(h1.get('approved_files'))

# Scenario 2: approved_files empty
content2 = """---
type: DC
project: server
title: test
group_id: server-mod-0001
target_id: server-mod-DC001
approved_files:
---
body
"""
h2, e2 = parse_yaml_header(content2)
print('S2 approved_files:', h2.get('approved_files'), '| expected: []')
assert h2.get('approved_files') == [], 'FAIL: ' + str(h2.get('approved_files'))

# Scenario 3: DC without approved_files -> lint error
errors3 = lint_header({'type': 'DC', 'project': 'server', 'title': 'x', 'group_id': 'server-mod-0001', 'target_id': 'server-mod-DC001'}, set())
print('S3 errors:', errors3)
assert any('approved_files' in e for e in errors3), 'FAIL: ' + str(errors3)

# Scenario 4: DC with approved_files items -> no approved_files error
errors4 = lint_header({'type': 'DC', 'project': 'server', 'title': 'x', 'group_id': 'server-mod-0001', 'target_id': 'server-mod-DC001', 'approved_files': ['a.md']}, set())
print('S4 errors:', errors4)
assert not any('approved_files' in e for e in errors4), 'FAIL: ' + str(errors4)

print('ALL SCENARIOS PASSED')
