# Relay project instructions

Relay is a fault-tolerant orchestration system that resumes an interrupted Claude
Code task where it stopped. See DESIGN.md (design of record plus the numbered
findings) and HANDOVER.md (cold-start guide for the next session).

## Writing style

- No em-dashes (the U+2014 character) in any prose, code comments, README, or
  commit messages. Use commas, parentheses, or sentence breaks instead.

## Hard rules carried through the code

- Plain-text status labels ([OK], [ERR], [INFO], [WAIT], [WARN], [FATAL],
  [GAVE_UP]); no emoji in logs.
- No credentials in code or logs (tokens masked to at most 4 chars).
- One thing per change; a syntax check and a test after each module.
- Cross-platform (Windows included); UTC-timestamped logging.
- compose_resume_prompt is an invariant: change what goes into the checkpoint,
  never the formatter.
