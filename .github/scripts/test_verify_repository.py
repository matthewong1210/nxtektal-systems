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
        ]

        errors = verifier.verify_paths(paths)

        self.assertEqual(len(errors), 3)

    def test_reports_check_uses_repository_relative_parts(self) -> None:
        readme = self.write("README.md")

        errors = verifier.verify_paths([readme])

        self.assertFalse(any("generated report" in error for error in errors))

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
            ]
        )

        self.assertEqual(sum("remote Docker action image" in error for error in errors), 7)

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

    def test_machine_path_without_trailing_slash_is_detected(self) -> None:
        mac_path = "/" + "Users" + "/developer"
        source = self.write("script.py", f'PATH = "{mac_path}"\n')

        errors, _ = verifier.verify_text([source])

        self.assertTrue(any("machine-specific macOS user path" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
