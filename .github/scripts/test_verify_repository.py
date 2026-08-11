"""Regression tests for deterministic repository hygiene verification."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import verify_repository as verifier


class RepositoryVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self._original_root = verifier.ROOT
        verifier.ROOT = self.root
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def tearDown(self) -> None:
        verifier.ROOT = self._original_root
        self._temporary.cleanup()

    def write(self, relative: str, text: str = "content\n") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_repository_paths_include_untracked_but_not_ignored(self) -> None:
        self.write(".gitignore", "ignored.txt\n")
        self.write("tracked.txt")
        self.write("untracked.txt")
        self.write("ignored.txt")
        subprocess.run(
            ["git", "add", ".gitignore", "tracked.txt"], cwd=self.root, check=True
        )

        relative = {path.relative_to(self.root).as_posix() for path in verifier.tracked_paths()}

        self.assertIn("tracked.txt", relative)
        self.assertIn("untracked.txt", relative)
        self.assertNotIn("ignored.txt", relative)

    def test_force_tracked_ignored_output_is_rejected(self) -> None:
        self.write(".gitignore", "generated/\n")
        self.write("generated/build.txt")
        self.write("global-ignore", "*.log\n")
        self.write("keep.log")
        subprocess.run(
            ["git", "config", "core.excludesFile", "global-ignore"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "add", ".gitignore"], cwd=self.root, check=True
        )
        subprocess.run(
            ["git", "add", "-f", "generated/build.txt", "keep.log"],
            cwd=self.root,
            check=True,
        )

        self.assertEqual(
            verifier.tracked_ignored_paths(), ["generated/build.txt"]
        )

    def test_git_index_gitlinks_are_rejected_as_submodules(self) -> None:
        index = (
            f"100644 {'a' * 40} 0\tREADME.md\0"
            f"160000 {'b' * 40} 0\tvendor/dependency with spaces\0"
        )

        self.assertEqual(
            verifier.gitlinks_from_index(index), ["vendor/dependency with spaces"]
        )

    def test_symlink_is_rejected_without_content_read(self) -> None:
        target = self.write("outside.txt", "outside content\n")
        link = self.root / "linked.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:  # pragma: no cover - platform capability
            self.skipTest(f"symlink unavailable: {exc}")

        errors = verifier.verify_paths([link])

        self.assertTrue(any("symlink" in error for error in errors))
        self.assertIsNone(verifier.text_content(link))

    def test_reference_link_heading_anchor_and_duplicate_slug(self) -> None:
        guide = self.write(
            "guide.md",
            "# [Linked heading](other.md)\n\n## Repeat\n\n## Repeat\n",
        )
        self.write("other.md", "# Other\n")
        source = self.write(
            "README.md",
            "Read the [guide][manual].\n\n[manual]: guide.md#linked-heading\n"
            "\n[second](guide.md#repeat-1)\n",
        )
        markdown = {
            source: source.read_text(encoding="utf-8"),
            guide: guide.read_text(encoding="utf-8"),
            self.root / "other.md": (self.root / "other.md").read_text(encoding="utf-8"),
        }

        self.assertEqual(verifier.verify_markdown(markdown), [])

    def test_missing_reference_target_fails(self) -> None:
        source = self.write(
            "README.md", "Read [manual][ref].\n\n[ref]: missing(guide).md\n"
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))

    def test_multiline_inline_link_fails_closed(self) -> None:
        source = self.write("README.md", "[broken](\nmissing.md)\n")

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("links must finish on one line" in error for error in errors))

    def test_multiline_link_label_target_is_checked(self) -> None:
        source = self.write(
            "README.md", "[broken\n[nested]\nlabel](missing.md)\n"
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))

    def test_unmatched_prose_bracket_does_not_capture_next_reference_link(self) -> None:
        guide = self.write("guide.md", "# Guide\n")
        source = self.write(
            "README.md",
            "Array [ notation.\n[guide][ref]\n\n[ref]: guide.md\n",
        )

        errors = verifier.verify_markdown(
            {
                source: source.read_text(encoding="utf-8"),
                guide: guide.read_text(encoding="utf-8"),
            }
        )

        self.assertEqual(errors, [])

    def test_unmatched_prose_bracket_does_not_capture_multiline_link(self) -> None:
        source = self.write(
            "README.md",
            "Array [ notation.\n[broken\nlabel](missing.md)\n",
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))
        self.assertFalse(
            any("reference-link labels must finish" in error for error in errors)
        )

    def test_links_cannot_resolve_through_git_or_ignored_leftovers(self) -> None:
        ignored = self.write("ignored.md", "# Ignored\n")
        source = self.write(
            "README.md", "[Git metadata](.git) and [ignored](ignored.md).\n"
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}, {source}
        )

        self.assertTrue(any("directory link has no tracked" in error for error in errors))
        self.assertTrue(any("link target is not tracked" in error for error in errors))
        self.assertTrue(ignored.exists())

    def test_shortcut_reference_resolves_and_checks_its_target(self) -> None:
        source = self.write(
            "README.md",
            "Read the [operator guide]. Plain [brackets] remain prose.\n\n"
            "[operator guide]: missing.md\n",
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertEqual(sum("missing link target" in error for error in errors), 1)
        self.assertFalse(any("undefined reference" in error for error in errors))

    def test_setext_heading_provides_anchor(self) -> None:
        guide = self.write("guide.md", "Setext heading\n==============\n")
        source = self.write("README.md", "[Read it](guide.md#setext-heading)\n")

        errors = verifier.verify_markdown(
            {
                source: source.read_text(encoding="utf-8"),
                guide: guide.read_text(encoding="utf-8"),
            }
        )

        self.assertEqual(errors, [])

    def test_html_comments_do_not_create_links_or_anchors(self) -> None:
        source = self.write(
            "README.md",
            "# Visible\n\n<!--\n[ignored](missing.md)\n# Hidden\n-->\n",
        )

        anchors, links, errors = verifier.markdown_structure(
            source, source.read_text(encoding="utf-8")
        )

        self.assertEqual(errors, [])
        self.assertEqual(links, [])
        self.assertEqual(anchors, {"visible"})

    def test_html_comment_opener_in_inline_code_does_not_hide_links(self) -> None:
        source = self.write(
            "README.md", "`<!--` [broken](missing.md)\n"
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))

    def test_inline_code_after_comment_close_does_not_reopen_comment(self) -> None:
        source = self.write(
            "README.md",
            "<!-- start\n--> `<!--` [broken](missing.md)\n",
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))

    def test_multiline_inline_code_does_not_hide_later_links(self) -> None:
        source = self.write(
            "README.md",
            "`literal\n<!--\nstuff\n`\n[broken](missing.md)\n",
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))

    def test_backslash_does_not_escape_a_code_span_closer(self) -> None:
        source = self.write(
            "README.md",
            "`literal\\`\n[broken](missing.md)\n",
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertTrue(any("missing link target" in error for error in errors))
        self.assertFalse(any("unclosed inline" in error for error in errors))

    def test_inline_code_cannot_mask_links_across_block_boundaries(self) -> None:
        blank = self.write(
            "blank.md",
            "`literal\n\n[broken](missing-blank.md)\nclosing `\n",
        )
        heading = self.write(
            "heading.md",
            "`literal\n# New block\n[broken](missing-heading.md)\n",
        )
        heading_opener = self.write(
            "heading-opener.md",
            "# Heading with `literal\n[broken](missing-heading-opener.md)\nclosing `\n",
        )
        list_item = self.write(
            "list.md",
            "`literal\n- item\n  [broken](missing-list.md)\nclosing `\n",
        )
        blockquote = self.write(
            "blockquote.md",
            "`literal\n> [broken](missing-blockquote.md)\nclosing `\n",
        )
        valid_blockquote = self.write(
            "valid-blockquote.md",
            "> item `code\n> continuation`\n",
        )

        errors = verifier.verify_markdown(
            {
                blank: blank.read_text(encoding="utf-8"),
                heading: heading.read_text(encoding="utf-8"),
                heading_opener: heading_opener.read_text(encoding="utf-8"),
                list_item: list_item.read_text(encoding="utf-8"),
                blockquote: blockquote.read_text(encoding="utf-8"),
                valid_blockquote: valid_blockquote.read_text(encoding="utf-8"),
            }
        )

        self.assertEqual(sum("crosses a block boundary" in error for error in errors), 5)
        self.assertEqual(sum("missing link target" in error for error in errors), 5)

    def test_indented_code_does_not_create_links(self) -> None:
        source = self.write(
            "README.md", "Example:\n\n    [ignored](missing.md)\n"
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertEqual(errors, [])

    def test_inline_code_is_ignored_but_adjacent_link_is_checked(self) -> None:
        source = self.write(
            "README.md",
            "`` `[ignored](missing-one.md)` `` and "
            "`[also ignored](missing-two.md)` but "
            "[checked](missing-three.md).\n",
        )

        errors = verifier.verify_markdown(
            {source: source.read_text(encoding="utf-8")}
        )

        self.assertEqual(sum("missing link target" in error for error in errors), 1)
        self.assertTrue(any("missing-three.md" in error for error in errors))

    def test_fence_with_trailing_info_does_not_close(self) -> None:
        source = self.write("README.md", "```text\nbody\n```not-closed\n")

        _, _, errors = verifier.markdown_structure(
            source, source.read_text(encoding="utf-8")
        )

        self.assertTrue(any("unclosed Markdown fence" in error for error in errors))

    def test_generated_screenshot_egg_info_and_tsbuildinfo_fail(self) -> None:
        paths = [
            self.write("Screenshot 2026-08-10 at 10.00.00.png"),
            self.write("simulation/nxt_sim.egg-info/PKG-INFO"),
            self.write("nxtektal-roi-engine/tsconfig.tsbuildinfo"),
            self.write("apps/operational-replay/out/index.html"),
            self.write("apps/operational-replay/.vercel/project.json"),
        ]

        errors = verifier.verify_paths(paths)

        self.assertEqual(len(errors), 5)

    def test_reports_check_uses_repository_relative_parts(self) -> None:
        readme = self.write("README.md")

        errors = verifier.verify_paths([readme])

        self.assertFalse(any("generated report" in error for error in errors))

    def test_repository_skill_metadata_is_validated(self) -> None:
        skill = self.write(
            ".agent/skills/example-skill/SKILL.md",
            "---\n"
            "name: example-skill\n"
            "description: Do a bounded thing. Use when the bounded thing is requested.\n"
            "---\n\n"
            "# Example skill\n\nFollow the bounded instructions.\n",
        )
        metadata = self.write(
            ".agent/skills/example-skill/agents/openai.yaml",
            "interface:\n"
            '  display_name: "Example Skill"\n'
            '  short_description: "Perform a bounded repository task"\n'
            '  default_prompt: "Use $example-skill when: requested # safely."\n',
        )

        self.assertEqual(verifier.verify_skills([skill, metadata]), [])

    def test_repository_skill_mismatches_and_scaffolding_fail(self) -> None:
        skill = self.write(
            ".agent/skills/example-skill/SKILL.md",
            "---\n"
            "name: wrong-name\n"
            "description: Bounded capability without a trigger phrase.\n"
            "extra: unsupported\n"
            "---\n\n"
            "# TODO placeholder instruction\n",
        )
        metadata = self.write(
            ".agent/skills/example-skill/agents/openai.yaml",
            "interface:\n"
            "  display_name: Example Skill\n"
            '  short_description: "Too short"\n'
            '  default_prompt: "Use the skill for this task."\n',
        )

        errors = verifier.verify_skills([skill, metadata])

        self.assertTrue(any("does not match directory" in error for error in errors))
        self.assertTrue(any("unsupported skill metadata" in error for error in errors))
        self.assertTrue(any("when the skill should be used" in error for error in errors))
        self.assertTrue(any("unresolved scaffold" in error for error in errors))
        self.assertTrue(any("strings must be quoted" in error for error in errors))
        self.assertTrue(any("25 to 64 characters" in error for error in errors))
        self.assertTrue(any("default_prompt must name" in error for error in errors))

    def test_repository_skill_agent_metadata_rejects_invalid_yaml_escapes(self) -> None:
        metadata = self.write(
            ".agent/skills/example-skill/agents/openai.yaml",
            "interface:\n"
            '  display_name: "Bad \\q"\n'
            '  short_description: "Perform a bounded repository task"\n'
            '  default_prompt: "Use $example-skill for this task."\n',
        )

        errors = verifier._verify_skill_agent_metadata(metadata, "example-skill")

        self.assertTrue(any("strings must be quoted and valid" in error for error in errors))

    def test_repository_skill_requires_instruction_and_agent_files(self) -> None:
        orphan = self.write(
            ".agent/skills/orphan/notes.md", "Repository-owned skill notes.\n"
        )
        missing_agent = self.write(
            ".agent/skills/missing-agent/SKILL.md",
            "---\n"
            "name: missing-agent\n"
            "description: Do a bounded thing. Use when requested.\n"
            "---\n\n"
            "# Missing agent metadata\n",
        )

        errors = verifier.verify_skills([orphan, missing_agent])

        self.assertTrue(any("missing SKILL.md" in error for error in errors))
        self.assertTrue(any("missing agents/openai.yaml" in error for error in errors))

    def test_repository_skill_frontmatter_rejects_non_string_yaml_values(self) -> None:
        collection = self.write(
            ".agent/skills/collection/SKILL.md",
            "---\n"
            "name: collection\n"
            "description: [Do a bounded thing. Use when requested.]\n"
            "---\n\n"
            "# Collection\n",
        )
        boolean = self.write(
            ".agent/skills/boolean/SKILL.md",
            "---\n"
            "name: boolean\n"
            "description: true # Use when requested.\n"
            "---\n\n"
            "# Boolean\n",
        )

        errors = verifier.verify_skills([collection, boolean])

        self.assertEqual(
            sum("values must be one-line strings" in error for error in errors), 2
        )

    def test_jarvis_dependency_spellings_fail_on_executable_surfaces(self) -> None:
        manifest = self.write(
            "requirements.txt", "jarvis" + "_ai_agent==1\n"
        )
        workflow = self.write(
            ".github/workflows/check.yml",
            "run: pip install jarvis" + ".ai.agent\n",
        )

        errors, _ = verifier.verify_text([manifest, workflow])

        self.assertEqual(
            sum(
                f"forbidden dependency on {verifier.JARVIS_DEPENDENCY_LABEL}" in error
                for error in errors
            ),
            2,
        )

    def test_workflow_actions_require_commit_pins_and_fail_fast(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            "  - uses: actions/checkout@v6\n"
            "  - uses: actions/setup-python@" + "a" * 40 + "\n"
            "    continue-on-error: true\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertTrue(any("not pinned" in error for error in errors))
        self.assertTrue(any("continue-on-error" in error for error in errors))
        self.assertEqual(sum("not pinned" in error for error in errors), 1)

    def test_workflow_action_keys_cannot_bypass_pins(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            "  - uses : actions/checkout@v6\n"
            "  - 'uses' : 'actions/setup-python@v6'\n"
            "  - { name: flow, \"uses\" : actions/setup-node@v6 }\n"
            "  - run: echo 'uses: actions/cache@v4'\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertEqual(sum("not pinned" in error for error in errors), 3)

    def test_action_values_reject_trailing_plain_or_quoted_content(self) -> None:
        pinned = "a" * 40
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            f"  - uses: actions/checkout@{pinned} trailing\n"
            f"  - uses: 'actions/setup-python@{pinned}' trailing\n"
            f'  - uses: "actions/setup-node@{pinned}" trailing\n',
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertEqual(sum("not pinned" in error for error in errors), 3)

    def test_policy_yaml_semantic_features_fail_closed(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            '  - "u\\u0073es": actions/checkout@v6\n'
            "  - &mutable { uses: actions/setup-python@v6 }\n"
            "  - *mutable\n"
            "  - ? uses\n"
            "    : actions/setup-node@v6\n"
            "  - <<: *mutable\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertTrue(any("escaped double-quoted mapping key" in error for error in errors))
        self.assertTrue(any("anchor or alias" in error for error in errors))
        self.assertTrue(any("explicit mapping key" in error for error in errors))
        self.assertTrue(any("mapping merge key" in error for error in errors))

    def test_nested_flow_mappings_fail_closed(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps: [{ uses: actions/checkout@v6 }]\n"
            "jobs: { test: { runs-on: ubuntu-latest, "
            "steps: [{ continue-on-error: true }] } }\n"
            "implicit-uses: [uses: actions/checkout@v6]\n"
            "implicit-continue: [continue-on-error: true]\n"
            "split-implicit-uses: [uses:\n"
            "  actions/checkout@v6]\n"
            'quoted-split-implicit-uses: ["uses":\n'
            "  actions/setup-python@v6]\n"
            'quoted-implicit-double: ["uses":actions/setup-node@v6]\n'
            "quoted-implicit-single: ['uses':actions/setup-go@v6]\n"
            "branches: [main]\n"
            "tags: ['release', \"stable\"]\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertEqual(sum("flow mapping" in error for error in errors), 6)
        self.assertEqual(sum("multiline flow collection" in error for error in errors), 2)

    def test_plain_scalar_program_text_is_not_yaml_structure(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            "  - run: echo *files\n"
            '  - run: echo "foo\\n": bar\n',
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertEqual(errors, [])

    def test_policy_yaml_ignores_block_scalar_program_text(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            "  - run: |\n"
            "      echo unsupported >&2\n"
            "      printf '*alias <<: ? uses\\n'\n"
            "  - uses: actions/checkout@" + "a" * 40 + "\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertEqual(errors, [])

    def test_local_composite_actions_require_transitive_commit_pins(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n  - uses: ./.github/actions/outer\n",
        )
        outer = self.write(
            ".github/actions/outer/action.yml",
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            "    - uses: ./.github/actions/inner\n",
        )
        inner = self.write(
            ".github/actions/inner/action.yaml",
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            "    - uses: actions/checkout@v6\n",
        )

        errors, _ = verifier.verify_text([workflow, outer, inner])

        self.assertEqual(sum("not pinned" in error for error in errors), 1)
        self.assertFalse(any("local action reference" in error for error in errors))

    def test_local_action_reference_must_resolve_inside_checkout(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            "  - uses: ./missing\n"
            "  - uses: ./../outside\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertTrue(any("has no action.yml" in error for error in errors))
        self.assertTrue(any("canonical repository-relative path" in error for error in errors))

    def test_remote_docker_action_image_requires_digest(self) -> None:
        mutable = self.write(
            ".github/actions/mutable/action.yml",
            "runs:\n  using: docker\n  image: docker://alpine:latest\n",
        )
        folded = self.write(
            ".github/actions/folded/action.yml",
            "runs:\n"
            "  using: docker\n"
            "  image: >-\n"
            "    docker://alpine:latest\n",
        )
        literal = self.write(
            ".github/actions/literal/action.yml",
            "runs:\n"
            "  using: docker\n"
            "  image: |\n"
            "    docker://alpine:latest\n",
        )
        escaped = self.write(
            ".github/actions/escaped/action.yml",
            "runs:\n"
            "  using: docker\n"
            '  image: "docker\\u003a//alpine:latest"\n',
        )
        empty = self.write(
            ".github/actions/empty/action.yml",
            "runs:\n  using: docker\n  image:\n",
        )
        multiline = self.write(
            ".github/actions/multiline/action.yml",
            "runs:\n"
            "  using: docker\n"
            "  image:\n"
            "    docker://alpine:latest\n",
        )
        multiline_digest = self.write(
            ".github/actions/multiline-digest/action.yml",
            "runs:\n"
            "  using: docker\n"
            "  image:\n"
            "    docker://alpine@sha256:" + "c" * 64 + "\n",
        )
        pinned = self.write(
            ".github/actions/pinned/action.yaml",
            "runs:\n"
            "  using: docker\n"
            "  image: docker://alpine@sha256:" + "a" * 64 + "\n",
        )
        quoted_pinned = self.write(
            ".github/actions/quoted-pinned/action.yaml",
            "runs:\n"
            "  using: docker\n"
            '  image: "docker://alpine@sha256:' + "b" * 64 + '"\n',
        )
        trailing_quoted = self.write(
            ".github/actions/trailing-quoted/action.yaml",
            "runs:\n"
            "  using: docker\n"
            "  image: 'docker://alpine@sha256:" + "d" * 64 + "' trailing\n",
        )

        errors, _ = verifier.verify_text(
            [
                mutable,
                folded,
                literal,
                escaped,
                empty,
                multiline,
                multiline_digest,
                pinned,
                quoted_pinned,
                trailing_quoted,
            ]
        )

        self.assertEqual(sum("remote Docker action image" in error for error in errors), 8)

    def test_continue_on_error_accepts_only_plain_false(self) -> None:
        workflow = self.write(
            ".github/workflows/check.yml",
            "steps:\n"
            "  - run: one\n"
            "    continue-on-error: false # explicitly fail fast\n"
            "  - run: two\n"
            "    continue-on-error: ${{ true }}\n"
            "  - { run: three, 'continue-on-error' : true }\n"
            "  - run: four\n"
            "    \"continue-on-error\" : \"false\"\n",
        )

        errors, _ = verifier.verify_text([workflow])

        self.assertEqual(sum("continue-on-error" in error for error in errors), 3)

    def test_invalid_utf8_markdown_fails_closed(self) -> None:
        path = self.root / "README.md"
        path.write_bytes(b"\xff\xfe")

        errors, _ = verifier.verify_text([path])

        self.assertTrue(any("must be UTF-8" in error for error in errors))

    def test_invalid_utf8_policy_yaml_fails_closed(self) -> None:
        workflow = self.root / ".github/workflows/check.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_bytes(b"\xff\xfe")
        action = self.root / ".github/actions/example/action.yaml"
        action.parent.mkdir(parents=True)
        action.write_bytes(b"runs:\x00")

        errors, _ = verifier.verify_text([workflow, action])

        self.assertEqual(sum("policy YAML must be UTF-8" in error for error in errors), 2)

    def test_invalid_utf8_executable_and_dependency_files_fail_closed(self) -> None:
        executable = self.root / "check.sh"
        executable.write_bytes(b"\xff<<<<<<< HEAD\n")
        dependency = self.root / "requirements.txt"
        dependency.write_bytes(b"dependency\x00jarvis" + b"_ai_agent\n")
        npmrc = self.root / ".npmrc"
        token = b"gh" + b"p_" + b"a" * 36
        npmrc.write_bytes(b"//registry/:_authToken=" + token + b"\xff\n")
        extensionless = self.root / "tool"
        extensionless.write_bytes(b"token=" + token + b"\xff\n")
        extensionless.chmod(0o755)

        errors, _ = verifier.verify_text(
            [executable, dependency, npmrc, extensionless]
        )

        self.assertEqual(
            sum("repository text must be UTF-8" in error for error in errors), 4
        )
        self.assertEqual(sum("possible GitHub token" in error for error in errors), 2)

    def test_machine_path_without_trailing_slash_is_detected(self) -> None:
        mac_path = "/" + "Users" + "/developer"
        source = self.write("script.py", f'PATH = "{mac_path}"\n')

        errors, _ = verifier.verify_text([source])

        self.assertTrue(any("machine-specific macOS user path" in error for error in errors))

    def test_unresolved_conflict_markers_fail_without_setext_false_positive(self) -> None:
        conflicted = self.write(
            "conflicted.txt",
            "<<<<<<< HEAD\nours\n||||||| base\nbase\n=======\ntheirs\n>>>>>>> topic\n",
        )
        markdown = self.write("README.md", "Heading\n=======\n")

        errors, _ = verifier.verify_text([conflicted, markdown])

        self.assertEqual(
            sum("unresolved merge conflict marker" in error for error in errors), 4
        )


if __name__ == "__main__":
    unittest.main()
