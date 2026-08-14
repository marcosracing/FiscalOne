"""
secure_paths — validação defensiva de caminho de certificado A1.

ADR-0051 Fase 2B.2 · custódia proporcional. Aplica-se antes de qualquer
`Path(path).read_bytes()` sobre um PFX/PEM resolvido por variável de
ambiente ou payload confiável. Rejeita:

- caminho fora da allowlist (defesa contra travessia);
- symlink (evita apontar para fora da raiz permitida);
- arquivo não regular (diretório, socket, device, FIFO);
- permissão insegura (leitura por grupo/outros).

Retorna o `Path` absoluto e resolvido quando o caminho passa. Nunca lê
o arquivo, nunca revela senha, PFX, nome de operador ou fingerprint.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


class SecurePathError(RuntimeError):
    """Erro nominal de validação de caminho — sem vazamento de segredo."""

    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


ENV_ALLOWED_ROOTS = "FISCALONE_CERT_ALLOWED_ROOTS"


def _default_allowed_roots() -> list[Path]:
    """Raízes aceitas por padrão quando FISCALONE_CERT_ALLOWED_ROOTS
    não estiver configurada. Somente diretórios de custódia por usuário.
    """
    home = Path.home()
    return [home / ".rlogix" / "certificates"]


def resolve_allowed_roots(env_value: str | None = None) -> list[Path]:
    """Resolve a allowlist a partir de env ou default. Cada raiz é
    normalizada (`Path.resolve(strict=False)`). Não exige existência,
    mas a validação real de arquivo exige que o path resolvido esteja
    dentro de pelo menos uma raiz existente.
    """
    raw = env_value if env_value is not None else os.environ.get(ENV_ALLOWED_ROOTS, "")
    if not raw:
        return [p.resolve(strict=False) for p in _default_allowed_roots()]
    roots: list[Path] = []
    for chunk in raw.split(os.pathsep):
        chunk = chunk.strip()
        if not chunk:
            continue
        roots.append(Path(chunk).expanduser().resolve(strict=False))
    if not roots:
        return [p.resolve(strict=False) for p in _default_allowed_roots()]
    return roots


def _path_inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def validate_pfx_path(
    path: str | os.PathLike[str],
    allowed_roots: list[Path] | None = None,
    *,
    require_restricted_permissions: bool = True,
) -> Path:
    """Valida um caminho de PFX/PEM segundo os controles do ADR-0051 F2B.2.

    Retorna o Path absoluto/resolvido quando aprovado. Levanta
    `SecurePathError` com código nominal em qualquer rejeição.

    Códigos:
      - CERT_PATH_VAZIO
      - CERT_PATH_TRAVESSIA
      - CERT_PATH_FORA_ALLOWLIST
      - CERT_PATH_SYMLINK
      - CERT_PATH_NAO_ENCONTRADO
      - CERT_PATH_NAO_REGULAR
      - CERT_PATH_PERMISSAO_INSEGURA

    O caller ainda é responsável por chamar `read_bytes` e por descartar
    o material sensível após uso.
    """
    if path is None or str(path).strip() == "":
        raise SecurePathError("CERT_PATH_VAZIO", "Caminho do PFX vazio.")

    raw = str(path)
    if ".." in Path(raw).parts:
        raise SecurePathError(
            "CERT_PATH_TRAVESSIA",
            "Caminho do PFX contém '..' — travessia proibida.",
        )

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise SecurePathError(
            "CERT_PATH_TRAVESSIA",
            "Caminho do PFX deve ser absoluto (sem componentes relativos).",
        )

    roots = allowed_roots or resolve_allowed_roots()
    resolved = candidate.resolve(strict=False)
    if not any(_path_inside(resolved, root) for root in roots):
        raise SecurePathError(
            "CERT_PATH_FORA_ALLOWLIST",
            "Caminho do PFX fora da allowlist. "
            f"Configure {ENV_ALLOWED_ROOTS} para autorizar a raiz.",
        )

    try:
        lstat = candidate.lstat()
    except FileNotFoundError:
        raise SecurePathError(
            "CERT_PATH_NAO_ENCONTRADO",
            "Arquivo de PFX não encontrado no caminho configurado.",
        ) from None

    if stat.S_ISLNK(lstat.st_mode):
        raise SecurePathError(
            "CERT_PATH_SYMLINK",
            "Caminho do PFX é um symlink — proibido para custódia.",
        )

    if not stat.S_ISREG(lstat.st_mode):
        raise SecurePathError(
            "CERT_PATH_NAO_REGULAR",
            "Caminho do PFX não aponta para um arquivo regular.",
        )

    if require_restricted_permissions and (lstat.st_mode & 0o077):
        raise SecurePathError(
            "CERT_PATH_PERMISSAO_INSEGURA",
            "Permissão do arquivo PFX permite leitura por grupo ou outros. "
            "Ajuste para 0o600 (chmod 600).",
        )

    return resolved
