"""The grammar's view of the shared role vocabulary (`ldraw_tools/role_vocab.py`).

Every placement the grammar emits carries exactly one of these in `role`. Edits such as "make the roof blue"
select by role, so a role must mean the same thing in every building.
"""
from __future__ import annotations

from ..role_vocab import GRAMMAR_ROLES, GRAMMAR_WORDS, ROLES as _ALL, role_for_word  # noqa: F401

ROLES = {name: _ALL[name] for name in GRAMMAR_ROLES}
ROLE_NAMES = tuple(ROLES)

# Words people use for each role, for natural-language addressing ("make the roof blue").
ALIASES = GRAMMAR_WORDS
