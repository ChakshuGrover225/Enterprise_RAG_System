---
name: Notetaker
description: >
  Summarizes the current conversation into a notes file. Use when the user
  says "take notes", "note this down", "save a summary of this", or invokes
  "/Notetaker".
argument-hint: "[optional title]"
---

# Notetaker

Summarize the conversation so far into a concise Markdown note, then save it.

## Steps

1. Pull out what matters: decisions made, open questions, action items, and any facts/code worth remembering. Skip small talk and dead ends.
2. Write it as Markdown with this shape:

```markdown
# <title>

<!-- date -->

## Summary
<2-4 sentences>

## Decisions
- ...

## Action items
- ...

## Open questions
- ...
```

Omit any section with nothing in it.

3. Save to `notes/<YYYY-MM-DD>-<slug>.md` under the project root (create `notes/` if missing), where `<slug>` is a short kebab-case version of the title. If the user gave a title as an argument, use it; otherwise derive one from the conversation topic.
4. Reply with the file path and the summary text, nothing else.
