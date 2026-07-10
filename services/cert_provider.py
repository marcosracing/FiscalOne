"""
cert_provider — resolve o Certificado Digital A1 em memória, por requisição.

ADR-0035 · Regras invioláveis:
  - FiscalOne NUNCA persiste PFX/PEM/senha (nem em disco, nem em banco, nem em log).
  - O PFX é usado dentro do escopo de uma única chamada e descartado.
  - Fontes aceitas, em ordem:
      1. cert_pfx_base64 + cert_password no payload (fonte "inline_base64").
      2. env GOV_CERT_PATH + GOV_CERT_PASSWORD (apenas homologação/teste local).
  - Integridade obrigatória: CNPJ embutido no cert deve bater com a tenant.
"""
import base64
import os
import re
from pathlib import Path


class CertResolveError(RuntimeError):
    """Erro controlado de resolução de certificado — NUNCA vaza segredo."""
    def __init__(self, codigo, mensagem):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


CNPJ_OID = "2.16.76.1.3.3"


def _pfx_to_pem(pfx_bytes, pw_bytes):
    """Converte PFX/PKCS#12 em (cert_pem_bytes, key_pem_bytes). Sem persistência."""
    from cryptography.hazmat.primitives.serialization import (
        pkcs12, Encoding, PrivateFormat, NoEncryption,
    )
    key, cert, _ = pkcs12.load_key_and_certificates(pfx_bytes, pw_bytes or None)
    if cert is None:
        raise CertResolveError("CERT_INVALIDO", "PFX nao contem certificado")
    if key is None:
        raise CertResolveError("CERT_INVALIDO", "PFX nao contem chave privada")
    return (
        cert.public_bytes(Encoding.PEM),
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
    ), cert


def _extract_cnpj(cert_obj):
    """Extrai CNPJ do e-CNPJ (SAN OtherName OID 2.16.76.1.3.3 ou CN 'RAZAO:CNPJ')."""
    from cryptography import x509
    try:
        san = cert_obj.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        for name in san.value:
            if isinstance(name, x509.OtherName) and name.type_id.dotted_string == CNPJ_OID:
                digits = re.sub(r"\D", "", name.value.decode("ascii", errors="ignore"))
                if len(digits) == 14:
                    return digits
    except (x509.ExtensionNotFound, Exception):
        pass
    try:
        cn = next(
            attr.value for attr in cert_obj.subject
            if attr.oid == x509.oid.NameOID.COMMON_NAME
        )
        parts = cn.split(":")
        if len(parts) >= 2:
            candidate = re.sub(r"\D", "", parts[1])
            if len(candidate) == 14:
                return candidate
    except StopIteration:
        pass
    return None


def _read_env_pfx():
    """Le PFX+senha via env (fallback homologacao). Retorna (pfx, pw) ou (None, None)."""
    path = (os.environ.get("GOV_CERT_PATH") or "").strip()
    if not path:
        return None, None, "sem_env"
    p = Path(path)
    if not p.exists():
        raise CertResolveError(
            "CERT_ENV_INVALIDO",
            "GOV_CERT_PATH configurado mas arquivo nao encontrado",
        )
    senha = os.environ.get("GOV_CERT_PASSWORD") or os.environ.get("GOV_CERT_PASS") or ""
    return p.read_bytes(), senha.encode("utf-8") if senha else b"", "env"


def resolve_cert(payload, tenant_cnpj):
    """
    Resolve o cert A1 para uma unica chamada.

    Args:
        payload: dict do request (cert_source, cert_pfx_base64, cert_password).
        tenant_cnpj: CNPJ da tenant (14 digitos) — usado para validar integridade.

    Returns:
        dict com:
          cert_pem: bytes
          key_pem:  bytes
          fonte:    "inline_base64" | "env"
          cert_cnpj: str (14 digitos)

    Raises:
        CertResolveError com codigo e mensagem controlados. NUNCA vaza segredo.
    """
    cert_source = (payload.get("cert_source") or "").strip().lower()
    pfx_b64     = payload.get("cert_pfx_base64")
    pw          = payload.get("cert_password")

    pfx_bytes = None
    pw_bytes  = b""
    fonte     = None

    if pfx_b64:
        try:
            pfx_bytes = base64.b64decode(pfx_b64, validate=True)
        except Exception:
            raise CertResolveError(
                "CERT_BASE64_INVALIDO",
                "cert_pfx_base64 nao e base64 valido",
            )
        pw_bytes = (pw or "").encode("utf-8") if pw else b""
        fonte    = "inline_base64"
    else:
        if cert_source and cert_source not in ("env", "env_or_vault"):
            raise CertResolveError(
                "CERT_FONTE_NAO_SUPORTADA",
                f"cert_source '{cert_source}' nao suportado — use cert_pfx_base64 ou env",
            )
        env_pfx, env_pw, tag = _read_env_pfx()
        if env_pfx is None:
            raise CertResolveError(
                "CERT_NAO_CONFIGURADO",
                "Nenhum certificado A1 disponivel: envie cert_pfx_base64 no payload "
                "ou configure GOV_CERT_PATH/GOV_CERT_PASSWORD (fallback homologacao)",
            )
        pfx_bytes, pw_bytes, fonte = env_pfx, env_pw, tag

    try:
        (cert_pem, key_pem), cert_obj = _pfx_to_pem(pfx_bytes, pw_bytes)
    except CertResolveError:
        raise
    except Exception as exc:
        # Nao concatena a excecao — pode conter fragmentos do PFX/senha
        raise CertResolveError(
            "CERT_ABERTURA_FALHOU",
            "Falha ao abrir o PFX (arquivo invalido ou senha incorreta)",
        ) from None

    cert_cnpj = _extract_cnpj(cert_obj)
    expected  = re.sub(r"\D", "", tenant_cnpj or "")

    if not cert_cnpj:
        raise CertResolveError(
            "CERT_SEM_CNPJ",
            "Certificado sem CNPJ ICP-Brasil identificavel (nao e e-CNPJ PJ?)",
        )
    if expected and cert_cnpj != expected:
        raise CertResolveError(
            "CERT_CNPJ_DIVERGENTE",
            f"CNPJ do certificado ({cert_cnpj}) diverge do CNPJ da tenant ({expected})",
        )

    return {
        "cert_pem":  cert_pem,
        "key_pem":   key_pem,
        "fonte":     fonte,
        "cert_cnpj": cert_cnpj,
    }


def wipe(cert_bundle):
    """Zera bytes do bundle apos uso. Boa pratica; GC nao garante limpeza rapida."""
    if not cert_bundle:
        return
    for k in ("cert_pem", "key_pem"):
        v = cert_bundle.get(k)
        if isinstance(v, (bytes, bytearray)):
            try:
                cert_bundle[k] = b"\x00" * len(v)
            except Exception:
                pass
        cert_bundle[k] = None
