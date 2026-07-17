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
    # ── FocusNFe (Fase 2-prep) — presentes apenas quando import_origin
    # for "fiscalone_focusnfe". Campos opcionais nao quebram consumidores
    # que ignoram chaves desconhecidas.
    versao:          int               # cursor incremental Focus (nao NSU)
    raw_json_focus:  str               # JSON serializado da resposta Focus
    danfe_sha256:    str               # hash do DANFE PDF baixado
    danfe_fonte:     str               # "focusnfe" | "sefaz" | "email" | ...
    # ── FocusNFe · Fase E4a — schema real da doc oficial.
    # Todos opcionais (total=False do TypedDict). SEFAZ/ADN nao populam.
    nfe_completa:    bool              # Focus tem XML completo (nfeProc)?
    tipo_nfe:        str               # "0"=Entrada | "1"=Saida
    manifestacao:    str               # nulo|ciencia|confirmacao|desconhecimento|nao_realizada
    situacao_focus:  str               # autorizada|cancelada|denegada (raw Focus)
    cancelado:       int               # 1 se situacao=cancelada; senao 0
    xml_pending:     bool              # XML nfeProc ainda nao baixado (cap batch ou falha)
    data_cancelamento: str             # ISO 8601 — presente so em cancelada
    justificativa_cancelamento: str    # texto Focus — presente so em cancelada
