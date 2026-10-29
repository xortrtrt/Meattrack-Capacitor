# Project knowledge

Before making substantive changes, read `docs/knowledge/Start Here.md`, then the
topic notes relevant to the task. This repository is also an Obsidian vault;
its linked Markdown notes are persistent project context for humans and agents.
Obsidian does not need to be running to read or maintain them.

Treat executable code, schema, migrations, and verified tests as the source of
truth. Notes describe understanding, not proof that a deployed system matches.
Follow source links and check current implementation before relying on a rule.
Check `docs/knowledge/Questions and Contradictions.md` for known documentation drift.

When a task changes a business rule, module relationship, workflow, or operating
procedure, update the relevant topic note in the same change. Link related notes
with wikilinks, cite repository source paths, and update the note's `verified`
date only when its claims have been checked. Record consequential decisions and
their rationale in `docs/knowledge/Decisions.md`. Do not turn routine edits into
lengthy journal entries or claim tests/deployments that were not performed.

Keep credentials, environment values, customer data, payment evidence, activation
links, private reports, and backups out of the knowledge notes.
