#!/usr/bin/env python3
"""Inspect the built Python wheel and sdist without extracting them."""

from __future__ import annotations

import argparse
from collections import Counter
from email import policy
from email.parser import BytesParser
import re
import stat
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath


ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "simulation" / "pyproject.toml"
FORBIDDEN_GENERATED_PARTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
WHEEL_FORBIDDEN_PARTS = {
    *FORBIDDEN_GENERATED_PARTS,
    "nxt_range_agent",
    "nxt_range_demo",
    "nxt_range_viewer",
    "reports",
    "tests",
}
FORBIDDEN_SUFFIXES = (".pyc", ".pyo")
WHEEL_FILENAME_RE = re.compile(
    r"^(?P<project>[^-]+)-(?P<version>[^-]+)"
    r"(?:-(?P<build>[0-9][^-]*))?"
    r"-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
)


def pyproject_data() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def declared_project_identity() -> tuple[str, str]:
    data = pyproject_data()
    project = data["project"]
    if not isinstance(project, dict):
        raise ValueError("simulation/pyproject.toml [project] must be a table")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not name:
        raise ValueError("simulation/pyproject.toml project.name must be a string")
    if not isinstance(version, str) or not version:
        raise ValueError("simulation/pyproject.toml project.version must be a string")
    return name, version


def declared_packages() -> list[str]:
    data = pyproject_data()
    return list(data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])


def normalized_distribution_name(name: str) -> str:
    """Return the wheel/sdist filename spelling for a project name."""
    return re.sub(r"[-_.]+", "_", name).lower()


def wheel_filename_identity(filename: str) -> tuple[str, str] | None:
    match = WHEEL_FILENAME_RE.fullmatch(filename)
    if match is None:
        return None
    return match.group("project"), match.group("version")


def metadata_header_values(metadata: bytes, header: str) -> list[str]:
    message = BytesParser(policy=policy.default).parsebytes(metadata, headersonly=True)
    return [str(value) for value in message.get_all(header, [])]


def member_parts(member: str) -> tuple[str, ...]:
    return PurePosixPath(member).parts


def unsafe_member(member: str) -> bool:
    if not member or "\x00" in member or any(character in member for character in "\r\n"):
        return True
    if "\\" in member or PureWindowsPath(member).drive:
        return True
    path = PurePosixPath(member)
    if path.is_absolute():
        return True
    normalized_member = member[:-1] if member.endswith("/") else member
    components = normalized_member.split("/")
    return not components or any(component in {"", ".", ".."} for component in components)


def duplicate_members(members: list[str]) -> list[str]:
    return sorted(member for member, count in Counter(members).items() if count > 1)


