"""
nfse_schema — Contrato de saida do FiscalOne para NFS-e Nacional (ADN).

IMPORTANTE: NSU do ADN e string livre. NUNCA aplicar zfill.
"""
from decimal import Decimal
from typing import TypedDict, Optional

from schemas import StatusXml, ImportOrigin


class NFSeDoc(TypedDict, total=True):
    numeroNfse:              str        # string livre (ADN nao usa zfill)
    CNPJ_prestador:          str
    discriminacaoServico:    str
    valorServicos:           Decimal
    nsu:                     str        # preservar string do provider ADN
    status_xml:              StatusXml
    import_origin:           ImportOrigin
    trace_id:                str
    parser_version:          str


class NFSeDocOpcional(TypedDict, total=False):
    codigoVerificacao:       Optional[str]
    CNAE:                    Optional[str]
    codTributacaoMunicipio:  Optional[str]
    municipioPrestacao:      Optional[str]
    dh_emi:                  str
    emit_nome:               str
    dest_cnpj:               str
    chave:                   str        # chave canonica se houver
    codigo:                  str
    erro:                    str
    ok:                      bool
    xml_bruto:               str        # NUNCA logar
    xml_hash_sha256:         str
    categoria:               str
