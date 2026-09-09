"""bot-coms-messaging CLI — dashboard plugin helpers.

Does not rewrite Hermes plugins.enabled or profile config.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def packaged_dashboard() -> Path:
    """Locate dashboard assets after wheel install or from the editable repo tree."""
    try:
        import hermes_bot_coms_messaging

        candidate = Path(hermes_bot_coms_messaging.__file__).resolve().parent / "dashboard"
        if (candidate / "manifest.json").is_file():
            return candidate
    except ImportError:
        pass

    # Editable checkout: src/bot_coms_messaging/cli.py → repo root / adapters / …
    repo_dashboard = (
        Path(__file__).resolve().parents[2]
        / "adapters"
        / "hermes_bot_coms_messaging"
        / "dashboard"
    )
    if (repo_dashboard / "manifest.json").is_file():
        return repo_dashboard

    raise FileNotFoundError(
        "Could not find hermes_bot_coms_messaging/dashboard/manifest.json. "
        "Reinstall bot-coms with the messaging extra, or run from the bot-coms checkout."
    )


def cmd_install_dashboard(ns: argparse.Namespace) -> int:
    hermes_root = Path(ns.hermes_root).expanduser().resolve()
    source = packaged_dashboard()
    dest_parent = hermes_root / "plugins" / "bot-coms-messaging"
    dest = dest_parent / "dashboard"

    if dest.exists() or dest.is_symlink():
        if not ns.force:
            sys.stderr.write(
                f"Refusing to replace existing {dest}. Pass --force to overwrite.\n"
            )
            return 1
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    dest_parent.mkdir(parents=True, exist_ok=True)
    if ns.copy:
        shutil.copytree(source, dest)
        sys.stdout.write(f"Copied dashboard plugin to {dest}\n")
    else:
        os.symlink(source, dest, target_is_directory=True)
        sys.stdout.write(f"Symlinked dashboard plugin to {dest} -> {source}\n")

    sys.stdout.write(
        "This package does not edit plugins.enabled. Enable bot-coms and "
        "bot-coms-messaging on the Hermes instance, write plugin-data config, "
        "and restart the Hermes dashboard backend. Execution is backend-owned.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bot-coms-messaging",
        description="Messaging helpers for bot-coms (dashboard install).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser(
        "install-dashboard",
        help="Symlink (or copy) the dashboard plugin into a Hermes root",
    )
    install.add_argument(
        "--hermes-root",
        required=True,
        help="Shared Hermes root containing plugins/ and plugin-data/",
    )
    install.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of creating a symlink",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing plugins/bot-coms-messaging/dashboard path",
    )
    install.set_defaults(func=cmd_install_dashboard)

    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    raise SystemExit(main())
