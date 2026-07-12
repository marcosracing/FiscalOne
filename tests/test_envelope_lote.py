"""Envelope de lote — status_lote correto por cenario."""
from app import _classificar_status_lote


class TestStatusLote:
    def test_sem_documento(self):
        assert _classificar_status_lote(0, 0, 0) == "SEM_DOCUMENTO"

    def test_sucesso_total(self):
        assert _classificar_status_lote(10, 10, 0) == "SUCESSO_TOTAL"

    def test_sucesso_parcial_1_de_10(self):
        assert _classificar_status_lote(10, 1, 9) == "SUCESSO_PARCIAL"

    def test_falha_total_0_de_10(self):
        assert _classificar_status_lote(10, 0, 10) == "FALHA_TOTAL"

    def test_falha_total_sem_erros_explicitos(self):
        # 3 processados, nenhum persistido, sem erros classificados
        # (edge case: tudo resumo → nao ha persistidos nem erros)
        assert _classificar_status_lote(3, 0, 0) == "FALHA_TOTAL"
