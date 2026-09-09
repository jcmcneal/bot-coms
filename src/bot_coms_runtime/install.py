"""Install backend plugin assets without editing configuration or services."""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path


def packaged_dashboard() -> Path:
    try:
        module = importlib.import_module('hermes_bot_coms')
        candidate = Path(module.__file__).resolve().parent / 'dashboard'
        if (candidate / 'manifest.json').is_file():
            return candidate
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[2] / 'adapters' / 'hermes_bot_coms' / 'dashboard'
    if not (candidate / 'manifest.json').is_file():
        raise FileNotFoundError('Reinstall bot-coms with its backend dashboard assets')
    return candidate


def install_dashboard(root: Path, *, copy: bool = False, force: bool = False) -> Path:
    source = packaged_dashboard()
    dest = Path(root).expanduser().resolve() / 'plugins' / 'bot-coms' / 'dashboard'
    if dest.is_symlink() and dest.resolve() == source.resolve() and not copy:
        return dest
    if dest.exists() or dest.is_symlink():
        if not force:
            raise FileExistsError(f'Refusing to replace {dest}; pass --force to replace dashboard assets')
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copytree(source, dest)
    else:
        dest.symlink_to(source, target_is_directory=True)
    return dest


def command(ns) -> int:
    try:
        dest = install_dashboard(Path(ns.hermes_root), copy=ns.copy, force=ns.force)
    except (OSError, ValueError) as exc:
        sys.stderr.write(str(exc) + '\n')
        return 1
    print(f'Installed bot-coms backend dashboard at {dest}')
    print('Enable bot-coms on the shared Hermes instance and restart the backend when work can drain. '
          'This command does not edit configuration, launch agents, or install a sidecar.')
    return 0


def register_command(subparsers):
    parser = subparsers.add_parser('install-dashboard', help='Install backend-owned team delivery')
    parser.add_argument('--hermes-root', required=True)
    parser.add_argument('--copy', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.set_defaults(func=command)
