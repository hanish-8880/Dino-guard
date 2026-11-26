# Notes from the reference project

Rules: own words only, links for context, no code or text copied. Plan changes need the owner's OK.

- The reference docs were **not** read for this build. The design follows CLAUDE.md directly. The only external
  source read was the Claude Code hooks reference (https://code.claude.com/docs/en/hooks), used to verify the
  adapter (see docs/adapters/claude.md).
- Things the current Claude Code hook docs offer that CLAUDE.md doesn't use yet (worth discussing before adopting):
  - `permissionDecision: "ask"` (route to the normal permission prompt) and `"defer"` (non-interactive mode).
  - `updatedInput` on PreToolUse (rewrite the tool input instead of denying), e.g. to add `--dry-run`.
  - `PostToolUse.updatedToolOutput` could replace output that contains secrets, instead of only instructing.
  - New events: `PreSkillShellCommand`, `MessageDisplay`, `PreModelSwitch` / `PostModelSwitch`, `DirectoryAdded`.

## 2026-11-26: feature inventory (supplied by the owner, not read from the reference repo)
- The owner pasted a full inventory of the reference product. Gaps against our build were added to CLAUDE.md §17
  (Phase 12 built, Phase 13 planned) with the owner's go-ahead. Everything is our own design and naming.
- Ideas taken from it, in our words: an agent "contract" (should / should not) checked during audits; audit goal,
  selection and reference context; audit history with coverage; reopening a fixed insight when its failure comes
  back; comparing sessions before and after a model or prompt change; grouping errors; offline and mirror switches
  for pack downloads; `pack:policy` names.
- Weak spots of the reference product that we design against: blocking power varies by harness (we print a
  capability table), post-tool blocks can't undo (planned git checkpoint hint), traces hold sensitive data
  (retention + extra redaction + no-output mode), run caps (we have none), hook formats drift between versions
  (drift table + doctor warnings).
