import json

import pytest

from bot_coms.cli import main
from bot_coms_runtime.install import install_dashboard


def test_backend_install_preserves_config_and_has_no_service_files(tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text('plugins: {enabled: []}\n')
    assert main(['install-dashboard', '--hermes-root', str(tmp_path)]) == 0
    dest = tmp_path / 'plugins' / 'bot-coms' / 'dashboard'
    assert dest.is_symlink()
    manifest = json.loads((dest / 'manifest.json').read_text())
    assert manifest['name'] == 'bot-coms'
    assert (dest / manifest['api']).is_file()
    assert config.read_text() == 'plugins: {enabled: []}\n'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['config.yaml', 'plugins']
    assert main(['install-dashboard', '--hermes-root', str(tmp_path)]) == 0


def test_copy_install_requires_explicit_replacement(tmp_path):
    dest = install_dashboard(tmp_path, copy=True)
    assert not dest.is_symlink()
    with pytest.raises(FileExistsError):
        install_dashboard(tmp_path, copy=True)
    (dest / 'local.txt').write_text('local change')
    install_dashboard(tmp_path, copy=True, force=True)
    assert not (dest / 'local.txt').exists()
