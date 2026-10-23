"""Supplementary config tests: .env parsing details, error messages, source-name validation."""
import os
import stat
from pathlib import Path

import pytest

from fallsoil import config
from fallsoil.errors import ConfigError

VAR = "FALLSOIL_DATA_DIR"


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.delenv(VAR, raising=False)
    return work


def write_env(cwd, text):
    (cwd / ".env").write_text(text)


# ---------------------------------------------------------------- .env parsing
def test_env_file_value_is_used_when_env_unset(cwd, tmp_path):
    target = tmp_path / "from_dotenv"
    write_env(cwd, f"{VAR}={target}\n")
    assert config.get_data_dir() == target and target.is_dir()


def test_comments_blank_lines_and_junk_lines_are_skipped(cwd, tmp_path):
    target = tmp_path / "d1"
    write_env(cwd, f"# a comment\n\n   \nthis line has no equals sign\nOTHER=1\n# {VAR}=/nope\n{VAR}={target}\nLATER=2\n")
    assert config.get_data_dir() == target


@pytest.mark.parametrize("fmt", ['"{p}"', "'{p}'", "{p}"])
def test_quoted_values_are_unquoted(cwd, tmp_path, fmt):
    target = tmp_path / "quoted"
    write_env(cwd, f"{VAR}=" + fmt.format(p=target) + "\n")
    assert config.get_data_dir() == target
    assert '"' not in str(config.get_data_dir()) and "'" not in str(config.get_data_dir())


def test_key_is_matched_exactly_not_by_prefix_or_suffix(cwd, tmp_path):
    target = tmp_path / "right"
    write_env(cwd, f"X{VAR}=/wrong1\n{VAR}X=/wrong2\n{VAR}={target}\n")
    assert config.get_data_dir() == target


def test_value_may_contain_equals_sign(cwd, tmp_path):
    target = tmp_path / "a=b"
    write_env(cwd, f"{VAR}={target}\n")
    assert config.get_data_dir() == target


def test_first_matching_line_wins(cwd, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_env(cwd, f"{VAR}={a}\n{VAR}={b}\n")
    assert config.get_data_dir() == a


def test_real_environment_wins_over_env_file(cwd, tmp_path, monkeypatch):
    real, filed = tmp_path / "real", tmp_path / "filed"
    write_env(cwd, f"{VAR}={filed}\n")
    monkeypatch.setenv(VAR, str(real))
    assert config.get_data_dir() == real
    assert not filed.exists()


def test_env_var_is_read_at_call_time(cwd, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(VAR, str(a))
    assert config.get_data_dir() == a
    monkeypatch.setenv(VAR, str(b))
    assert config.get_data_dir() == b


def test_unset_everywhere_names_the_variable(cwd):
    with pytest.raises(ConfigError) as ei:
        config.get_data_dir()
    assert VAR in str(ei.value)


def test_env_file_without_the_key_is_unset(cwd):
    write_env(cwd, "OTHER=1\n# comment\n")
    with pytest.raises(ConfigError) as ei:
        config.get_data_dir()
    assert VAR in str(ei.value)


@pytest.mark.parametrize("line", [f"{VAR}=", f"{VAR}=   ", f'{VAR}=""'])
def test_blank_value_in_env_file_is_unset(cwd, line):
    write_env(cwd, line + "\n")
    with pytest.raises(ConfigError):
        config.get_data_dir()


def test_blank_real_env_value_is_unset(cwd, monkeypatch):
    monkeypatch.setenv(VAR, "   ")
    with pytest.raises(ConfigError):
        config.get_data_dir()


# ---------------------------------------------------------------- error messages name variable and path
def test_not_a_directory_message_names_variable_and_path(cwd, tmp_path, monkeypatch):
    f = tmp_path / "afile"
    f.write_text("x")
    monkeypatch.setenv(VAR, str(f))
    with pytest.raises(ConfigError) as ei:
        config.get_data_dir()
    msg = str(ei.value)
    assert VAR in msg and str(f) in msg and "not a directory" in msg.lower()


def test_cannot_create_message_names_variable_and_path(cwd, tmp_path, monkeypatch):
    f = tmp_path / "afile"
    f.write_text("x")
    target = f / "sub"  # parent is a regular file
    monkeypatch.setenv(VAR, str(target))
    with pytest.raises(ConfigError) as ei:
        config.get_data_dir()
    msg = str(ei.value)
    assert VAR in msg and str(target) in msg


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_unwritable_directory_message_names_variable_and_path(cwd, tmp_path, monkeypatch):
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        monkeypatch.setenv(VAR, str(d))
        with pytest.raises(ConfigError) as ei:
            config.get_data_dir()
        msg = str(ei.value)
        assert VAR in msg and str(d) in msg and "writable" in msg.lower()
    finally:
        d.chmod(stat.S_IRWXU)


def test_missing_directory_is_created_including_parents(cwd, tmp_path, monkeypatch):
    target = tmp_path / "x" / "y" / "z"
    monkeypatch.setenv(VAR, str(target))
    assert config.get_data_dir() == target and target.is_dir()


# ---------------------------------------------------------------- derived paths
@pytest.mark.parametrize("source", ["usgs", "snotel", "gridmet"])
def test_raw_and_interim_dirs_for_each_source(data_dir, source):
    r, i = config.raw_dir(source), config.interim_dir(source)
    assert r == data_dir / "raw" / source and r.is_dir()
    assert i == data_dir / "interim" / source and i.is_dir()


def test_manifest_path_is_in_data_dir(data_dir):
    assert config.manifest_path() == data_dir / "manifest.jsonl"


@pytest.mark.parametrize("bad", ["", "   ", ".", "..", "a/b", "a\\b", "/abs", "../up", "x/../y"])
def test_invalid_source_names_are_value_errors(data_dir, bad):
    with pytest.raises(ValueError):
        config.raw_dir(bad)
    with pytest.raises(ValueError):
        config.interim_dir(bad)


@pytest.mark.parametrize("bad", [None, 5, b"usgs", ["usgs"]])
def test_non_string_source_is_value_or_type_error(data_dir, bad):
    with pytest.raises((ValueError, TypeError)):
        config.raw_dir(bad)


def test_invalid_source_creates_nothing(data_dir):
    with pytest.raises(ValueError):
        config.raw_dir("a/b")
    assert not (data_dir / "raw").exists() or list((data_dir / "raw").iterdir()) == []


def test_custom_valid_source_name_is_allowed(data_dir):
    assert config.raw_dir("other-src_1").is_dir()
