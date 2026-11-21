"""0127: full SP state matrix over the WP transport."""
import pytest

from l3verify_support_0127 import l3, kind  # noqa: F401
from l3_states_support_0127 import SP, check_sp_state


SCENARIOS = [row for row in SP if not row[1].get('instruction') and row[1].get('legacy')]


@pytest.mark.parametrize('via,kind', [('WP', 'WP')])
@pytest.mark.parametrize('scenario', SCENARIOS, ids=[row[0] for row in SCENARIOS])
def test_SP_literal_table_T1_T2_T3(l3, monkeypatch, via, kind, scenario):
    check_sp_state(l3, monkeypatch, via, kind, scenario)
