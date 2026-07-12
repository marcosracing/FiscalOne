"""NSU: SEFAZ zfill(15), ADN preservado, provider desconhecido → ValueError."""
import pytest
from services.nsu_utils import normalizar_nsu


class TestNsuSefaz:
    def test_zfill_15(self):
        assert normalizar_nsu("sefaz", "nfe", "123") == "000000000000123"

    def test_sefaz_alias_import_origin(self):
        assert normalizar_nsu("fiscalone_sefaz", "cte", "1") == "000000000000001"

    def test_string_sem_digitos_vira_zero(self):
        assert normalizar_nsu("sefaz", "nfe", "abc") == "000000000000000"

    def test_vazio_vira_zero(self):
        assert normalizar_nsu("sefaz", "nfe", "") == "000000000000000"

    def test_extrai_digitos_ignora_letras(self):
        assert normalizar_nsu("sefaz", "nfe", "abc123") == "000000000000123"


class TestNsuAdn:
    def test_preserva_numero(self):
        assert normalizar_nsu("adn_nfse", "nfse", "125643") == "125643"

    def test_adn_alias_import_origin(self):
        assert normalizar_nsu("fiscalone_nfse_adn", "nfse", "125643") == "125643"

    def test_nunca_zfill(self):
        assert normalizar_nsu("adn_nfse", "nfse", "5") == "5"

    def test_string_livre_preservada(self):
        # ADN pode aceitar formatos diferentes (embora tipicamente numerico).
        assert normalizar_nsu("adn_nfse", "nfse", "abc-def") == "abc-def"

    def test_vazio_vira_zero(self):
        assert normalizar_nsu("adn_nfse", "nfse", "") == "0"


class TestNsuProviderDesconhecido:
    def test_provider_invalido_erro_controlado(self):
        with pytest.raises(ValueError, match="Provider desconhecido"):
            normalizar_nsu("focusnfe", "nfe", "1")

    def test_provider_vazio_erro(self):
        with pytest.raises(ValueError):
            normalizar_nsu("", "nfe", "1")
