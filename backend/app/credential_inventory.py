"""Inventario de credenciales SIN valores: qué existe, dónde vive, si git lo ignora y cuándo vence.

REGLA ABSOLUTA: ninguna función de este módulo devuelve, registra ni conserva el valor de un secreto.
  * `.env`: `read_env_names` lee línea por línea y devuelve SOLO los nombres de las variables con valor no vacío;
    el valor se descarta en la misma iteración (solo se mide si está vacío).
  * Archivos de tokens: `read_token_facts` conserva los nombres de los campos con valor y las FECHAS de
    vencimiento ya convertidas; el resto del JSON se descarta antes de volver.
Quien llame no puede obtener un valor aunque quiera: no existe ninguna ruta que lo devuelva.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.credential_catalog import (
    CREDENTIAL_SPECS,
    ENV_FILE,
    KIND_LABELS,
    PLATFORM_LABELS,
    PLATFORMS,
    TOKEN_FILES,
    CredentialSpec,
)

BACKEND_DIR: Final = Path(__file__).resolve().parent.parent
ENV_NAME = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")
QUOTES = "\"'"
GIT_TIMEOUT_SECONDS = 10
MILLISECOND_EPOCH_FLOOR = 100_000_000_000  # un epoch mayor que esto está en milisegundos
SECONDS_PER_DAY = 86_400
EXAMPLE_SUFFIX = ".example"  # .env.example no guarda secretos: no cuenta como copia
# `backup.py` copia las TABLAS de la base a un zip de CSV; ningún archivo de credenciales entra al respaldo.
FILES_IN_APP_BACKUP: Final[frozenset[str]] = frozenset()


@dataclass(frozen=True, slots=True)
class TokenFileFacts:
    """Lo único que se conserva de un archivo de tokens: qué campos tienen valor y cuándo vencen."""

    fields_present: frozenset[str]
    expiries: dict[str, datetime | None]


def read_env_names(path: Path) -> frozenset[str]:
    """Nombres de las variables con valor no vacío en un archivo .env. Los valores NUNCA salen de la iteración."""
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    names: set[str] = set()
    with handle:
        for line in handle:
            match = None if line.lstrip().startswith("#") else ENV_NAME.match(line)
            if match is not None and match.group(2).strip().strip(QUOTES):
                names.add(match.group(1))
    return frozenset(names)


def parse_expiry(value: Any) -> datetime | None:
    """Epoch (segundos o milisegundos) o texto ISO -> fecha con zona. Cualquier otra cosa -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        try:
            value = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if isinstance(value, int | float) and value > 0:
        seconds = value / 1000 if value >= MILLISECOND_EPOCH_FLOOR else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def read_token_facts(path: Path, expiry_fields: frozenset[str]) -> TokenFileFacts:
    """Campos con valor + fechas de vencimiento de un archivo de tokens. Archivo ausente o ilegible: sin nada."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TokenFileFacts(frozenset(), {})
    if not isinstance(data, dict):
        return TokenFileFacts(frozenset(), {})
    present = frozenset(str(key) for key, value in data.items() if value not in (None, "", [], {}))
    expiries = {field: parse_expiry(data.get(field)) for field in expiry_fields}
    del data  # el resto del contenido (los tokens) no sale de esta función
    return TokenFileFacts(present, expiries)


def git_ignores(path: Path, repo_dir: Path) -> bool | None:
    """¿`git check-ignore` ignora el archivo? None si no se puede saber (sin git o fuera de un repositorio)."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            cwd=repo_dir,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(result.returncode)


def sibling_copies(path: Path) -> list[Path]:
    """Copias junto al archivo (`.bak`, `.pre-*.bak`, `.lock`, `.tmp`): contienen el mismo secreto y también hay que borrarlas."""
    return sorted(p for p in path.parent.glob(f"{path.name}.*") if p.is_file() and not p.name.endswith(EXAMPLE_SUFFIX))


