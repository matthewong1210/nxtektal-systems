"""Regression tests for wheel and source-distribution inspection."""

from __future__ import annotations

import io
import stat
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from verify_python_distribution import (
    declared_packages,
    declared_project_identity,
    normalized_distribution_name,
    validate,
)


class DistributionVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.build_dir = Path(self._temporary.name)
        self.project_name, self.project_version = declared_project_identity()
        self.archive_name = normalized_distribution_name(self.project_name)
        self.archive_root = f"{self.archive_name}-{self.project_version}"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def write_archives(
        self,
        *,
        wheel_filename: str | None = None,
        wheel_dist_info_root: str | None = None,
        wheel_metadata: bytes | None = None,
        sdist_filename: str | None = None,
        sdist_root: str | None = None,
        sdist_pyproject: bytes | None = None,
        extra_wheel_root: str | None = None,
        extra_wheel_members: tuple[tuple[str | zipfile.ZipInfo, bytes], ...] = (),
        extra_sdist_members: tuple[tuple[str | tarfile.TarInfo, bytes], ...] = (),
    ) -> None:
        packages = declared_packages()
        wheel = self.build_dir / (
            wheel_filename or f"{self.archive_root}-py3-none-any.whl"
        )
        wheel_dist_info_root = wheel_dist_info_root or f"{self.archive_root}.dist-info"
        if wheel_metadata is None:
            wheel_metadata = (
                f"Metadata-Version: 2.4\nName: {self.project_name}\n"
                f"Version: {self.project_version}\n"
            ).encode("utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(wheel, "w") as archive:
                for package in packages:
                    archive.writestr(f"{package}/__init__.py", "")
                archive.writestr(
                    f"{wheel_dist_info_root}/METADATA", wheel_metadata
                )
                if extra_wheel_root:
                    archive.writestr(f"{extra_wheel_root}/__init__.py", "")
                for member, payload in extra_wheel_members:
                    archive.writestr(member, payload)

        sdist = self.build_dir / (sdist_filename or f"{self.archive_root}.tar.gz")
        sdist_root = sdist_root or self.archive_root
        if sdist_pyproject is None:
            sdist_pyproject = (
                f'[project]\nname = "{self.project_name}"\n'
                f'version = "{self.project_version}"\n'
            ).encode("utf-8")
        with tarfile.open(sdist, "w:gz") as archive:
            members = {
                f"{sdist_root}/pyproject.toml": sdist_pyproject,
                f"{sdist_root}/README.md": b"content\n",
                f"{sdist_root}/tests/test_contract.py": b"content\n",
                f"{sdist_root}/reports/.gitkeep": b"content\n",
                **{
                    f"{sdist_root}/{package}/__init__.py": b"content\n"
                    for package in packages
                },
            }
            for name, payload in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            for member, payload in extra_sdist_members:
                info = tarfile.TarInfo(member) if isinstance(member, str) else member
                if info.isfile():
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
                else:
                    archive.addfile(info)

    @staticmethod
    def zip_special_member(name: str, file_type: int) -> zipfile.ZipInfo:
        member = zipfile.ZipInfo(name)
        member.create_system = 3
        member.external_attr = (file_type | 0o777) << 16
        return member

    def test_expected_wheel_and_source_contents_pass(self) -> None:
        self.write_archives()

        self.assertEqual(validate(self.build_dir), [])

    def test_forged_wheel_filename_identity_fails(self) -> None:
        self.write_archives(
            wheel_filename="attacker-9.9.9-py3-none-any.whl",
        )

        errors = validate(self.build_dir)

        self.assertTrue(any("wheel filename project" in error for error in errors))
        self.assertTrue(any("wheel filename version" in error for error in errors))

    def test_forged_wheel_dist_info_identity_fails(self) -> None:
        self.write_archives(wheel_dist_info_root="attacker-9.9.9.dist-info")

        errors = validate(self.build_dir)

        self.assertTrue(any("wheel dist-info root" in error for error in errors))

    def test_forged_wheel_metadata_identity_fails(self) -> None:
        self.write_archives(
            wheel_metadata=(
                b"Metadata-Version: 2.4\n"
                b"Name: attacker-project\n"
                b"Version: 9.9.9\n"
            )
        )

        errors = validate(self.build_dir)

        self.assertTrue(any("wheel METADATA Name" in error for error in errors))
        self.assertTrue(any("wheel METADATA Version" in error for error in errors))

    def test_forged_sdist_filename_identity_fails(self) -> None:
        self.write_archives(
            sdist_filename="attacker-9.9.9.tar.gz",
            sdist_root="attacker-9.9.9",
        )

        errors = validate(self.build_dir)

        self.assertTrue(any("sdist filename" in error for error in errors))

    def test_forged_sdist_project_identity_fails(self) -> None:
        self.write_archives(
            sdist_pyproject=(
                b'[project]\nname = "attacker-project"\nversion = "9.9.9"\n'
            ),
        )

        errors = validate(self.build_dir)

        self.assertTrue(any("sdist pyproject project.name" in error for error in errors))
        self.assertTrue(any("sdist pyproject project.version" in error for error in errors))

    def test_unexpected_wheel_package_fails(self) -> None:
        self.write_archives(extra_wheel_root="nxt_range_viewer")

        errors = validate(self.build_dir)

        self.assertTrue(any("unexpected top-level content" in error for error in errors))
        self.assertTrue(any("forbidden generated/test content" in error for error in errors))

    def test_windows_drive_and_traversal_paths_fail(self) -> None:
        self.write_archives(
            extra_wheel_members=(
                ("C:/escape.py", b""),
                ("../escape.py", b""),
                ("nxt_sim//escape.py", b""),
            ),
            extra_sdist_members=(
                ("C:escape.py", b""),
                (f"{self.archive_root}/../escape.py", b""),
            ),
        )

        errors = validate(self.build_dir)

        self.assertTrue(
            any("wheel contains unsafe member path: C:/escape.py" in error for error in errors)
        )
        self.assertTrue(
            any("wheel contains unsafe member path: ../escape.py" in error for error in errors)
        )
        self.assertTrue(
            any(
                "wheel contains unsafe member path: nxt_sim//escape.py" in error
                for error in errors
            )
        )
        self.assertTrue(
            any("sdist contains unsafe member path: C:escape.py" in error for error in errors)
        )
        self.assertTrue(
            any(
                "sdist contains unsafe member path" in error and "../" in error
                for error in errors
            )
        )

    def test_duplicate_archive_members_fail(self) -> None:
        package = declared_packages()[0]
        wheel_member = f"{package}/__init__.py"
        sdist_member = f"{self.archive_root}/{package}/__init__.py"
        self.write_archives(
            extra_wheel_members=((wheel_member, b"duplicate"),),
            extra_sdist_members=((sdist_member, b"duplicate"),),
        )

        errors = validate(self.build_dir)

        self.assertIn(f"wheel contains duplicate member: {wheel_member}", errors)
        self.assertIn(f"sdist contains duplicate member: {sdist_member}", errors)

    def test_zip_symlink_and_special_members_fail(self) -> None:
        self.write_archives(
            extra_wheel_members=(
                (self.zip_special_member("link.py", stat.S_IFLNK), b"target.py"),
                (self.zip_special_member("pipe", stat.S_IFIFO), b""),
            )
        )

        errors = validate(self.build_dir)

        self.assertTrue(
            any("wheel contains symlink or special member: link.py" in error for error in errors)
        )
        self.assertTrue(
            any("wheel contains symlink or special member: pipe" in error for error in errors)
        )

    def test_tar_link_and_special_members_fail(self) -> None:
        link = tarfile.TarInfo(f"{self.archive_root}/link.py")
        link.type = tarfile.SYMTYPE
        link.linkname = "target.py"
        fifo = tarfile.TarInfo(f"{self.archive_root}/pipe")
        fifo.type = tarfile.FIFOTYPE
        self.write_archives(
            extra_sdist_members=((link, b""), (fifo, b"")),
        )

        errors = validate(self.build_dir)

        self.assertTrue(
            any(
                f"sdist contains non-file member: {self.archive_root}/link.py" in error
                for error in errors
            )
        )
        self.assertTrue(
            any(
                f"sdist contains non-file member: {self.archive_root}/pipe" in error
                for error in errors
            )
        )

    def test_sdist_member_outside_expected_root_fails(self) -> None:
        self.write_archives(
            extra_sdist_members=(("another-root/README.md", b"outside\n"),),
        )

        errors = validate(self.build_dir)

        self.assertTrue(
            any("outside expected top-level root" in error for error in errors)
        )


if __name__ == "__main__":
    unittest.main()
