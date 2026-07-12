"""
cte_provider — Encapsula parse_cte + normalizar_nsu SEFAZ.

Uso pretendido pelo dfe_fetch_service quando processa docZips de CT-e
retornados pela CTeDistDFeInteresse.
"""
from xml_parser import parse_cte
from services.nsu_utils import normalizar_nsu

PROVIDER_TAG = "sefaz"
IMPORT_ORIGIN = "fiscalone_gov_fetch"


def normalizar_nsu_sefaz(nsu: str) -> str:
    """NSU CT-e SEFAZ — 15 digitos zero-padded."""
    return normalizar_nsu(PROVIDER_TAG, "cte", nsu)


def processar(xml: str, trace_id: str, filename: str = "cte.xml") -> dict:
    """Roteia XML CT-e para parser dedicado. Retorno sempre com status_xml."""
    return parse_cte(xml, import_origin=IMPORT_ORIGIN,
                     trace_id=trace_id, filename=filename)