def _days_left(expires: datetime | None, now: datetime) -> float | None:
    return None if expires is None else round((expires - now).total_seconds() / SECONDS_PER_DAY, 1)


@dataclass(frozen=True, slots=True)
class _Sources:
    """Lo leído una sola vez por archivo, para no abrirlo una vez por credencial."""

    env_names: frozenset[str]
    token_facts: dict[str, TokenFileFacts]


def _read_sources(base_dir: Path, environ: dict[str, str]) -> _Sources:
    env_names = read_env_names(base_dir / ENV_FILE) | {name for name, value in environ.items() if value.strip()}
    expiry_fields: dict[str, set[str]] = {}
    for spec in CREDENTIAL_SPECS:
        if spec.location == "file" and spec.expiry_field:
            expiry_fields.setdefault(spec.file, set()).add(spec.expiry_field)
    facts = {file: read_token_facts(base_dir / file, frozenset(expiry_fields.get(file, ()))) for file in TOKEN_FILES}
    return _Sources(env_names, facts)


def _is_present(spec: CredentialSpec, sources: _Sources) -> bool:
    if spec.location == "env":
        return spec.name in sources.env_names
    return spec.field in sources.token_facts[spec.file].fields_present


def present_platforms(base_dir: Path = BACKEND_DIR, environ: dict[str, str] | None = None) -> frozenset[str]:
    """Plataformas con al menos una credencial presente (sin consultar git: es barato para el Centro de acciones)."""
    sources = _read_sources(base_dir, dict(os.environ) if environ is None else environ)
    return frozenset(spec.platform for spec in CREDENTIAL_SPECS if _is_present(spec, sources))


IgnoreCheck = Callable[[Path], bool | None]


def _ignore_check(base_dir: Path, check_git: bool) -> IgnoreCheck:
    """Consulta a git con memoria: varias credenciales comparten archivo y cada consulta lanza un proceso."""
    known: dict[Path, bool | None] = {}

    def check(path: Path) -> bool | None:
        if not check_git:
            return None
        if path not in known:
            known[path] = git_ignores(path, base_dir)
        return known[path]

    return check


def _item(spec: CredentialSpec, sources: _Sources, base_dir: Path, now: datetime, ignored: IgnoreCheck) -> dict[str, Any]:
    path = base_dir / spec.file
    expires = sources.token_facts[spec.file].expiries.get(spec.expiry_field) if spec.expiry_field else None
    return {
        "platform": spec.platform,
        "name": spec.name,
        "kind": spec.kind,
        "kind_label": KIND_LABELS[spec.kind],
        "location": spec.location,
        "file": spec.file,
        "present": _is_present(spec, sources),
        "git_ignored": ignored(path),
        "expires_at": expires.isoformat() if expires else None,
        "days_left": _days_left(expires, now),
        "expired": expires is not None and expires <= now,
        "in_app_backup": spec.file in FILES_IN_APP_BACKUP,
        "copies": [{"file": c.name, "git_ignored": ignored(c)} for c in sibling_copies(path)],
    }


def build_inventory(
    *,
    base_dir: Path = BACKEND_DIR,
    environ: dict[str, str] | None = None,
    now: datetime | None = None,
    check_git: bool = True,
) -> list[dict[str, Any]]:
    """Una fila por credencial conocida (presente o no), agrupable por plataforma. Sin ningún valor."""
    now = now or datetime.now(UTC)
    sources = _read_sources(base_dir, dict(os.environ) if environ is None else environ)
    ignored = _ignore_check(base_dir, check_git)
    return [_item(spec, sources, base_dir, now, ignored) for spec in CREDENTIAL_SPECS]


def group_by_platform(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa el inventario por plataforma, en el orden del catálogo."""
    return [
        {
            "platform": platform,
            "label": PLATFORM_LABELS[platform],
            "present": any(i["present"] for i in items if i["platform"] == platform),
            "credentials": [i for i in items if i["platform"] == platform],
        }
        for platform in PLATFORMS
    ]
