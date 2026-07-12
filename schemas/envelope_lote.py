"""
envelope_lote — Envelope de saida do FiscalOne para lote de documentos.

Este e o contrato que o MapOne consome em:
  - POST /fiscal/gov/fetch
  - POST /fiscal/documents/import
"""
from typing import TypedDict, List, Union

from schemas import StatusLote, ImportOrigin
from schemas.nfe_schema  import NFeDoc, NFeDocOpcional
from schemas.cte_schema  import CTeDoc, CTeDocOpcional
from schemas.nfse_schema import NFSeDoc, NFSeDocOpcional


DocQualquer = Union[NFeDoc, CTeDoc, NFSeDoc]


class EnvelopeLote(TypedDict, total=True):
    ok:            bool
    trace_id:      str
    status_lote:   StatusLote
    recebidos:     int    # itens que chegaram para processamento
    processados:  int    # itens que o FiscalOne conseguiu inspecionar
    persistidos:  int    # itens ok:true no envelope (COMPLETO)
    duplicados:   int    # itens ja vistos no lote (mesma chave/NSU)
    resumos:      int    # itens em resumos[]
    eventos:      int    # itens EVENTO
    erros:        int    # itens em erros[]
    docs:         List[DocQualquer]
