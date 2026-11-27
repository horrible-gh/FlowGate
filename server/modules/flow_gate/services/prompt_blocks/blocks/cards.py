"""Card blocks: the bound Graph state read.

``graph_state_read`` moved verbatim from ``flowgate.graph_read.tools.cli_mention``;
that function now delegates here. The card control and card
question contracts stay in their chat tool modules under ``server/flowgate`` (they are also the
API tool descriptors' contracts, and ``modules.flow_gate`` must not import ``flowgate``).
"""
from __future__ import annotations

from ..registry import Block, register

# The display ids the canvas read carries (card 0077 numbering) and how to say them to the user.
DISPLAY_IDS = ("Canvas names: flow_name F77(name), nodes[].card_name F77-12(card title), card_ref (#12, full id). "
               "Say these to the user; API calls keep flow_id/node_key/doc_id.")


def graph_state_read(api_base, _raw_token=None):
    """Bound Graph state read (canvas / runs/current); moved verbatim from ``graph_read.tools.cli_mention``."""
    base = api_base.rstrip("/")
    base = base[:-len("/api/v1")] if base.endswith("/api/v1") else base
    return ("[Bound Graph state read]\n"
            f"GET {base}/api/v2/ai/flows/{{flow_id}}/canvas\n"
            f"GET {base}/api/v2/ai/flows/{{flow_id}}/runs/current\n"
            "Use this run's existing worker/chat Bearer, never a user JWT. Only your bound group is readable; "
            "a card worker sees only its active node/attempt. Chat canvas defaults to latest; view=revision "
            "shows the execution definition. These read-only calls return stored state. "
            "Chat returns applied chat_mode. Failed edit_once yields read_only. Never log the Bearer.\n"
            + DISPLAY_IDS)


register(Block("cards.state", brief=graph_state_read, detail=graph_state_read, help_leaf="cards/state"))
