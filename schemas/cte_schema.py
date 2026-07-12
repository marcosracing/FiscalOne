"""
cte_schema — Contrato de saida do FiscalOne para CT-e.
"""
import re
from decimal import Decimal
from typing import TypedDict, Literal

from schemas import StatusXml, ImportOrigin

CHAVE_CTE_RE = re.compile(r"^\d{44}$")

ModalCTe = Literal[
    "rodoviario",
    "aereo",
    "aquaviario",
    "ferroviario",
    "dutoviario",
    "multimodal",  # apesar de nao listado no prompt, existe no layout SEFAZ
]

# Mapeamento CT-e SEFAZ (modal 01..06) → nome
MODAL_CTE_MAP = {
    "01": "rodoviario",
    "02": "aereo",
    "03": "aquaviario",
    "04": "ferroviario",
    "05": "dutoviario",
    "06": "multimodal",
}


def validar_chave_cte(ch: str) -> bool:
    return bool(ch) and bool(CHAVE_CTE_RE.match(ch))


class CTeDoc(TypedDict, total=True):
    chCTe:          str                # 44 digitos
    nProt:          str
    CNPJ_emit:      str
    vTPrest:        Decimal
    modal:          ModalCTe
    status_xml:     StatusXml
    import_origin:  ImportOrigin
    trace_id:       str
    parser_version: str


class CTeDocOpcional(TypedDict, total=False):
    dest_cnpj:      str
    tomador_cnpj:   str
    tomador_papel:  str
    uf_ini:         str
    uf_fim:         str
    nsu:            str
    numero:         str
    serie:          str
    dh_emi:         str
    emit_nome:      str
    codigo:         str
    erro:           str
    ok:             bool
    xml_bruto:      str
    xml_hash_sha256: str
    categoria:      str
