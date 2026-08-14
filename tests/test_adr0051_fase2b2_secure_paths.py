"""
ADR-0051 Fase 2B.2 · FiscalOne — validação defensiva do caminho de PFX.

Cobre secure_paths.validate_pfx_path e a integração com cert_provider:

  1. certificado em caminho permitido → OK.
  2. caminho fora da raiz rejeitado.
  3. travessia `../` rejeitada.
  4. symlink rejeitado.
  5. arquivo não regular (diretório) rejeitado.
  6. permissão insegura rejeitada.
  7. ausência do arquivo falha nominalmente.
  8. cert_provider._read_env_pfx propaga o código nominal.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_dummy_pfx(path: Path, permissions: int = 0o600) -> None:
    path.write_bytes(b"\x00PFX-DUMMY\x00")
    os.chmod(path, permissions)


def test_path_permitido_ok(tmp_path):
    from services.secure_paths import validate_pfx_path
    root = tmp_path / "allowed"
    root.mkdir()
    pfx = root / "cert.pfx"
    _write_dummy_pfx(pfx)
    resolved = validate_pfx_path(str(pfx), allowed_roots=[root.resolve()])
    assert resolved == pfx.resolve()


def test_path_fora_da_allowlist_rejeitado(tmp_path):
    from services.secure_paths import validate_pfx_path, SecurePathError
    permitido = tmp_path / "allowed"
    permitido.mkdir()
    outra = tmp_path / "outra"
    outra.mkdir()
    pfx = outra / "cert.pfx"
    _write_dummy_pfx(pfx)
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path(str(pfx), allowed_roots=[permitido.resolve()])
    assert excinfo.value.codigo == "CERT_PATH_FORA_ALLOWLIST"


def test_travessia_rejeitada(tmp_path):
    from services.secure_paths import validate_pfx_path, SecurePathError
    root = tmp_path / "allowed"
    root.mkdir()
    caminho = f"{root}/subdir/../../fuga.pfx"
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path(caminho, allowed_roots=[root.resolve()])
    assert excinfo.value.codigo == "CERT_PATH_TRAVESSIA"


def test_relativo_rejeitado():
    from services.secure_paths import validate_pfx_path, SecurePathError
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path("cert.pfx", allowed_roots=[Path("/tmp").resolve()])
    assert excinfo.value.codigo == "CERT_PATH_TRAVESSIA"


def test_symlink_rejeitado(tmp_path):
    from services.secure_paths import validate_pfx_path, SecurePathError
    root = tmp_path / "allowed"
    root.mkdir()
    alvo = root / "real.pfx"
    _write_dummy_pfx(alvo)
    link = root / "cert.pfx"
    link.symlink_to(alvo)
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path(str(link), allowed_roots=[root.resolve()])
    assert excinfo.value.codigo == "CERT_PATH_SYMLINK"


def test_diretorio_rejeitado(tmp_path):
    from services.secure_paths import validate_pfx_path, SecurePathError
    root = tmp_path / "allowed"
    root.mkdir()
    subdir = root / "cert.pfx"
    subdir.mkdir()
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path(str(subdir), allowed_roots=[root.resolve()])
    assert excinfo.value.codigo == "CERT_PATH_NAO_REGULAR"


def test_permissao_insegura_rejeitada(tmp_path):
    from services.secure_paths import validate_pfx_path, SecurePathError
    root = tmp_path / "allowed"
    root.mkdir()
    pfx = root / "cert.pfx"
    _write_dummy_pfx(pfx, permissions=0o644)
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path(str(pfx), allowed_roots=[root.resolve()])
    assert excinfo.value.codigo == "CERT_PATH_PERMISSAO_INSEGURA"


def test_permissao_flexivel_quando_solicitado(tmp_path):
    from services.secure_paths import validate_pfx_path
    root = tmp_path / "allowed"
    root.mkdir()
    pfx = root / "cert.pfx"
    _write_dummy_pfx(pfx, permissions=0o644)
    resolved = validate_pfx_path(
        str(pfx),
        allowed_roots=[root.resolve()],
        require_restricted_permissions=False,
    )
    assert resolved == pfx.resolve()


def test_arquivo_ausente(tmp_path):
    from services.secure_paths import validate_pfx_path, SecurePathError
    root = tmp_path / "allowed"
    root.mkdir()
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path(str(root / "nao_existe.pfx"), allowed_roots=[root.resolve()])
    assert excinfo.value.codigo == "CERT_PATH_NAO_ENCONTRADO"


def test_caminho_vazio_rejeitado():
    from services.secure_paths import validate_pfx_path, SecurePathError
    with pytest.raises(SecurePathError) as excinfo:
        validate_pfx_path("")
    assert excinfo.value.codigo == "CERT_PATH_VAZIO"


def test_cert_provider_propaga_codigo_nominal(tmp_path, monkeypatch):
    from services import cert_provider
    from services.secure_paths import ENV_ALLOWED_ROOTS
    root = tmp_path / "cofre"
    root.mkdir()
    pfx = root / "fake.pfx"
    _write_dummy_pfx(pfx, permissions=0o644)
    monkeypatch.setenv(ENV_ALLOWED_ROOTS, str(root))
    monkeypatch.setenv("FISCALONE_CERT_PFX_PATH", str(pfx))
    monkeypatch.setenv("FISCALONE_CERT_PASSWORD", "senha-sintetica")
    monkeypatch.delenv("FISCALONE_CERT_PFX_BASE64", raising=False)
    with pytest.raises(cert_provider.CertResolveError) as excinfo:
        cert_provider._read_env_pfx()
    assert excinfo.value.codigo == "CERT_PATH_PERMISSAO_INSEGURA"
    assert "senha-sintetica" not in excinfo.value.mensagem
