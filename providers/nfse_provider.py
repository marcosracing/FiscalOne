"""
nfse_provider — Encapsula parse_nfse + normalizar_nsu ADN.

Uso pretendido pelo nfse_nacional_provider apos receber LoteDFe do ADN.
NSU ADN NUNCA sofre zfill — string livre preservada.
"""
from xml_parser import parse_nfse
from services.nsu_utils import normalizar_nsu

PROVIDER_TAG = "adn_nfse"
IMPORT_ORIGIN = "fiscalone_nfse_adn"


def normalizar_nsu_adn(nsu: str) -> str:
    """NSU NFS-e ADN — string livre preservada."""
    return normalizar_nsu(PROVIDER_TAG, "nfse", nsu)


def processar(xml: str, trace_id: str, filename: str = "nfse.xml") -> dict:
    """Roteia XML NFS-e para parser dedicado. Retorno sempre com status_xml."""
    return parse_nfse(xml, import_origin=IMPORT_ORIGIN,
                      trace_id=trace_id, filename=filename)
