"""
schemas — Contratos de saida do FiscalOne por tipo de documento.

Fonte da verdade para MapOne. Sao TypedDicts (checagem estatica). O envelope
runtime pode conter chaves opcionais adicionais, mas os campos declarados
aqui sao os garantidos.

Modulos:
    nfe_schema       — payload de NF-e
    cte_schema       — payload de CT-e
    nfse_schema      — payload de NFS-e Nacional (ADN)
    envelope_lote    — envelope /gov/fetch e /documents/import

Constantes compartilhadas centralizadas neste modulo.
"""
from typing import Literal

# ── status_xml — estado do XML dentro do envelope ────────────────────────────
StatusXml = Literal[
    "COMPLETO",              # XML fiscal integral parseado com sucesso
    "RESUMO",                # DFe resumido (resNFe/resCTe/resEvento)
    "EVENTO",                # procEventoNFe/procEventoCTe
    "FALHA_PROCESSAMENTO",   # erro de parser/layout desconhecido
    "RECEBIDA",              # DFe recebida ainda nao processada (reservado)
]

# ── import_origin — origem do documento no envelope ─────────────────────────
ImportOrigin = Literal[
    "fiscalone_gov_fetch",   # parse invocado pelo /fiscal/gov/fetch
    "fiscalone_sefaz",       # persistido no MapOne com origem SEFAZ
    "fiscalone_upload",      # POST /fiscal/documents/import
    "fiscalone_nfse_adn",    # NFS-e Nacional via ADN
    "fiscalone_email",       # captacao por email (MapOne)
    "fiscalone_reparse",     # reprocessamento manual (MapOne)
]

# ── status_lote — resultado agregado do lote ────────────────────────────────
StatusLote = Literal[
    "SUCESSO_TOTAL",         # todos processados sem erro
    "SUCESSO_PARCIAL",       # >=1 sucesso + >=1 erro
    "FALHA_TOTAL",           # 0 sucesso e >=1 tentativa
    "SEM_DOCUMENTO",         # SEFAZ/ADN devolveu 0 documentos
]

VALID_STATUS_XML     = frozenset(StatusXml.__args__)
VALID_IMPORT_ORIGIN  = frozenset(ImportOrigin.__args__)
VALID_STATUS_LOTE    = frozenset(StatusLote.__args__)
