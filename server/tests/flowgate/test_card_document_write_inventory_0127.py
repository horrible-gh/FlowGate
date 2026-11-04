"""L#2 §2.8.2 inventory: detect raw writers without a port, private CAS/history or duplicate contacts."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = json.loads((Path(__file__).parent/'fixtures'/'card_document_write_inventory_0127.json').read_text(encoding='utf-8'))


def raw_writers(source):
    tree = ast.parse(source)
    parents = {c:n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    found = []
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):
            continue
        fn = node.func
        writer = isinstance(fn,ast.Attribute) and fn.attr in {'write_text','write_bytes','rename','unlink','touch'}
        if isinstance(fn,ast.Attribute) and fn.attr=='replace' and len(node.args)==1:
            writer = True  # Path.replace(target), unlike str.replace(old, new)
        if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name) and fn.value.id in {'os','shutil'} and fn.attr in {'replace','rename','move','copy','copy2','copyfile','copytree','remove','unlink','rmtree'}:
            writer = True
        if (isinstance(fn,ast.Name) and fn.id=='open') or (isinstance(fn,ast.Attribute) and fn.attr=='open'):
            if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name) and fn.value.id == 'os':
                flags = node.args[1] if len(node.args)>1 else next((k.value for k in node.keywords if k.arg=='flags'),None)
                expression = flags
                if isinstance(flags,ast.Name):
                    definitions = [a.value for a in ast.walk(tree) if isinstance(a,ast.Assign)
                                   and any(isinstance(t,ast.Name) and t.id==flags.id for t in a.targets)]
                    expression = definitions[0] if len(definitions)==1 else flags
                text = ast.unparse(expression) if expression is not None else ''
                readonly = ('O_RDONLY' in text and not any(flag in text for flag in
                            ('O_WRONLY','O_RDWR','O_CREAT','O_TRUNC','O_APPEND','O_EXCL')))
                writer = not readonly and not (isinstance(flags,ast.Constant) and flags.value==0)
                mode = None
            else:
                mode = node.args[1] if isinstance(fn,ast.Name) and len(node.args)>1 else node.args[0] if isinstance(fn,ast.Attribute) and node.args else None
            mode = next((k.value for k in node.keywords if k.arg=='mode'),mode)
            writer = writer or isinstance(mode,ast.Constant) and isinstance(mode.value,str) and any(c in mode.value for c in 'wax+')
        if not writer:
            continue
        ancestor, name, protected = node, None, False
        while ancestor in parents:
            ancestor = parents[ancestor]
            if isinstance(ancestor,ast.Call) and ast.unparse(ancestor.func).endswith('write_gate'):
                protected = True
            if isinstance(ancestor,ast.With) and any(ast.unparse(i.context_expr).split('(')[0].endswith(('write_paths','paths_scope')) for i in ancestor.items):
                protected = True
            if isinstance(ancestor,(ast.FunctionDef,ast.AsyncFunctionDef)) and name is None:
                name = ancestor.name
        found.append((name,protected,node.lineno))
    return found


def function(path, name):
    tree = ast.parse((ROOT/path).read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
    if isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant):
        node.body = node.body[1:]
    return ast.unparse(node)


def test_inventory_has_no_unclassified_or_unguarded_raw_writer():
    assert len(MANIFEST) >= 30
    issues = []
    for path, expected in MANIFEST.items():
        source = (ROOT/path).read_text(encoding='utf-8')
        actual = raw_writers(source)
        if {name for name,_,_ in actual} != set(expected):
            issues.append(f'{path}: writer functions differ: actual={sorted({name for name,_,_ in actual})}, expected={sorted(expected)}')
        assert set(expected.values()) <= {'PORT','IDENTITY','EXCLUSIVE'}, path
        for name, protected, line in actual:
            if name not in expected:
                continue  # recorded above; keep collecting the other violations
            if expected[name] == 'PORT':
                if not protected:
                    issues.append(f'{path}:{line} {name}: raw body writer without a port')
            elif expected[name] == 'IDENTITY':
                if 'is_same_file_as' not in function(path,name):
                    issues.append(f'{path}:{name}: unowned rollback')
            else:
                body = function(path,name)
                if not ('document_using_file' in body and "'xb'" in body and 'track_new_file' in body):
                    issues.append(f'{path}:{name}: nonexclusive snapshot')
    for path in (ROOT/'server/flowgate/card_document_edit').glob('*.py'):
        if raw_writers(path.read_text(encoding='utf-8')):
            if path.relative_to(ROOT).as_posix() not in MANIFEST:
                issues.append(f'{path.name}: unclassified module')
    assert not issues, '\n'.join(issues)


def test_revision_entry_points_delegate_to_the_single_storage_boundary():
    entries = {
      'server/modules/flow_gate/api/inbox_routes.py': {'_handle_edit':'card_edit'},
      'server/modules/flow_gate/documents/routers/work_plan.py': {'save_work_plan':'dedicated_write','restore_work_plan_revision':'dedicated_write','_heal_unwritten_plan':'dedicated_write'},
      'server/modules/flow_gate/documents/tr2_service.py': {'_save':'dedicated_write','_backfill_current_snapshot':'dedicated_write'},
      'server/modules/flow_gate/documents/body_recovery.py': {'recover_one':'dedicated_write','_apply':'write_gate'},
      'server/modules/flow_gate/documents/root_conversion.py': {'execute_root_conversion':'dedicated_write','stage_converted_file':'dedicated_write'},
      'server/modules/flow_gate/documents/document_service.py': {'convert_by_number_swap':'dedicated_write','update_document':'write_gate'},
      'server/modules/flow_gate/db/documents.py': {'update':'write_gate','create':'write_gate'},
      'server/modules/flow_gate/documents/routers/documents.py': {'_save_write_checks':'write_gate','stage_body_save':'write_gate'},
    }
    forbidden = ('revision_no = revision_no + 1', 'doc_edited', 'record_body_saved', 'publish_canvas_changed', '_broadcast_document_refresh')
    for path, names in entries.items():
        for name, port in names.items():
            source = function(path,name)
            assert port in source, (path,name,port)
            # _apply is the retained NON_CARD recovery implementation; it is guarded explicitly.
            if name not in {'_apply','convert_by_number_swap'}:
                assert not any(value in source for value in forbidden), (path,name)
    tr2 = (ROOT/'server/modules/flow_gate/documents/tr2_service.py').read_text(encoding='utf-8')
    assert 'def _cas_bump(' not in tr2 and 'def _record_snapshot(' not in tr2
    deploy = function('server/flowgate/work_plan_deploy/service.py','_write')
    assert 'deployment_bridge.fill' in deploy and 'stage_body_save' not in deploy
    store = function('server/flowgate/card_document_edit/store.py','commit_revision')
    assert 'revision(ctx, inp)' in store and '_commit_revision' in store


def test_state_only_allowlist_does_not_gain_body_columns():
    source = function('server/modules/flow_gate/db/documents.py','update_document_fields')
    # Explicit allowlist is an additional independent boundary to the generic update gate.
    tree = ast.parse(source)
    literal_sets = [n for n in ast.walk(tree) if isinstance(n,(ast.Set,ast.List,ast.Tuple))]
    strings = {n.value for collection in literal_sets for n in collection.elts if isinstance(n,ast.Constant) and isinstance(n.value,str)}
    assert strings and not strings.intersection({'file_path','filename','revision_no','project_id','branch'})


def test_inventory_detects_replacement_deletion_and_descriptor_writes():
    source = """def mutation(p, q):
    p.replace(q)
    p.unlink()
    p.touch()
    os.remove(q)
    os.unlink(q)
    shutil.rmtree(q)
    os.open(q, os.O_WRONLY | os.O_CREAT)
    os.open(q, flags=os.O_RDWR)
    with p.open('rb') as stream:
        stream.read()
"""
    assert len(raw_writers(source)) == 8
    assert not raw_writers("def read(p):\n    return os.open(p, 0)\n")
