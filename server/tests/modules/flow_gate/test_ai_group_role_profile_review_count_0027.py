"""flowgate.v02test.0027 (0007-T, A-server): the AI role group's default review count.

Real migrated SQLite (migration 191) and the real routes, reusing the 0120 fixture: stored per profile row (NULL = the
type row inherits common, common NULL = the card default 0), its own write route, the server policy's choices without
-1 for a new value, an already stored value outside them kept, administrator only.
"""
import pytest
from test_ai_group_role_profiles_0120 import world, create  # noqa: F401 - reuse the real DB/API fixture


def put_count(w, gid, type_key, body, user="admin"):
    return w['client'].put(f'/api/v1/ai-groups/{gid}/role-profiles/{type_key}/review-count',
                           headers={'X-User': user}, json=body)


def counts(w, gid):
    r = w['client'].get(f'/api/v1/ai-groups/{gid}/role-profiles', headers={'X-User': 'user'})
    assert r.status_code == 200, r.text
    return r.json()['review_counts']


def test_migration_column_existing_rows_null_and_default_zero(world):
    columns = {row['name'] for row in world['store']._fetch_all("PRAGMA table_info(ai_group_role_profiles)", [])}
    assert 'review_count' in columns
    gid = create(world)
    world['profiles'].add(gid, 'T')
    assert [row['review_count'] for row in world['repo'].list_profiles(gid)] == [None, None]
    assert counts(world, gid) == {
        'common': {'review_count': None, 'effective_review_count': 0, 'source': 'default'},
        'T': {'review_count': None, 'effective_review_count': 0, 'source': 'default'}}
    # The AI cells keep their shape (0120 contract): the count is not one of the items' fields.
    assert all(set(item) == {'type_key', 'position', 'roles'} for item in world['profiles'].profiles(gid)['items'])


def test_common_and_type_save_reread_inherit_and_clear(world):
    gid = create(world)
    world['profiles'].add(gid, 'T')
    world['profiles'].add(gid, 'N')
    r = put_count(world, gid, 'common', {'review_count': 2})
    assert r.status_code == 200, r.text
    assert r.json()['review_counts']['T'] == {'review_count': None, 'effective_review_count': 2, 'source': 'common'}
    assert put_count(world, gid, 'T', {'review_count': 0}).status_code == 200
    assert put_count(world, gid, 'N', {'review_count': 3}).status_code == 200
    got = counts(world, gid)
    assert got['common'] == {'review_count': 2, 'effective_review_count': 2, 'source': 'common'}
    assert got['T'] == {'review_count': 0, 'effective_review_count': 0, 'source': 'type'}    # explicit 0 kept
    assert got['N'] == {'review_count': 3, 'effective_review_count': 3, 'source': 'type'}
    assert world['repo'].get(gid, 'T')['review_count'] == 0
    assert put_count(world, gid, 'T', {'review_count': None}).status_code == 200
    assert counts(world, gid)['T'] == {'review_count': None, 'effective_review_count': 2, 'source': 'common'}
    assert put_count(world, gid, 'common', {'review_count': None}).status_code == 200
    assert counts(world, gid)['T'] == {'review_count': None, 'effective_review_count': 0, 'source': 'default'}
    # AI cells are untouched by the count write.
    assert world['profiles'].profiles(gid)['items'][1]['roles']['review'] == {'kind': 'inherit'}


@pytest.mark.parametrize('body', [{'review_count': -1}, {'review_count': 31}, {'review_count': 4},
                                  {'review_count': '2'}, {'review_count': True}, {'review_count': 1.5},
                                  {}, {'review_count': 1, 'kind': 'assigned'}])
def test_invalid_values_rejected_and_nothing_stored(world, monkeypatch, body):
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    monkeypatch.setattr(policy, 'get_repeat_count_max', lambda: 3)
    gid = create(world)
    r = put_count(world, gid, 'common', body)
    assert r.status_code == 422, r.text
    assert world['repo'].get(gid, 'common')['review_count'] is None