def unsafe_zip_type(member: zipfile.ZipInfo) -> bool:
    mode = (member.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        return True
    if member.is_dir():
        return file_type not in {0, stat.S_IFDIR}
    return file_type == stat.S_IFDIR


def forbidden_wheel_member(member: str) -> bool:
    parts = set(member_parts(member))
    return bool(parts & WHEEL_FORBIDDEN_PARTS) or member.endswith(FORBIDDEN_SUFFIXES)


def forbidden_sdist_member(member: str) -> bool:
    parts = member_parts(member)
    if set(parts) & FORBIDDEN_GENERATED_PARTS:
        return True
    if member.endswith(FORBIDDEN_SUFFIXES):
        return True
    if "reports" in parts and not member.endswith("/reports/.gitkeep"):
        return True
    return False


def validate(build_dir: Path) -> list[str]:
    errors: list[str] = []
    wheels = sorted(build_dir.glob("*.whl"))
    sdists = sorted(build_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        errors.append(f"expected one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        errors.append(f"expected one sdist, found {len(sdists)}")
    if errors:
        return errors

    project_name, project_version = declared_project_identity()
    archive_name = normalized_distribution_name(project_name)
    expected_archive_root = f"{archive_name}-{project_version}"
    packages = declared_packages()
    wheel_identity = wheel_filename_identity(wheels[0].name)
    if wheel_identity is None:
        errors.append(f"wheel filename is malformed: {wheels[0].name}")
    else:
        wheel_project, wheel_version = wheel_identity
        if wheel_project != archive_name:
            errors.append(
                f"wheel filename project must be {archive_name}: {wheels[0].name}"
            )
        if wheel_version != project_version:
            errors.append(
                f"wheel filename version must be {project_version}: {wheels[0].name}"
            )

    expected_dist_info_root = f"{expected_archive_root}.dist-info"
    wheel_metadata: bytes | None = None
    with zipfile.ZipFile(wheels[0]) as archive:
        zip_members = archive.infolist()
        members = [member.filename for member in zip_members]
        metadata_members = [
            member
            for member in zip_members
            if member.filename == f"{expected_dist_info_root}/METADATA"
        ]
        if len(metadata_members) == 1 and not unsafe_zip_type(metadata_members[0]):
            wheel_metadata = archive.read(metadata_members[0])
    for member in duplicate_members(members):
        errors.append(f"wheel contains duplicate member: {member}")
    unsafe = [member for member in members if unsafe_member(member)]
    errors.extend(f"wheel contains unsafe member path: {member}" for member in unsafe)
    for member in zip_members:
        if unsafe_zip_type(member):
            errors.append(f"wheel contains symlink or special member: {member.filename}")
    for package in packages:
        if not any(member.startswith(f"{package}/") for member in members):
            errors.append(f"wheel is missing declared package: {package}")
    dist_info_roots = {
        member_parts(member)[0]
        for member in members
        if member_parts(member) and member_parts(member)[0].endswith(".dist-info")
    }
    if len(dist_info_roots) != 1:
        errors.append(f"wheel must contain one dist-info root, found {len(dist_info_roots)}")
    elif dist_info_roots != {expected_dist_info_root}:
        errors.append(
            f"wheel dist-info root must be {expected_dist_info_root}: "
            + ", ".join(sorted(dist_info_roots))
        )
    if wheel_metadata is None:
        errors.append(f"wheel is missing {expected_dist_info_root}/METADATA")
    else:
        metadata_names = metadata_header_values(wheel_metadata, "Name")
        metadata_versions = metadata_header_values(wheel_metadata, "Version")
        if metadata_names != [project_name]:
            errors.append(
                f"wheel METADATA Name must be {project_name!r}, found {metadata_names!r}"
            )
        if metadata_versions != [project_version]:
            errors.append(
                "wheel METADATA Version must be "
                f"{project_version!r}, found {metadata_versions!r}"
            )
    top_level = {member_parts(member)[0] for member in members if member_parts(member)}
    unexpected_roots = top_level - set(packages) - dist_info_roots
    if unexpected_roots:
        errors.append(
            "wheel contains unexpected top-level content: "
            + ", ".join(sorted(unexpected_roots))
        )
    for member in members:
        if forbidden_wheel_member(member):
            errors.append(f"wheel contains forbidden generated/test content: {member}")

    expected_sdist_filename = f"{expected_archive_root}.tar.gz"
    if sdists[0].name != expected_sdist_filename:
        errors.append(
            f"sdist filename must be {expected_sdist_filename}: {sdists[0].name}"
        )

    sdist_pyproject: bytes | None = None
    with tarfile.open(sdists[0], "r:gz") as archive:
        tar_members = archive.getmembers()
        sdist_members = [member.name for member in tar_members]
        pyproject_members = [
            member
            for member in tar_members
            if member.name == f"{expected_archive_root}/pyproject.toml"
        ]
        if len(pyproject_members) == 1 and pyproject_members[0].isfile():
            extracted = archive.extractfile(pyproject_members[0])
            if extracted is not None:
                sdist_pyproject = extracted.read()
    for member in duplicate_members(sdist_members):
        errors.append(f"sdist contains duplicate member: {member}")
    unsafe = [member for member in sdist_members if unsafe_member(member)]
    errors.extend(f"sdist contains unsafe member path: {member}" for member in unsafe)
    for member in tar_members:
        if not (member.isfile() or member.isdir()):
            errors.append(f"sdist contains non-file member: {member.name}")
    outside_root = [
        member
        for member in sdist_members
        if not member_parts(member)
        or member_parts(member)[0] != expected_archive_root
    ]
    errors.extend(
        "sdist member is outside expected top-level root "
        f"{expected_archive_root}: {member}"
        for member in outside_root
    )
    if sdist_pyproject is None:
        errors.append("sdist is missing pyproject.toml")
    else:
        try:
            embedded = tomllib.loads(sdist_pyproject.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"sdist pyproject.toml is invalid UTF-8 TOML: {exc}")
        else:
            embedded_project = embedded.get("project")
            embedded_name = (
                embedded_project.get("name")
                if isinstance(embedded_project, dict)
                else None
            )
            embedded_version = (
                embedded_project.get("version")
                if isinstance(embedded_project, dict)
                else None
            )
            if embedded_name != project_name:
                errors.append(
                    "sdist pyproject project.name must be "
                    f"{project_name!r}, found {embedded_name!r}"
                )
            if embedded_version != project_version:
                errors.append(
                    "sdist pyproject project.version must be "
                    f"{project_version!r}, found {embedded_version!r}"
                )
    if f"{expected_archive_root}/README.md" not in sdist_members:
        errors.append("sdist is missing README.md")
    for package in packages:
        package_root = f"{expected_archive_root}/{package}/"
        if not any(member.startswith(package_root) for member in sdist_members):
            errors.append(f"sdist is missing declared package: {package}")
    for member in sdist_members:
        if forbidden_sdist_member(member):
            errors.append(f"sdist contains forbidden generated content: {member}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("build_dir", type=Path)
    args = parser.parse_args()
    errors = validate(args.build_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "Python distribution inspection passed for: "
        + ", ".join(declared_packages())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
