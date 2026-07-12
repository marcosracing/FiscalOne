"""
nsu_utils — Normalizacao explicita do NSU por provider.

Regra definitiva (2026-07-12):

  - SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse
      NSU e string decimal de 15 digitos zero-padded (spec XSD SEFAZ).
      → aplicar zfill(15) apos limpar nao-digitos.

  - ADN NFS-e Nacional (Distribuicao DFe por NSU)
      NSU e string livre (path REST GET /contribuintes/DFe/{NSU}).
      → preservar exatamente como veio; NUNCA zfill.

Providers desconhecidos levantam ValueError — decisao aparece no lote,
nao quebra o processo.
"""
import re

# Aliases aceitos por provider — permite chamadores usarem tanto o "nome curto"
# (sefaz, adn_nfse) quanto o import_origin correspondente.
_SEFAZ_PROVIDERS = frozenset({"sefaz", "fiscalone_sefaz"})
_ADN_PROVIDERS   = frozenset({"adn_nfse", "fiscalone_nfse_adn"})

NSU_SEFAZ_LEN = 15


def normalizar_nsu(provider: str, doc_type: str, nsu: str) -> str:
    """
    Normaliza NSU conforme provider. Nao valida contra maxNSU.

    Args:
        provider:  "sefaz" | "fiscalone_sefaz" | "adn_nfse" | "fiscalone_nfse_adn"
        doc_type:  "nfe" | "cte" | "nfse"  (informativo — nao muda regra)
        nsu:       string bruta do payload

    Returns:
        NSU normalizado (str). SEFAZ: 15 digitos zero-padded. ADN: string livre.

    Raises:
        ValueError se provider desconhecido.
    """
    prov = (provider or "").lower().strip()
    nsu_str = (nsu or "").strip()

    if prov in _SEFAZ_PROVIDERS:
        digits = re.sub(r"\D", "", nsu_str) or "0"
        return digits.zfill(NSU_SEFAZ_LEN)

    if prov in _ADN_PROVIDERS:
        # ADN preserva exatamente. Se veio vazio, entrega "0" (inicio).
        return nsu_str or "0"

    raise ValueError(f"Provider desconhecido para normalizacao NSU: {provider!r}")
