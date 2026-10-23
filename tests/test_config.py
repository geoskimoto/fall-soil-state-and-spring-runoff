"""config: data dir from FALLSOIL_DATA_DIR (env, then .env); clear errors."""
import os
import stat

import pytest

from fallsoil import config
from fallsoil.errors import ConfigError, FallsoilError


def test_resolves_data_dir_from_env(tmp_path, monkeypatch):
    target = tmp_path / "d"
    target.mkdir()
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(target))
    assert config.get_data_dir() == target


def test_env_is_read_at_call_time_not_import_time(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(a))
    assert config.get_data_dir() == a
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(b))
    assert config.get_data_dir() == b


def test_missing_directory_is_created_when_parent_writable(tmp_path, monkeypatch):
    target = tmp_path / "new" / "deeper"
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(target))
    assert config.get_data_dir() == target
    assert target.is_dir()


def test_unset_raises_clear_error_naming_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("FALLSOIL_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    with pytest.raises(ConfigError) as ei:
        config.get_data_dir()
    assert "FALLSOIL_DATA_DIR" in str(ei.value)
    assert isinstance(ei.value, FallsoilError)


def test_empty_string_counts_as_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("FALLSOIL_DATA_DIR", "  ")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        config.get_data_dir()


def test_path_that_is_a_file_raises(tmp_path, monkeypatch):
    f = tmp_path / "file.txt"
    f.write_text("x")
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(f))
    with pytest.raises(ConfigError) as ei:
        config.get_data_dir()
    assert str(f) in str(ei.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_unwritable_directory_raises_and_names_path(tmp_path, monkeypatch):
    target = tmp_path / "ro"
    target.mkdir()
    target.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        monkeypatch.setenv("FALLSOIL_DATA_DIR", str(target))
        with pytest.raises(ConfigError) as ei:
            config.get_data_dir()
        assert str(target) in str(ei.value)
    finally:
        target.chmod(stat.S_IRWXU)


def test_dotenv_file_in_cwd_is_used_when_env_unset(tmp_path, monkeypatch):
    target = tmp_path / "from_dotenv"
    (tmp_path / ".env").write_text(f"FALLSOIL_DATA_DIR={target}\n")
    monkeypatch.delenv("FALLSOIL_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.get_data_dir() == target


def test_real_env_wins_over_dotenv(tmp_path, monkeypatch):
    real, other = tmp_path / "real", tmp_path / "other"
    (tmp_path / ".env").write_text(f"FALLSOIL_DATA_DIR={other}\n")
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(real))
    monkeypatch.chdir(tmp_path)
    assert config.get_data_dir() == real


def test_standard_subdirectories(data_dir):
    assert config.raw_dir("usgs") == data_dir / "raw" / "usgs"
    assert config.interim_dir("usgs") == data_dir / "interim" / "usgs"
    assert config.raw_dir("usgs").is_dir()
    assert config.interim_dir("usgs").is_dir()
    assert config.manifest_path() == data_dir / "manifest.jsonl"


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "."])
def test_subdirectory_names_cannot_escape_data_dir(data_dir, bad):
    with pytest.raises(ValueError):
        config.raw_dir(bad)
