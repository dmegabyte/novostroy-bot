from __future__ import annotations

from pathlib import Path

import pytest

from scripts import nmbot_local_publish as publish


def test_allowed_bootstrap_out_dir_accepts_project_root_and_rejects_traversal(tmp_path: Path) -> None:
    allowed = tmp_path / "release_bundles" / "bootstrap" / "candidate"

    assert publish.allowed_bootstrap_out_dir(allowed, project_root=tmp_path) == allowed.resolve(strict=False)

    with pytest.raises(publish.ReleaseError, match="parent traversal"):
        publish.allowed_bootstrap_out_dir(tmp_path / "release_bundles" / "bootstrap" / ".." / "outside", project_root=tmp_path)


def test_allowed_bootstrap_out_dir_rejects_symlink_component(tmp_path: Path) -> None:
    root = tmp_path / "release_bundles" / "bootstrap"
    root.mkdir(parents=True)
    link = root / "linked"
    link.symlink_to(tmp_path / "outside")

    with pytest.raises(publish.ReleaseError, match="symlink component"):
        publish.allowed_bootstrap_out_dir(link / "candidate", project_root=tmp_path)


def test_rename_noreplace_publishes_once_and_preserves_existing_directory(tmp_path: Path) -> None:
    source = tmp_path / "staging"
    source.mkdir()
    (source / "manifest.json").write_text("{}\n", encoding="utf-8")
    destination = tmp_path / "published"

    publish.rename_noreplace(source, destination)

    assert not source.exists()
    assert (destination / "manifest.json").read_text(encoding="utf-8") == "{}\n"

    next_source = tmp_path / "next-staging"
    next_source.mkdir()
    with pytest.raises(publish.ReleaseError, match="overwrite"):
        publish.rename_noreplace(next_source, destination)
    assert next_source.exists()


def test_cleanup_private_staging_refuses_paths_outside_output_root(tmp_path: Path) -> None:
    outside = tmp_path / ".nmbot-capture-staging-outside"
    outside.mkdir()
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(publish.ReleaseError, match="outside output parent"):
        publish.cleanup_private_staging(outside, output)
    assert outside.exists()
