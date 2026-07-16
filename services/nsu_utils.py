"""
nsu_utils — Normalizacao explicita do NSU por provider.

Regra definitiva (2026-07-17):

  - SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse
      NSU e string decimal de 15 digitos zero-padded (spec XSD SEFAZ).
      → aplicar zfill(15) apos limpar nao-digitos.

  - ADN NFS-e Nacional (Distribuicao DFe por NSU)
      NSU e string livre (path REST GET /contribuintes/DFe/{NSU}).
      → preservar exatamente como veio; NUNCA zfill.

  - FocusNFe (recebimento NF-e/CT-e via API REST)
      Cursor e a "versao" incremental exposta pela Focus (int em JSON).
      → preservar como string; NUNCA zfill; None/vazio → "0".

Providers desconhecidos levantam ValueError — decisao aparece no lote,
nao quebra o processo.
"""
import re

# Aliases aceitos por provider — permite chamadores usarem tanto o "nome curto"
# (sefaz, adn_nfse, focusnfe) quanto o import_origin correspondente.
_SEFAZ_PROVIDERS    = frozenset({"sefaz", "fiscalone_sefaz"})
_ADN_PROVIDERS      = frozenset({"adn_nfse", "fiscalone_nfse_adn"})
_FOCUSNFE_PROVIDERS = frozenset({"focusnfe", "fiscalone_focusnfe"})

NSU_SEFAZ_LEN = 15


def normalizar_nsu(provider: str, doc_type: str, nsu) -> str:
    """
    Normaliza NSU conforme provider. Nao valida contra maxNSU.

    Args:
        provider:  "sefaz" | "fiscalone_sefaz" | "adn_nfse" |
                   "fiscalone_nfse_adn" | "focusnfe" | "fiscalone_focusnfe"
        doc_type:  "nfe" | "cte" | "nfse"  (informativo — nao muda regra)
        nsu:       string bruta do payload (aceita int para FocusNFe versao)

    Returns:
        NSU normalizado (str). SEFAZ: 15 digitos zero-padded. ADN/Focus:
        string preservada; se vazio, "0".

    Raises:
        ValueError se provider desconhecido.
    """
    prov = (provider or "").lower().strip()

    if prov in _SEFAZ_PROVIDERS:
        nsu_str = (nsu or "").strip() if isinstance(nsu, str) else str(nsu or "")
        digits = re.sub(r"\D", "", nsu_str) or "0"
        return digits.zfill(NSU_SEFAZ_LEN)

    if prov in _ADN_PROVIDERS:
        # ADN preserva exatamente. Se veio vazio, entrega "0" (inicio).
        nsu_str = (nsu or "").strip() if isinstance(nsu, str) else str(nsu or "")
        return nsu_str or "0"

    if prov in _FOCUSNFE_PROVIDERS:
        # FocusNFe usa "versao" incremental (int em JSON). Preservar como
        # string sem zfill; None/vazio → "0" (inicio). Nunca "None".
        if nsu is None:
            return "0"
        raw = str(nsu).strip()
        return raw or "0"

    raise ValueError(f"Provider desconhecido para normalizacao NSU: {provider!r}")
