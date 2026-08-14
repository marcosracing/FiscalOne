"""
ADR-0051 Fase 2B.2 · FiscalOne — sem rotação/renovação/reemissão automática.

Prova estrutural: cert_provider e secure_paths não contêm funções nem
chamadas a rotação, renovação, revogação ou reemissão automática de
certificado. Renovação continua sendo operação humana explícita.

Também confirma que cert_provider mantém `wipe()` para descartar bytes
sensíveis após uso.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


PROIBIDOS_AUTOMATICOS = [
    "rotate_certificate",
    "renew_certificate",
    "reissue_certificate",
    "revoke_certificate",
    "auto_rotate",
    "auto_renew",
    "schedule_rotation",
    "schedule_renewal",
]


def _read(mod_name: str) -> str:
    from importlib import import_module
    mod = import_module(mod_name)
    return open(mod.__file__, "r", encoding="utf-8").read()


def test_cert_provider_sem_rotacao_automatica():
    src = _read("services.cert_provider")
    for proibido in PROIBIDOS_AUTOMATICOS:
        assert proibido not in src, f"cert_provider não pode expor {proibido!r}"


def test_secure_paths_sem_rotacao_automatica():
    src = _read("services.secure_paths")
    for proibido in PROIBIDOS_AUTOMATICOS:
        assert proibido not in src


def test_cert_provider_mantem_wipe():
    from services.cert_provider import wipe
    bundle = {"cert_pem": b"aaaa", "key_pem": b"bbbb"}
    wipe(bundle)
    assert bundle["cert_pem"] is None
    assert bundle["key_pem"] is None


def test_isolamento_por_cnpj_estrutural():
    """cert_provider.resolve_cert precisa comparar cert_cnpj com tenant_cnpj."""
    src = _read("services.cert_provider")
    assert "CERT_CNPJ_DIVERGENTE" in src, (
        "cert_provider deve rejeitar cert com CNPJ diferente do da tenant"
    )
    assert "cert_cnpj != expected" in src or "cert_cnpj !=  expected" in src
