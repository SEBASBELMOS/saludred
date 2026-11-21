"""The secret scanner finds what it should, masks what it finds and ignores placeholders.

The fake secrets are assembled at run time so this file does not contain one itself
(the scanner would flag its own tests).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import escanear_secretos as scanner

GROQ = "gsk_" + "Ab3dEf6hIj9lMn2pQr5tUv8x"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
URL_PASSWORD = "Xk29" + "qLmPzR7w"
LONG_VALUE = "q7Zk2" + "Lm9Xp4Rt8Vw"


def kinds(line: str) -> list[str]:
    return [kind for kind, _ in scanner.scan_line(line)]


def test_provider_keys_jwts_and_private_keys_are_found() -> None:
    assert kinds(f'client = Groq(api_key="{GROQ}")') == ["clave Groq"]
    assert "JWT" in kinds(f"Authorization: Bearer {JWT}")
    assert kinds("-----BEGIN " + "RSA PRIVATE KEY-----") == ["llave privada"]
    assert kinds("aws " + "AKIA" + "ABCDEFGHIJKLMNOP") == ["clave AWS"]


def test_urls_with_a_password_are_found_even_without_digits() -> None:
    assert kinds(f"DATABASE_URL=postgresql://app:{URL_PASSWORD}@db.example.net/saludred") == [
        "URL con contrasena"
    ]
    assert kinds("mongodb+srv://cluster:SuperSecretWord@atlas.example.net/x") == ["URL con contrasena"]


def test_secret_assignments_are_found() -> None:
    assert kinds(f"JWT_SECRET_KEY={LONG_VALUE}") == ["asignacion de secreto"]
    assert kinds(f'  "api_key": "{LONG_VALUE}",') == ["asignacion de secreto"]
    assert kinds(f"GROQ_API_KEY = '{LONG_VALUE}'") == ["asignacion de secreto"]


@pytest.mark.parametrize(
    "line",
    [
        "JWT_SECRET_KEY=unit-test-secret-key-not-a-real-secret",
        "password=settings.seed_default_password",
        "password_hash=hash_password(data.password)",
        "DATABASE_URL=postgresql+psycopg://nobody:nothing@localhost:1/void",
        "postgresql://user:password@localhost/db",
        "DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db/saludred",
        "GROQ_API_KEY=<tu-clave-aqui>",
        "MONGO_PASSWORD=changeme-please-12345",
        "SECRET = os.environ['SECRET_FROM_ENV']",
        "x = 1  # token",
        "SEED_DEFAULT_PASSWORD=Demo2026!",  # shorter than 12 characters
    ],
)
def test_placeholders_and_variable_references_are_ignored(line: str) -> None:
    assert kinds(line) == []


def test_a_finding_never_carries_the_secret() -> None:
    for line, secret in (
        (f'key="{GROQ}"', GROQ),
        (f"DATABASE_URL=postgresql://app:{URL_PASSWORD}@db.example.net/x", URL_PASSWORD),
        (f"JWT_SECRET_KEY={LONG_VALUE}", LONG_VALUE),
    ):
        for _, masked in scanner.scan_line(line):
            assert secret not in masked
            assert masked.startswith(secret[:2]) and f"len {len(secret)}" in masked


def test_very_long_lines_are_skipped() -> None:
    assert kinds("x" * 5000 + GROQ) == []


def test_env_files_are_recognised_but_the_example_is_not() -> None:
    for path in (".env", "api/.env", "pass.env", ".env.bak2026", "prod.env", ".env.local"):
        assert scanner._is_env_file(path), path
    for path in (".env.example", "docs/.env.example", "app/config.py", "environment.md"):
        assert not scanner._is_env_file(path), path


def test_the_gitignore_covers_every_secrets_file() -> None:
    lines = {
        line.strip()
        for line in (Path(__file__).resolve().parents[1] / ".gitignore").read_text().splitlines()
    }
    for pattern in (".env", ".env.*", "pass.env", "*.env", ".env.bak*", "!.env.example"):
        assert pattern in lines, pattern


# -- Against a real repository (needs the git binary) -------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.org", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


@needs_git
def test_a_clean_repository_passes(repo: Path, capsys) -> None:
    (repo / "app.py").write_text("DEBUG = True\nname = 'saludred'\n")
    (repo / ".env.example").write_text("JWT_SECRET_KEY=<generate-one>\n")
    _git(repo, "add", "."), _git(repo, "commit", "-qm", "init")

    assert scanner.main(["--root", str(repo)]) == 0
    assert "Sin hallazgos" in capsys.readouterr().out


@needs_git
def test_a_secret_in_the_tree_is_reported_masked_with_file_and_line(repo: Path, capsys) -> None:
    (repo / "settings.py").write_text(f'x = 1\nGROQ_KEY = "{GROQ}"\n')
    _git(repo, "add", "."), _git(repo, "commit", "-qm", "init")

    assert scanner.main(["--root", str(repo), "--sin-historial"]) == 1
    out = capsys.readouterr().out
    assert "settings.py:2" in out and "clave Groq" in out
    assert GROQ not in out and GROQ[8:] not in out


@needs_git
def test_a_secret_deleted_from_the_tree_is_still_found_in_the_history(repo: Path, capsys) -> None:
    (repo / "settings.py").write_text(f'GROQ_KEY = "{GROQ}"\n')
    _git(repo, "add", "."), _git(repo, "commit", "-qm", "oops")
    (repo / "settings.py").write_text('GROQ_KEY = "from-env"\n')
    _git(repo, "commit", "-qam", "remove it")

    assert scanner.main(["--root", str(repo), "--sin-historial"]) == 0  # the tree is clean
    capsys.readouterr()
    assert scanner.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "HISTORIAL" in out and "settings.py:1" in out and "ROTE" in out
    assert GROQ not in out


@needs_git
def test_a_committed_env_file_is_a_finding_even_after_it_is_removed(repo: Path, capsys) -> None:
    (repo / "pass.env").write_text("anything\n")
    (repo / "keep.txt").write_text("x\n")
    _git(repo, "add", "."), _git(repo, "commit", "-qm", "oops")
    _git(repo, "rm", "-q", "pass.env"), _git(repo, "commit", "-qm", "remove")

    assert scanner.main(["--root", str(repo)]) == 1
    assert "archivo de secretos en el historial" in capsys.readouterr().out


@needs_git
def test_the_same_secret_in_many_commits_is_one_finding(repo: Path, capsys) -> None:
    for number in range(3):
        (repo / "a.py").write_text(f'KEY = "{GROQ}"\n')
        _git(repo, "add", "."), _git(repo, "commit", "-qm", f"add{number}")
        (repo / "a.py").write_text("KEY = None\n")
        _git(repo, "commit", "-qam", f"remove{number}")
    findings = scanner.deduplicate(scanner.scan_history(repo))
    assert len(findings) == 1


def test_outside_a_git_repository_it_fails_cleanly(tmp_path: Path, capsys) -> None:
    assert scanner.main(["--root", str(tmp_path)]) == 2
    assert "ERROR" in capsys.readouterr().out
