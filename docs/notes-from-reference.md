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
