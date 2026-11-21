"""Simple secret scanner for the repository AND its git history.

    python -m scripts.escanear_secretos                  # tracked files + git log -p
    python -m scripts.escanear_secretos --sin-historial  # only the tracked files
    python -m scripts.escanear_secretos --root /ruta/al/repo

Why the history matters: a secret that reached a commit stays in the history even if
the file is cleaned afterwards. Deleting it is not enough; it has to be ROTATED (new
key at the provider, old one revoked). The scanner only reports; rotating is a human
action.

It NEVER prints a secret. Each finding shows ``file:line``, the type of secret and the
value masked (two characters and the length). Exit code: 0 clean, 1 findings, 2 error.

What it looks for: provider keys (``gsk_``, ``AKIA``), JWTs, private keys, URLs with a
password (``postgresql://user:password@host``), ``.env``-style files that were
committed, and assignments such as ``SOMETHING_SECRET = <12+ characters>``. Obvious
placeholders (``changeme``, ``${VAR}``, ``<tu-clave>``, ``not-a-real``...) and plain
variable references are ignored. The generic rules (assignments, URLs) are not applied
inside test code, whose credentials are synthetic; provider-shaped secrets are reported
everywhere. It is a heuristic, not a guarantee: review what it reports.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MAX_LINE_CHARS = 2_000

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar", ".woff",
    ".woff2", ".ttf", ".pt", ".pkl", ".joblib", ".npy", ".npz", ".onnx", ".dcm", ".svg",
    ".lock", ".min.js", ".map",
}
SKIP_NAMES = {"package-lock.json"}

# Files that exist to hold secrets: committing one is a finding by itself.
_ENV_FILE = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.env|pass\.env)$")
_ENV_SAFE = re.compile(r"(^|/)\.env\.(example|sample|template)$")

_PLACEHOLDER = re.compile(
    r"(?i)(changeme|change-me|change_me|placeholder|example|not-a-real|not-real|unit-test|"
    r"tu-clave|your[-_]|<[^>]*>|\$\{|\$\(|%\(|xxxx|\*\*\*|dummy|fake|sample|secreto-del-servidor)"
)
_VARIABLE_REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\[\]]*$")
_DUMMY_PASSWORDS = {
    "password", "pass", "passwd", "secret", "clave", "contrasena", "nothing", "postgres",
    "user", "usuario", "admin", "root", "test", "changeme", "xxxx", "xxxxxx",
}
_COMMIT_LINE = re.compile(r"^commit ([0-9a-f]{40,64})$")

# (type, regex, group holding the value). Group 0 = the whole match.
PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("clave Groq", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), 0),
    ("clave AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    (
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        0,
    ),
    (
        "llave privada",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
        0,
    ),
    (
        "URL con contrasena",
        re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:/@'\"`]+:([^\s@'\"`/]{4,})@[^\s'\"`]+"),
        1,
    ),
    (
        "asignacion de secreto",
        re.compile(
            r"(?i)\b[A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|API_?KEY|TOKEN|PRIVATE_?KEY)"
            r"[A-Z0-9_]*[\"']?\s*[:=]\s*[\"']?([^\s\"'#,;)(]{12,})"
        ),
        1,
    ),
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    masked: str
    commit: str | None = None  # None: found in the working tree


def mask(value: str) -> str:
    """Two characters and the length: enough to recognise it, useless to use it."""

    return f"{value[:2]}{'*' * 6}(len {len(value)})"


_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures)/|\.(test|spec)\.[a-z]+$")
# Generic heuristics (assignments, URLs) are skipped inside test code, whose credentials
# are synthetic by design. Provider-shaped secrets (keys, JWTs, private keys) are always
# reported, wherever they are.
_GENERIC_KINDS = {"asignacion de secreto", "URL con contrasena"}


def _is_noise(value: str) -> bool:
    if _PLACEHOLDER.search(value):
        return True
    # ``{token}`` / ``{create_access_token(...)}``: an interpolation, not a literal.
    if value.startswith("{"):
        return True
    # A bare identifier chain (``settings.jwt_secret_key``) is code, not a secret.
    if _VARIABLE_REFERENCE.match(value) and not any(ch.isdigit() for ch in value):
        return True
    return False


def scan_line(text: str) -> Iterator[tuple[str, str]]:
    """``(kind, masked value)`` for each secret-looking thing in one line."""

    if len(text) > MAX_LINE_CHARS:
        return
    reported: list[str] = []
    for kind, pattern, group in PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(group)
            if kind == "URL con contrasena":
                # Any real password is a finding, even one without digits: only the
                # obvious stand-ins are ignored.
                if _PLACEHOLDER.search(value) or value.lower() in _DUMMY_PASSWORDS:
                    continue
            elif kind == "asignacion de secreto":
                if _is_noise(value):
                    continue
            elif kind != "llave privada" and _PLACEHOLDER.search(value):
                # A provider-shaped value is real unless it says otherwise.
                continue
            # One secret, one finding: ``api_key="gsk_..."`` matches two patterns.
            if any(value in earlier or earlier in value for earlier in reported):
                continue
            reported.append(value)
            yield kind, mask(value)


def _generic_allowed(path: str, kind: str) -> bool:
    return not (kind in _GENERIC_KINDS and _TEST_PATH.search(path))


def _skipped(path: str) -> bool:
    p = Path(path)
    if p.name in SKIP_NAMES or any(part in SKIP_DIRS for part in p.parts):
        return True
    return any(path.endswith(suffix) for suffix in SKIP_SUFFIXES)


def _is_env_file(path: str) -> bool:
    return bool(_ENV_FILE.search(path)) and not _ENV_SAFE.search(path)


def _git(root: Path, *args: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def scan_tracked(root: Path) -> Iterator[Finding]:
    """The tracked files as they are now."""

    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    )
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", errors="replace")
        if _is_env_file(path):
            yield Finding(path, 0, "archivo de secretos versionado", mask(Path(path).name))
        if _skipped(path):
            continue
        file = root / path
        try:
            if not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
                continue
            content = file.read_bytes()
        except OSError:
            continue
        if b"\0" in content[:2048]:
            continue
        for number, line in enumerate(content.decode("utf-8", errors="replace").splitlines(), 1):
            for kind, masked in scan_line(line):
                if _generic_allowed(path, kind):
                    yield Finding(path, number, kind, masked)


def scan_history(root: Path, max_commits: int | None = None) -> Iterator[Finding]:
    """Every line ever ADDED in any commit of any branch (``git log -p --all``)."""

    args = ["log", "--all", "-p", "-U0", "--no-color", "--no-ext-diff", "--format=commit %H"]
    if max_commits:
        args.append(f"-n{max_commits}")
    process = _git(root, *args)
    assert process.stdout is not None
    commit = ""
    path = ""
    number = 0
    in_header = False  # between ``diff --git`` and the first ``@@`` of a file
    seen_env: set[str] = set()
    for raw in process.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        commit_line = _COMMIT_LINE.match(line)
        if commit_line:
            commit, path, in_header = commit_line.group(1)[:8], "", False
        elif line.startswith("diff --git "):
            path, in_header = "", True
        elif in_header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
            if path and _is_env_file(path) and path not in seen_env:
                seen_env.add(path)
                yield Finding(path, 0, "archivo de secretos en el historial", mask(Path(path).name), commit)
        elif line.startswith("@@"):
            in_header = False
            match = re.search(r"\+(\d+)", line)
            number = int(match.group(1)) - 1 if match else 0
        elif not in_header and line.startswith("+") and path and not _skipped(path):
            number += 1
            for kind, masked in scan_line(line[1:]):
                if _generic_allowed(path, kind):
                    yield Finding(path, number, kind, masked, commit)
    process.wait()
    if process.returncode not in (0, None):
        err = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"git log fallo: {err.strip()[:200]}")


def deduplicate(findings: Iterable[Finding]) -> list[Finding]:
    """One entry per (path, kind, masked value): the same secret in 40 commits is one."""

    seen: set[tuple[str, str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.kind, finding.masked)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path("."), help="raiz del repositorio")
    parser.add_argument("--sin-historial", action="store_true", help="no revisar git log -p")
    parser.add_argument("--max-commits", type=int, default=None, help="limite del historial")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        working = deduplicate(scan_tracked(root))
        history = [] if args.sin_historial else deduplicate(scan_history(root, args.max_commits))
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"ERROR: no se pudo recorrer el repositorio ({type(error).__name__}).")
        return 2

    # A value present in the working tree is not reported twice as "history".
    now = {(f.path, f.kind, f.masked) for f in working}
    history = [f for f in history if (f.path, f.kind, f.masked) not in now]

    print(f"Archivos versionados: {len(working)} hallazgo(s).")
    for f in working:
        where = f"{f.path}:{f.line}" if f.line else f.path
        print(f"  ARBOL     {where}  [{f.kind}]  {f.masked}")
    if not args.sin_historial:
        print(f"Historial de git: {len(history)} hallazgo(s) que ya no estan en el arbol.")
        for f in history:
            where = f"{f.path}:{f.line}" if f.line else f.path
            print(f"  HISTORIAL {where}  [{f.kind}]  {f.masked}  commit {f.commit}")

    if working or history:
        print(
            "\nHay secretos expuestos. Borrarlos no basta: ROTE cada uno (clave nueva en el "
            "proveedor y revocar la anterior). Este programa no imprime los valores."
        )
        return 1
    print("Sin hallazgos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
