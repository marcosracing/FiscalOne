"""
nfe_schema — Contrato de saida do FiscalOne para NF-e.

Fonte da verdade tecnica para o MapOne. TypedDict tem verificacao estatica;
runtime pode adicionar chaves auxiliares. `total=False` marca campos opcionais.
"""
import re
from decimal import Decimal
from typing import TypedDict, Optional

from schemas import StatusXml, ImportOrigin

CHAVE_NFE_RE = re.compile(r"^\d{44}$")


def validar_chave_nfe(ch: str) -> bool:
    """Chave NF-e — 44 digitos. Nao valida digito verificador (fora do escopo)."""
    return bool(ch) and bool(CHAVE_NFE_RE.match(ch))


class NFeDoc(TypedDict, total=True):
    chNFe:          str                # 44 digitos — validar formato
    nProt:          str
    dhRecbto:       str                # ISO 8601
    CNPJ_emit:      str
    vNF:            Decimal
    vICMS:          Decimal
    cStat:          str
    xMotivo:        str
    status_xml:     StatusXml
    import_origin:  ImportOrigin
    trace_id:       str
    parser_version: str


class NFeDocOpcional(TypedDict, total=False):
    CNPJ_dest:      Optional[str]
    vIPI:           Optional[Decimal]
    nsu:            str
    numero:         str
    serie:          str
    emit_nome:      str
    dh_emi:         str
    codigo:         str                # em ok:false, codigo do erro
    erro:           str                # em ok:false, mensagem controlada
    ok:             bool
    xml_bruto:      str                # NUNCA logar
    xml_hash_sha256: str
    categoria:      str                # unificado no results[]
