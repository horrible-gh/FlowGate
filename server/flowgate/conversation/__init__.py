"""Conversation production contract (flowgate.v02.0032 D0006).

* chat command requests: approval-gated command execution for chat AI runs, one
  contract for both transports (API ``run_command`` tool, CLI ``/chat-commands``);
* run changes: what one chat AI run changed in the group worktree, measured from
  start/end tree snapshots;
* activity: commands and run changes of a CH, placed in the conversation by a
  server-decided anchor;
* standalone CH creation for the Conversation [new chat] button.

Module map: ``contracts`` (wire names, codes, limits), ``routes`` (HTTP adapter),
``chats``, ``commands``, ``command_policy``, ``activity``, ``run_changes``,
``repository`` (new tables), ``integration`` (chat run hook handlers) and
``legacy_adapter`` (the one boundary to ``modules.flow_gate``).
"""
