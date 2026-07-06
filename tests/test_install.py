from __future__ import annotations

from pathlib import Path

import pytest

import install

ENTRY_SCRIPT_NAME = "Download from URL (yt-dlp).py"


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal fake checkout: scripts/<entry script> + resolve_ytdlp/."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / ENTRY_SCRIPT_NAME).write_text("# entry script\n", encoding="utf-8")

    package = repo / "resolve_ytdlp"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text("# config\n", encoding="utf-8")

    monkeypatch.setattr(install, "_repo_root", lambda: repo)
    return repo


# -- resolve_target_dir / resolve_modules_dir --------------------------------


def test_resolve_target_dir_macos(tmp_home: Path, set_platform) -> None:
    set_platform("darwin")

    target = install.resolve_target_dir()

    assert target == (
        tmp_home
        / "Library"
        / "Application Support"
        / "Blackmagic Design"
        / "DaVinci Resolve"
        / "Fusion"
        / "Scripts"
        / "Utility"
    )


def test_resolve_target_dir_linux_uses_primary_when_present(
    tmp_home: Path, set_platform
) -> None:
    set_platform("linux")
    (tmp_home / ".local" / "share" / "DaVinciResolve").mkdir(parents=True)

    target = install.resolve_target_dir()

    expected = tmp_home / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts" / "Utility"
    assert target == expected


def test_resolve_target_dir_linux_falls_back_when_primary_missing(
    tmp_home: Path, set_platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_platform("linux")
    fallback_root = tmp_path / "opt-resolve"
    monkeypatch.setattr(install, "_linux_system_root", lambda: fallback_root)

    target = install.resolve_target_dir()

    assert target == fallback_root / "Fusion" / "Scripts" / "Utility"


def test_resolve_modules_dir_is_sibling_of_target_dir(tmp_path: Path) -> None:
    target = tmp_path / "Fusion" / "Scripts" / "Utility"

    modules = install.resolve_modules_dir(target)

    assert modules == tmp_path / "Fusion" / "Scripts" / "Modules"


def test_resolve_modules_dir_defaults_from_resolve_target_dir(
    tmp_home: Path, set_platform
) -> None:
    set_platform("linux")
    (tmp_home / ".local" / "share" / "DaVinciResolve").mkdir(parents=True)

    modules = install.resolve_modules_dir()

    assert modules == install.resolve_target_dir().parent / "Modules"


# -- default_mode --------------------------------------------------------


def test_default_mode_symlink_inside_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(install, "_repo_root", lambda: repo)

    assert install.default_mode() == "symlink"


def test_default_mode_copy_outside_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(install, "_repo_root", lambda: repo)

    assert install.default_mode() == "copy"


# -- install: symlink mode -----------------------------------------------


def test_install_symlink_creates_symlinks(fake_repo: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    result = install.install("symlink", target_dir=target_dir, modules_dir=modules_dir)

    assert result == target_dir
    dest_script = target_dir / ENTRY_SCRIPT_NAME
    dest_package = modules_dir / "resolve_ytdlp"
    assert dest_script.is_symlink()
    assert dest_script.resolve() == (fake_repo / "scripts" / ENTRY_SCRIPT_NAME).resolve()
    assert dest_package.is_symlink()
    assert dest_package.resolve() == (fake_repo / "resolve_ytdlp").resolve()


def test_install_symlink_edits_to_source_are_picked_up_live(
    fake_repo: Path, tmp_path: Path
) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"
    install.install("symlink", target_dir=target_dir, modules_dir=modules_dir)

    (fake_repo / "resolve_ytdlp" / "config.py").write_text("# edited\n", encoding="utf-8")

    dest_config = modules_dir / "resolve_ytdlp" / "config.py"
    assert dest_config.read_text(encoding="utf-8") == "# edited\n"


# -- install: copy mode ---------------------------------------------------


def test_install_copy_creates_standalone_files(fake_repo: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)

    dest_script = target_dir / ENTRY_SCRIPT_NAME
    dest_package = modules_dir / "resolve_ytdlp"
    assert dest_script.is_file()
    assert not dest_script.is_symlink()
    assert dest_package.is_dir()
    assert not dest_package.is_symlink()
    assert (dest_package / "config.py").is_file()


def test_install_copy_is_independent_of_source(fake_repo: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"
    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)

    (fake_repo / "resolve_ytdlp" / "config.py").write_text("# edited\n", encoding="utf-8")

    dest_config = modules_dir / "resolve_ytdlp" / "config.py"
    assert dest_config.read_text(encoding="utf-8") == "# config\n"


def test_install_copy_does_not_place_package_in_target_dir(
    fake_repo: Path, tmp_path: Path
) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)

    assert not (target_dir / "resolve_ytdlp").exists()


# -- install: shared behavior ---------------------------------------------


def test_install_creates_target_and_modules_dir_if_missing(fake_repo: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "deeply" / "nested" / "Utility"
    modules_dir = tmp_path / "deeply" / "nested" / "Modules"
    assert not target_dir.exists()
    assert not modules_dir.exists()

    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)

    assert target_dir.is_dir()
    assert modules_dir.is_dir()


def test_install_defaults_to_resolve_target_dir_and_modules_dir(
    fake_repo: Path, tmp_home: Path, set_platform
) -> None:
    set_platform("linux")
    (tmp_home / ".local" / "share" / "DaVinciResolve").mkdir(parents=True)

    result = install.install("copy")

    assert result == install.resolve_target_dir()
    assert (result / ENTRY_SCRIPT_NAME).is_file()
    assert (install.resolve_modules_dir() / "resolve_ytdlp" / "config.py").is_file()


def test_install_rejects_unknown_mode(fake_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        install.install("bogus", target_dir=tmp_path / "Utility")


def test_install_symlink_is_idempotent(fake_repo: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    install.install("symlink", target_dir=target_dir, modules_dir=modules_dir)
    install.install("symlink", target_dir=target_dir, modules_dir=modules_dir)

    assert (target_dir / ENTRY_SCRIPT_NAME).is_symlink()


def test_install_copy_is_idempotent(fake_repo: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)
    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)

    dest_package = modules_dir / "resolve_ytdlp"
    assert dest_package.is_dir()
    assert (dest_package / "config.py").is_file()


def test_install_switching_from_copy_to_symlink_replaces_cleanly(
    fake_repo: Path, tmp_path: Path
) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    install.install("copy", target_dir=target_dir, modules_dir=modules_dir)
    install.install("symlink", target_dir=target_dir, modules_dir=modules_dir)

    assert (modules_dir / "resolve_ytdlp").is_symlink()


# -- main() CLI -------------------------------------------------------------


def test_main_prints_installed_mode_target_and_modules_dir(
    fake_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target_dir = tmp_path / "Utility"
    modules_dir = tmp_path / "Modules"

    install.main(
        [
            "--mode",
            "copy",
            "--target-dir",
            str(target_dir),
            "--modules-dir",
            str(modules_dir),
        ]
    )

    captured = capsys.readouterr()
    assert "copy" in captured.out
    assert str(target_dir) in captured.out
    assert str(modules_dir) in captured.out
    assert (target_dir / ENTRY_SCRIPT_NAME).is_file()
    assert (modules_dir / "resolve_ytdlp").is_dir()
