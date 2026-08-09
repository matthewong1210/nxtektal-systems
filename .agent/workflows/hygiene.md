# Hygiene workflow

Run hygiene after tests and before handoff.

## Repository checks

From the repository root:

```bash
git status --short --branch
git diff --check
git diff --stat
git diff --name-status
git ls-files --others --exclude-standard
```

Inspect both tracked and untracked lists. Distinguish pre-existing files from
the current task; do not delete or absorb unrelated work.

`git diff --check` ignores untracked files. For each new text file, also run:

```bash
git diff --no-index --check /dev/null <new-file>
```

Exit status `1` is expected because the files differ; any command output is a
whitespace error to fix.

## Diff checks

- Read the complete final diff.
- Confirm every file is within the authorized scope.
- Confirm no source/production file changed in a documentation-only task.
- Search changed text for secrets, tokens, credentials, personal data, debug
  dumps, machine-specific absolute paths, and accidental generated output.
- Check Markdown links and command paths from the repository root or their
  documented working directory.
- Ensure no scaffold `TODO`, placeholder instruction, or sample text remains in
  agent skills.

## Skill checks

For every changed `.agent/skills/<name>/`:

- Confirm the folder name equals the frontmatter `name`.
- Keep YAML frontmatter to `name` and `description` only.
- Make the description state both capability and trigger conditions.
- Use imperative instructions and keep the body concise.
- Confirm `agents/openai.yaml` strings are quoted and `default_prompt` names the
  skill as `$<name>`.
- Run the available skill validator and resolve all findings.

## Result record

Report:

- exact hygiene commands;
- clean/error result;
- files intentionally created/modified;
- any pre-existing dirty files left untouched;
- any checks unavailable in the environment.

Do not call the worktree clean if unrelated changes remain. Say the task diff
is clean while naming the remaining pre-existing state.
