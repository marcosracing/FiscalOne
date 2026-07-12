"""
nfe_provider — Encapsula parse_nfe + normalizar_nsu SEFAZ.

Uso pretendido pelo dfe_fetch_service quando processa docZips de NF-e
retornados pela NFeDistDFeInteresse.
"""
from xml_parser import parse_nfe
from services.nsu_utils import normalizar_nsu

PROVIDER_TAG = "sefaz"       # para normalizar_nsu
IMPORT_ORIGIN = "fiscalone_gov_fetch"


def normalizar_nsu_sefaz(nsu: str) -> str:
    """NSU NF-e SEFAZ — 15 digitos zero-padded."""
    return normalizar_nsu(PROVIDER_TAG, "nfe", nsu)


def processar(xml: str, trace_id: str, filename: str = "nfe.xml") -> dict:
    """Roteia XML NF-e para parser dedicado. Retorno sempre com status_xml."""
    return parse_nfe(xml, import_origin=IMPORT_ORIGIN,
                     trace_id=trace_id, filename=filename)
