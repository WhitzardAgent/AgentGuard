"""Policy Evaluation Loop: driven by PolicyActor's mailbox consumer."""

from __future__ import annotations


# The PolicyActor handles its own mailbox loop via BaseActor._run_loop().
# This module exists for symmetry with Instruction.md §5 table and can
# host additional pre/post-processing logic if needed.