def test_permissions_unknown_row_general_group_and_ai_cell_payload_unchanged(world):
    gid = create(world)
    assert put_count(world, gid, 'common', {'review_count': 1}, user='user').status_code == 403
    assert world['client'].put(f'/api/v1/ai-groups/{gid}/role-profiles/common/review-count',
                               json={'review_count': 1}).status_code == 401
    r = put_count(world, gid, 'T', {'review_count': 1})
    assert (r.status_code, r.json()['error']['code']) == (404, 'ai_role_profile_not_found')
    general = create(world, 'General', 'general')
    r = put_count(world, general, 'common', {'review_count': 1})
    assert (r.status_code, r.json()['error']['code']) == (409, 'ai_role_group_required')
    # The AI cell write does not take a count on the side.
    r = world['client'].put(f'/api/v1/ai-groups/{gid}/role-profiles/common/roles/review', headers={'X-User': 'admin'},
                            json={'kind': 'not_used', 'review_count': 2})
    assert r.status_code == 422
    assert world['repo'].get(gid, 'common')['review_count'] is None
    with pytest.raises(world['groups'].AIGroupError, match='ai_role_profile_review_count_invalid'):
        world['profiles'].set_review_count(gid, 'common', {'review_count': 1, 'extra': 1})


def test_choices_follow_server_policy_without_until_pass(world, monkeypatch):
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    gid = create(world)
    options = world['client'].get(f'/api/v1/ai-groups/{gid}/role-profile-options', headers={'X-User': 'user'}).json()
    assert options['review_count_choices'] == list(policy.repeat_count_choices(allow_zero=True, allow_unlimited=False))
    assert -1 not in options['review_count_choices'] and options['review_count_choices'][0] == 0
    assert options['review_count_default'] == 0
    monkeypatch.setattr(policy, 'get_repeat_count_max', lambda: 5)
    options = world['client'].get(f'/api/v1/ai-groups/{gid}/role-profile-options', headers={'X-User': 'user'}).json()
    assert options['review_count_choices'] == [0, 1, 2, 3, 4, 5]
    assert put_count(world, gid, 'common', {'review_count': 5}).status_code == 200


def test_stored_value_outside_choices_is_kept_on_read_and_resave(world, monkeypatch):
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    gid = create(world)
    monkeypatch.setattr(policy, 'get_repeat_count_max', lambda: 5)
    assert put_count(world, gid, 'common', {'review_count': 5}).status_code == 200
    monkeypatch.setattr(policy, 'get_repeat_count_max', lambda: 3)          # the ceiling was lowered later
    # The stored 5 is shown as it is, but a new card would copy 0 (outside today's card choices): the effective value
    # and its source follow the creation copy's judgment (TR0008 rev2 rejection).
    assert counts(world, gid)['common'] == {'review_count': 5, 'effective_review_count': 0, 'source': 'default'}
    assert put_count(world, gid, 'common', {'review_count': 5}).status_code == 200     # same value: kept
    assert put_count(world, gid, 'common', {'review_count': 4}).status_code == 422     # a new out-of-range value
    with world['store'].transaction():
        world['repo'].set_review_count(gid, 'common', -1)                    # e.g. written by an older rule
    assert counts(world, gid)['common'] == {'review_count': -1, 'effective_review_count': -1, 'source': 'common'}
    assert put_count(world, gid, 'common', {'review_count': -1}).status_code == 200
    assert world['repo'].get(gid, 'common')['review_count'] == -1


def test_type_row_delete_and_readd_starts_inheriting(world):
    gid = create(world)
    world['profiles'].add(gid, 'T')
    assert put_count(world, gid, 'T', {'review_count': 1}).status_code == 200
    world['profiles'].remove(gid, 'T')
    world['profiles'].add(gid, 'T')
    assert counts(world, gid)['T']['review_count'] is None
