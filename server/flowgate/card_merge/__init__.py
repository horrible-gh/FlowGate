"""flowgate.v02test.0042: a card's result merge, seen and handled on the card itself (the inspector's '병합 · 충돌' tab).

A card result's merge conflict is resolved on the card, never sent to the Git tab: ``service`` reads the card's merge
state (why it waits and behind which card, the overlapping files, what a person may do) and runs a person's choice
(an AI resolution, a per-file pick, the merge, or discarding the result for a new attempt on the latest code) through
the existing conflict session, commit, abort and card retry paths. ``routes`` is its HTTP adapter (user JWT only).
"""
