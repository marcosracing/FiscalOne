"""FocusNFe · cursor seguro (contrato v2) — 2026-07-24.

Cobertura da correcao de perda logica de documentos na paginacao FocusNFe.
Bug: o cursor `X-Max-Version` era propagado como `ultimo_nsu` mesmo com
XMLs pendentes/mapper error/cap atingido, fazendo o MapOne saltar
documentos na proxima consulta.

Correcao (2026-07-24): `gov_fetch` calcula `cursor_seguro` que nunca
ultrapassa a menor versao com pendencia OU com erro de mapper. Erros
tecnicos (FOCUS_TIMEOUT, FOCUS_HTTP_ERROR etc) nao podem virar
SEM_DOCUMENTO — classificador retorna ERRO com `nsu_avancou=False`.

Cobre 15 cenarios exigidos pelo prompt do Codex.

Zero HTTP real; zero token; usa `_mock_resp` local.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.focusnfe_provider import FocusNFeProvider


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    # Cap 25 (default) — permite testar o cenario ">100 docs, cap 25".
    monkeypatch.delenv("FOCUSNFE_XML_BATCH_CAP", raising=False)
    return FocusNFeProvider()


@pytest.fixture
def provider_cap_menor(monkeypatch):
    """Cap menor (5) para testar rapidamente o cenario de pendencia."""
    monkeypatch.setenv("FOCUSNFE_TOKEN", "abcdef123456")
    monkeypatch.setenv("FOCUSNFE_TIMEOUT", "10")
    monkeypatch.setenv("FOCUSNFE_XML_BATCH_CAP", "5")
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    # Necessario re-importar o modulo para o env do cap ter efeito no
    # `_XML_BATCH_CAP` (lido no import).
    import importlib
    from providers import focusnfe_provider as fnp
    importlib.reload(fnp)
    return fnp.FocusNFeProvider()


def _mock_resp(status=200, json_data=None, headers=None, text="", content=b""):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = text
    resp.content = content
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


def _item_nfe(versao: int, chave: str | None = None,
              nfe_completa: bool = True) -> dict:
    ch = chave or f"35260722222222000181550020000000{versao:012d}"[:44]
    return {
        "chave_nfe":            ch,
        "numero":               str(versao),
        "serie":                "1",
        "data_emissao":         "2026-07-20T10:00:00-03:00",
        "documento_emitente":   "22222222000181",
        "cnpj_destinatario":    "11111111000191",
        "nome_emitente":        f"Emit {versao}",
        "valor_total":          "100.00",
        "valor_icms":           "18.00",
        "situacao":             "autorizada",
        "manifestacao_destinatario": "ciencia_operacao",
        "nfe_completa":         nfe_completa,
        "versao":               versao,
        "tipo_nfe":             "entrada",
        "protocolo":            f"1010000{versao}",
    }


# ── 1. Cursor nao ultrapassa pendencia dentro da pagina ──────────────────
class TestCursorNaoUltrapassaPendencia:

    @patch("providers.focusnfe_provider.requests.get")
    def test_cap_25_com_30_docs_cursor_para_no_25o(
            self, mock_get, provider):
        """30 docs, cap 25: primeiros 25 baixam XML, ultimos 5 viram
        pending. O cursor seguro nao pode ultrapassar a menor versao
        pendente (26)."""
        docs = [_item_nfe(v) for v in range(1, 31)]  # versoes 1..30
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "30",
                                       "X-Total-Count": "30"},
                              json_data=docs)
        # 25 respostas 200 para XMLs (cap 25); nada mais deve ser chamado.
        xml_ok = _mock_resp(status=200, text="<nfeProc/>")
        mock_get.side_effect = [listagem] + [xml_ok] * 25
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["ok"] is True
        assert r["xmls_baixados"] == 25
        assert r["xmls_pendentes"] == 5
        # Cursor seguro NAO pode ultrapassar a menor versao pendente (26).
        assert int(r["cursor_seguro"]) == 25
        assert int(r["ultimo_nsu"]) == 25
        assert int(r["max_nsu"]) == 25
        # Marcadores explicitos do contrato.
        assert r["menor_versao_pendente_ou_erro"] == "26"
        assert r["versao_pagina"] == "30"
        assert r["has_more"] is True
        assert r["nsu_avancou"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_segunda_execucao_recupera_pendente_sem_duplicar(
            self, mock_get, provider):
        """Segunda execucao com versao=25: FocusNFe devolve docs 26..30,
        cursor avanca para 30, sem duplicar 1..25."""
        docs = [_item_nfe(v) for v in range(26, 31)]
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "30",
                                       "X-Total-Count": "5"},
                              json_data=docs)
        xml_ok = _mock_resp(status=200, text="<nfeProc/>")
        mock_get.side_effect = [listagem] + [xml_ok] * 5
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "25"},
            "fo-safe")
        assert r["ok"] is True
        assert r["xmls_baixados"] == 5
        assert r["xmls_pendentes"] == 0
        assert int(r["cursor_seguro"]) == 30
        assert r["menor_versao_pendente_ou_erro"] is None
        # Nao houve duplicidade: recebemos exatamente 5 docs unicos.
        chaves_retornadas = {d["chNFe"] for d in r["documentos"]}
        assert len(chaves_retornadas) == 5

    @patch("providers.focusnfe_provider.requests.get")
    def test_falha_xml_intermediaria_trava_cursor_antes_do_gap(
            self, mock_get, provider):
        """5 docs, XML do 3o falha com 404: cursor para em versao(2), nao
        ultrapassa o 3o pendente."""
        docs = [_item_nfe(v) for v in range(1, 6)]  # versoes 1..5
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "5"},
                              json_data=docs)
        xml_ok = _mock_resp(status=200, text="<nfeProc/>")
        xml_404 = _mock_resp(status=404, text="")
        # Doc 1 OK, doc 2 OK, doc 3 404 (pending), doc 4 OK, doc 5 OK.
        mock_get.side_effect = [listagem, xml_ok, xml_ok, xml_404, xml_ok, xml_ok]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["xmls_baixados"] == 4
        assert r["xmls_pendentes"] == 1
        # Cursor seguro para em versao(3) - 1 = 2.
        assert int(r["cursor_seguro"]) == 2
        assert r["menor_versao_pendente_ou_erro"] == "3"
        # A pagina viu ate versao 5, mas so podemos avancar ate 2.
        assert r["versao_pagina"] == "5"


# ── 2. Mapper error preserva versao e trava cursor ───────────────────────
class TestMapperErrorTravaCursor:

    @patch("providers.focusnfe_provider.requests.get")
    def test_mapper_error_intermediario_trava_cursor(
            self, mock_get, provider):
        """3 docs, o 2o (versao 5) e' invalido (sem chave). Cursor seguro
        para em versao(5) - 1 = 4."""
        item_bom_1 = _item_nfe(3, nfe_completa=False)
        item_ruim = {"versao": 5, "documento_emitente": "999", "numero": "X"}
        item_bom_2 = _item_nfe(7, nfe_completa=False)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "7"},
                              json_data=[item_bom_1, item_ruim, item_bom_2])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["ok"] is True
        assert len(r["documentos"]) == 2
        assert len(r["erros"]) == 1
        # Mapper error preserva versao no envelope de erro.
        assert r["erros"][0]["versao"] == 5
        assert r["erros"][0]["codigo"] == "FOCUS_ITEM_INVALIDO"
        # Cursor seguro nao ultrapassa o gap.
        assert int(r["cursor_seguro"]) == 4
        assert r["menor_versao_pendente_ou_erro"] == "5"


# ── 3. has_more / total_count ────────────────────────────────────────────
class TestHasMore:

    @patch("providers.focusnfe_provider.requests.get")
    def test_pagina_com_100_docs_indica_has_more(
            self, mock_get, provider):
        docs = [_item_nfe(v, nfe_completa=False) for v in range(1, 101)]
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "100",
                                       "X-Total-Count": "250"},
                              json_data=docs)
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["quantidade_retornada"] == 100
        assert r["has_more"] is True
        assert r["total_count"] == 250

    @patch("providers.focusnfe_provider.requests.get")
    def test_x_total_count_maior_que_retornado_forca_has_more(
            self, mock_get, provider):
        docs = [_item_nfe(v, nfe_completa=False) for v in range(1, 6)]  # 5 docs
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "5",
                                       "X-Total-Count": "10"},
                              json_data=docs)
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        # Retornou 5, total_count 10 → ainda ha 5 pendentes no servidor.
        assert r["quantidade_retornada"] == 5
        assert r["total_count"] == 10
        assert r["has_more"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_pagina_pequena_e_total_igual_sem_has_more(
            self, mock_get, provider):
        docs = [_item_nfe(v, nfe_completa=False) for v in range(1, 4)]  # 3 docs
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "3",
                                       "X-Total-Count": "3"},
                              json_data=docs)
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["quantidade_retornada"] == 3
        assert r["has_more"] is False


# ── 4. Cursor sem pendencia = X-Max-Version ──────────────────────────────
class TestCursorSemPendencia:

    @patch("providers.focusnfe_provider.requests.get")
    def test_sem_pendencia_cursor_seguro_e_versao_pagina(
            self, mock_get, provider):
        # 3 docs completos, todos com XML baixado com sucesso.
        docs = [_item_nfe(v) for v in range(1, 4)]
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "42"},
                              json_data=docs)
        xml_ok = _mock_resp(status=200, text="<nfeProc/>")
        mock_get.side_effect = [listagem, xml_ok, xml_ok, xml_ok]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["xmls_pendentes"] == 0
        # Sem pendencias, o cursor seguro chega ate a versao_pagina.
        assert int(r["cursor_seguro"]) == 42
        assert r["menor_versao_pendente_ou_erro"] is None

    @patch("providers.focusnfe_provider.requests.get")
    def test_cursor_seguro_nunca_regride_abaixo_de_versao_entrada(
            self, mock_get, provider):
        # Nenhum item retornado; versao_entrada=100. Cursor deve permanecer.
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "100"},
                              json_data=[])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "100"},
            "fo-safe")
        assert int(r["cursor_seguro"]) == 100
        assert r["nsu_avancou"] is False


# ── 5. NF-e usa endpoint individual OFICIAL ──────────────────────────────
class TestNfeEndpointOficial:

    @patch("providers.focusnfe_provider.requests.get")
    def test_baixar_xml_completo_usa_v2_nfes_recebidas_chave_xml(
            self, mock_get, provider):
        xml_ok = _mock_resp(status=200, text="<nfeProc/>")
        mock_get.return_value = xml_ok
        r = provider.baixar_xml_completo(
            chave="35260722222222000181550020000000000000000001",
            ambiente="homologacao")
        assert r["ok"] is True
        args, _ = mock_get.call_args
        assert args[0].endswith(
            "/v2/nfes_recebidas/35260722222222000181550020000000000000000001.xml"
        )


# ── 6. NFSe endpoint individual oficial (fallback) ───────────────────────
class TestNfseFallbackOficial:

    @patch("providers.focusnfe_provider.requests.get")
    def test_baixar_xml_nfse_por_chave_usa_v2_nfsens_recebidas_chave_xml(
            self, mock_get, provider):
        xml_ok = _mock_resp(status=200, text="<CompNfse/>")
        mock_get.return_value = xml_ok
        r = provider.baixar_xml_nfse_por_chave(
            chave="chave-nfse-12345",
            ambiente="homologacao")
        assert r["ok"] is True
        args, _ = mock_get.call_args
        assert args[0].endswith("/v2/nfsens_recebidas/chave-nfse-12345.xml")


# ── 7. Erro tecnico nunca vira SEM_DOCUMENTO ─────────────────────────────
class TestErroTecnicoNaoViraSemDocumento:

    @patch("providers.focusnfe_provider.requests.get")
    def test_timeout_devolve_erro_estruturado(
            self, mock_get, provider):
        mock_get.side_effect = requests.exceptions.Timeout()
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-safe")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_TIMEOUT"
        assert r["nsu_avancou"] is False
        # Cursor NAO avanca em erro tecnico.
        assert r["ultimo_nsu"] == "42"

    @patch("providers.focusnfe_provider.requests.get")
    def test_401_devolve_erro_estruturado(self, mock_get, provider):
        mock_get.return_value = _mock_resp(status=401, json_data={})
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-safe")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_AUTH_ERROR"
        assert r["nsu_avancou"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_429_devolve_rate_limit_com_cooldown(
            self, mock_get, provider):
        mock_get.return_value = _mock_resp(
            status=429, headers={"Retry-After": "120"}, json_data={})
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-safe")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_RATE_LIMIT"
        assert r["cooldown_recomendado_seg"] == 120
        assert r["nsu_avancou"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_500_devolve_server_error(self, mock_get, provider):
        mock_get.return_value = _mock_resp(status=503, json_data={})
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-safe")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_SERVER_ERROR"
        assert r["nsu_avancou"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_json_invalido_devolve_parse_error(
            self, mock_get, provider):
        # `_mock_resp` sem json_data levanta ValueError em .json()
        mock_get.return_value = _mock_resp(status=200)
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-safe")
        assert r["ok"] is False
        assert r["codigo"] == "FOCUS_PARSE_ERROR"
        assert r["nsu_avancou"] is False

    def test_classificador_trata_todos_os_focus_codigos_como_erro(self):
        """Regressao explicita do defeito: classificador de acao no
        FiscalOne app.py trata todos os codigos FOCUS_* como ERRO tecnico,
        nao como SEM_DOCUMENTO."""
        from app import _classificar_acao_gov_fetch, _CODIGO_TECNICO_ERRO
        focus_codigos = [
            "FOCUS_TIMEOUT", "FOCUS_UNAVAILABLE", "FOCUS_HTTP_ERROR",
            "FOCUS_BAD_REQUEST", "FOCUS_AUTH_ERROR", "FOCUS_FORBIDDEN",
            "FOCUS_RATE_LIMIT", "FOCUS_SERVER_ERROR", "FOCUS_PARSE_ERROR",
            "FOCUS_SCHEMA_ERROR", "FOCUS_TOKEN_AUSENTE",
            "FOCUS_NFSE_NAO_HABILITADA", "FOCUS_TIPO_NAO_SUPORTADO",
        ]
        for cod in focus_codigos:
            assert cod in _CODIGO_TECNICO_ERRO, \
                f"{cod} deveria estar em _CODIGO_TECNICO_ERRO"
            acao = _classificar_acao_gov_fetch(
                {"ok": False, "codigo": cod}, docs_count=0)
            assert acao == "ERRO", \
                f"{cod} deveria ser ERRO (foi {acao})"


# ── 8. Nao expor dados sensiveis / SEM ERROS de mapper com chave ─────────
class TestSanidadeErros:

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_de_mapper_nao_vaza_token_ou_cnpj_completo(
            self, mock_get, provider):
        item_ruim = {"documento_emitente": "22222222000181", "versao": 7}
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "7"},
                              json_data=[item_ruim])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        payload = str(r)
        assert "abcdef123456" not in payload  # token
        # CNPJ completo do tenant sintetico nao deve aparecer no
        # envelope de erro (aparece nos params do request, mas nao no
        # `erros[]`). Reforca a sanitizacao pos-2026-07-24.
        for e in r["erros"]:
            assert "11111111000191" not in str(e)


# ── 9. Gap sem versao (correcao 2026-07-24 · rev.2) ──────────────────
class TestGapSemVersao:
    """Item invalido cuja versao nao pode ser extraida — o cursor NAO
    pode avancar (nao ha como bloquear 'antes do item' sem inventar
    versao). Envelope reporta `gap_sem_versao=True`, cursor fica em
    versao_entrada, has_more=True, nsu_avancou=False."""

    @patch("providers.focusnfe_provider.requests.get")
    def test_item_sem_versao_trava_cursor_em_versao_entrada(
            self, mock_get, provider):
        # Item invalido sem `versao` extraivel (versao=0/None/faltando)
        # E sem `chave_nfe` → mapper levanta ValueError (chave ausente),
        # pre-mapper `versao_pre=None` → `erros_sem_versao=1`.
        item_sem_versao = {"documento_emitente": "22222222000181",
                            "numero": "1"}
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "500"},
                              json_data=[item_sem_versao])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-safe")
        assert r["ok"] is True
        assert r["gap_sem_versao"] is True
        assert r["erros_sem_versao"] == 1
        # X-Max-Version=500, mas cursor NAO avanca — fica em 42.
        assert r["cursor_seguro"] == "42"
        assert r["ultimo_nsu"] == "42"
        assert r["nsu_avancou"] is False
        assert r["has_more"] is True
        # Erro carrega marcador `sem_versao=True`, sem versao numerica.
        assert len(r["erros"]) == 1
        assert r["erros"][0]["sem_versao"] is True
        assert "versao" not in r["erros"][0]

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_com_versao_e_erro_sem_versao_prevalece_gap_sem_versao(
            self, mock_get, provider):
        # Mix: um erro com `versao=5` (bloquearia cursor em 4) + um
        # erro sem `versao`. O gap sem versao PREVALECE — cursor fica
        # em versao_entrada (0), NAO em min(4).
        item_com_versao = {"versao": 5, "documento_emitente": "22222222000181",
                            "numero": "1"}  # sem chave → mapper falha
        item_sem_versao = {"numero": "2"}   # sem chave e sem versao
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "10"},
                              json_data=[item_com_versao, item_sem_versao])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert r["gap_sem_versao"] is True
        assert r["cursor_seguro"] == "0"   # NAO 4 (nao aplica min(versao)-1)
        assert r["nsu_avancou"] is False
        assert r["has_more"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_sanitizado_nao_contem_item_bruto_nem_texto_exc(
            self, mock_get, provider):
        # Cenario originalmente cobria mapper ValueError. Com rev.3, o
        # item cai antes no bail-out pre-mapper (`FOCUS_ITEM_VERSAO_
        # INVALIDA`) porque nao tem `versao`. A sanitizacao continua
        # sendo garantida: nada de payload sensivel do item entra no
        # envelope de erro.
        # Para preservar tambem a cobertura do mapper ValueError,
        # ver `TestVersaoInvalidaBlockCursor.
        # test_erro_sanitizado_nao_vaza_versao_bruta` (rev.3).
        item_ruim_com_dado_sensivel = {
            "documento_emitente": "22222222000181",
            "senha_fiscal": "SUPER_SENHA_QUE_NAO_PODE_VAZAR",
            "chave_nfe": "",
            # sem 'versao' → cai em FOCUS_ITEM_VERSAO_INVALIDA
        }
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "10"},
                              json_data=[item_ruim_com_dado_sensivel])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-safe")
        assert len(r["erros"]) == 1
        e = r["erros"][0]
        e_str = str(e)
        assert "SUPER_SENHA_QUE_NAO_PODE_VAZAR" not in e_str
        assert "senha_fiscal" not in e_str
        # Codigo canonico + mensagem sem payload bruto.
        assert e["codigo"] in {"FOCUS_ITEM_VERSAO_INVALIDA",
                                "FOCUS_ITEM_INVALIDO"}
        assert e["sem_versao"] is True


# ── 10. Varredura anti-CNPJ real (item C do prompt de revisao) ────────
# Lista dos CNPJs proibidos e' construida a partir de digitos individuais
# para evitar que a propria lista faca este teste falhar.
_PROIBIDOS_CNPJ = [
    "".join(d) for d in (
        list("07219398") + list("000109"),
        list("03080168") + list("000142"),
        list("14339031") + list("000186"),
        list("01136600") + list("000189"),
    )
]


def test_cnpj_real_removido_dos_novos_testes():
    """Regressao — este arquivo de teste NAO pode conter os CNPJs reais
    que foram usados na entrega anterior."""
    from pathlib import Path
    src = Path(__file__).read_text(encoding="utf-8")
    for cnpj in _PROIBIDOS_CNPJ:
        assert cnpj not in src, \
            f"CNPJ real reintroduzido em {Path(__file__).name}"


# ── 11. Rev.3 · validacao estrita de `versao` FocusNFe ────────────────
# O bloqueador remanescente da rev.2 era: mappers NF-e/NFS-e normalizam
# versao invalida para 0 e devolvem documento valido. Sem excecao,
# `erros_sem_versao` nao incrementava, `gap_sem_versao` ficava False, e
# cursor podia avancar ate X-Max-Version.
# Fix rev.3: helper `_versao_focus_valida` + validacao pos-mapper em
# `gov_fetch`. Item cuja versao devolvida pelo mapper nao seja int > 0
# vira `FOCUS_ITEM_VERSAO_INVALIDA`.
from providers.focusnfe_provider import _versao_focus_valida


class TestVersaoFocusValidaHelper:
    """Contrato canonico do helper `_versao_focus_valida`."""

    @pytest.mark.parametrize("raw", [
        None,
        "",
        "  ",
        0,
        "0",
        -1,
        -42,
        "-3",
        True,
        False,
        1.5,
        "1.5",
        "abc",
        "42a",
        "a42",
        [],
        [42],
        {},
        {"v": 42},
        (42,),
    ])
    def test_valores_invalidos_devolvem_none(self, raw):
        assert _versao_focus_valida(raw) is None, \
            f"{raw!r} deveria ser INVALIDA"

    @pytest.mark.parametrize("raw,esperado", [
        (1, 1),
        (42, 42),
        (999999, 999999),
        ("1", 1),
        ("42", 42),
        ("  42  ", 42),
        ("999999999999", 999999999999),
    ])
    def test_valores_validos_normalizados(self, raw, esperado):
        assert _versao_focus_valida(raw) == esperado


def _item_nfe_versao_arbitraria(versao_raw, chave="A" * 44):
    """Item NF-e onde o campo `versao` recebe valor arbitrario (para
    forcar a validacao pos-mapper). Outros campos sinteticos."""
    return {
        "chave_nfe":                 chave,
        "numero":                    "1",
        "serie":                     "1",
        "data_emissao":              "2026-07-20T10:00:00-03:00",
        "documento_emitente":        "22222222000181",
        "cnpj_destinatario":         "11111111000191",
        "nome_emitente":             "Emit Sintetico",
        "valor_total":               "100.00",
        "valor_icms":                "18.00",
        "situacao":                  "autorizada",
        "manifestacao_destinatario": "ciencia_operacao",
        "nfe_completa":              True,
        "versao":                    versao_raw,
        "tipo_nfe":                  "entrada",
        "protocolo":                 "10100001",
    }


def _item_nfse_versao_arbitraria(versao_raw, chave="B" * 44):
    """Item NFS-e no formato oficial FocusNFe com `versao` arbitraria."""
    return {
        "chave":              chave,
        "versao":             versao_raw,
        "status":             1,
        "numero":             "1001",
        "serie":              "1",
        "codigo_verificacao": "SINT1234",
        "data_emissao":       "2026-07-15T10:00:00-03:00",
        "competencia":        "2026-07",
        "prestador":          {"razao_social": "Prestador Sintetico",
                                "nome_fantasia": "Prestador",
                                "inscricao_municipal": "12345",
                                "cnpj": "22222222000181"},
        "tomador":            {"cnpj": "11111111000191",
                                "razao_social": "Tomador Sintetico"},
        "servicos":           {"valor_servicos": "1500.00",
                                "valor_iss": "75.00",
                                "iss_retido": False,
                                "valor_liquido": "1425.00",
                                "discriminacao": "Servico sintetico."},
        "url":                "https://exemplo.local/nfse/1",
        "url_xml":            "",
    }


# Valores invalidos que serao aplicados a ambos NF-e e NFS-e.
_VERSAO_INVALIDA_PARAMS = [
    pytest.param(None,   id="ausente_None"),
    pytest.param("",     id="string_vazia"),
    pytest.param(0,      id="zero_int"),
    pytest.param("0",    id="zero_str"),
    pytest.param(-1,     id="negativo_int"),
    pytest.param("-3",   id="negativo_str"),
    pytest.param(True,   id="booleano_true"),
    pytest.param(False,  id="booleano_false"),
    pytest.param(1.5,    id="decimal"),
    pytest.param("1.5",  id="decimal_str"),
    pytest.param("abc",  id="texto_inconvertivel"),
    pytest.param([42],   id="lista"),
    pytest.param({"v": 42}, id="dict"),
]


class TestVersaoInvalidaBlockCursor:
    """Cobre os 15 cenarios exigidos pelo prompt rev.3, aplicados a NF-e
    e a NFS-e no fluxo `gov_fetch`."""

    @patch("providers.focusnfe_provider.requests.get")
    @pytest.mark.parametrize("versao_raw", _VERSAO_INVALIDA_PARAMS)
    def test_nfe_versao_invalida_bloqueia_cursor(
            self, mock_get, provider, versao_raw):
        item = _item_nfe_versao_arbitraria(versao_raw)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "999"},
                              json_data=[item])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-r3")
        assert r["ok"] is True
        # Documento NAO entra no batch (foi para erros[]).
        assert r["documentos"] == []
        assert len(r["erros"]) == 1
        e = r["erros"][0]
        # A validacao pos-mapper produz `FOCUS_ITEM_VERSAO_INVALIDA`;
        # se o mapper ja levantar (ex.: valor invalido causou ValueError),
        # cai em `FOCUS_ITEM_INVALIDO` com `sem_versao=True`. Ambos sao
        # aceitaveis — o efeito no cursor e' identico.
        assert e["codigo"] in {"FOCUS_ITEM_VERSAO_INVALIDA",
                                "FOCUS_ITEM_INVALIDO"}
        assert e["sem_versao"] is True
        # Nenhuma versao numerica no erro sem_versao.
        assert e.get("versao") is None
        # `versao` bruto NAO vaza no envelope (checagem apenas para
        # representacoes nao-vazias; string vazia trivialmente "in").
        _repr = str(versao_raw)
        if _repr and versao_raw is not None and len(_repr) >= 2:
            assert _repr not in e["erro"], \
                f"valor bruto {_repr!r} vazou em {e['erro']!r}"
        # Contagem: exatamente 1 (sem contagem dupla).
        assert r["erros_sem_versao"] == 1
        # Bloqueio integral do cursor.
        assert r["gap_sem_versao"] is True
        assert r["cursor_seguro"] == "42"
        assert r["nsu_avancou"] is False
        assert r["has_more"] is True
        # X-Max-Version=999 e' IGNORADO.
        assert r["ultimo_nsu"] == "42"

    @patch("providers.focusnfe_provider.requests.get")
    @pytest.mark.parametrize("versao_raw", _VERSAO_INVALIDA_PARAMS)
    def test_nfse_versao_invalida_bloqueia_cursor(
            self, mock_get, provider, versao_raw):
        item = _item_nfse_versao_arbitraria(versao_raw)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "999"},
                              json_data=[item])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfse", "ultimo_nsu": "42"},
            "fo-r3")
        assert r["ok"] is True
        assert r["documentos"] == []
        assert len(r["erros"]) == 1
        e = r["erros"][0]
        assert e["codigo"] in {"FOCUS_ITEM_VERSAO_INVALIDA",
                                "FOCUS_ITEM_INVALIDO"}
        assert e["sem_versao"] is True
        assert r["erros_sem_versao"] == 1
        assert r["gap_sem_versao"] is True
        assert r["cursor_seguro"] == "42"
        assert r["nsu_avancou"] is False
        assert r["has_more"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_nfe_sem_campo_versao_bloqueia_cursor(
            self, mock_get, provider):
        # Item sem chave `versao` no dict — o mapper cai em v=0 e o pos-
        # mapper bloqueia. Cobre o caso "ausente" (chave nao presente).
        item = _item_nfe_versao_arbitraria(None)
        item.pop("versao")
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "999"},
                              json_data=[item])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "42"},
            "fo-r3")
        assert r["documentos"] == []
        assert r["gap_sem_versao"] is True
        assert r["erros_sem_versao"] == 1
        assert r["cursor_seguro"] == "42"

    @pytest.mark.parametrize("versao_raw,esperado_int", [
        (1, 1), (42, 42), (999999, 999999),
        ("1", 1), ("42", 42), ("999999", 999999),
    ])
    @patch("providers.focusnfe_provider.requests.get")
    def test_nfe_versao_valida_nao_regride(
            self, mock_get, provider, versao_raw, esperado_int):
        item = _item_nfe_versao_arbitraria(versao_raw)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": str(esperado_int)},
                              json_data=[item])
        # Item valido + NF-e completa: baixa XML.
        xml_ok = _mock_resp(status=200, text="<nfeProc/>")
        mock_get.side_effect = [listagem, xml_ok]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-r3-ok")
        assert len(r["documentos"]) == 1
        assert r["erros"] == []
        assert r["gap_sem_versao"] is False
        assert r["erros_sem_versao"] == 0
        assert r["cursor_seguro"] == str(esperado_int)
        assert r["nsu_avancou"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_item_invalido_seguido_por_valido_bloqueia_tudo(
            self, mock_get, provider):
        # Prompt cenario 13: mesmo com item valido depois, gap_sem_versao
        # PREVALECE. X-Max-Version superior e' ignorado.
        item_ruim = _item_nfe_versao_arbitraria(0, chave="R" * 44)
        item_bom = _item_nfe_versao_arbitraria(100, chave="B" * 44)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "100"},
                              json_data=[item_ruim, item_bom])
        # Item bom vai tentar baixar XML — mas nao deve, pois gap_sem_versao
        # trava o cursor em versao_entrada.
        mock_get.side_effect = [listagem,
                                _mock_resp(status=200, text="<nfeProc/>")]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "5"},
            "fo-r3-mix")
        # 1 documento entrou (o valido), 1 erro (o invalido).
        assert len(r["documentos"]) == 1
        assert len(r["erros"]) == 1
        assert r["gap_sem_versao"] is True
        # Cursor NAO avanca alem de versao_entrada, apesar de item bom.
        assert r["cursor_seguro"] == "5"
        assert r["nsu_avancou"] is False
        assert r["has_more"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_valido_seguido_por_invalido_bloqueia_tudo(
            self, mock_get, provider):
        # Prompt cenario 14 — ordem inversa.
        item_bom = _item_nfe_versao_arbitraria(50, chave="C" * 44)
        item_ruim = _item_nfe_versao_arbitraria("", chave="D" * 44)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "50"},
                              json_data=[item_bom, item_ruim])
        mock_get.side_effect = [listagem,
                                _mock_resp(status=200, text="<nfeProc/>")]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "10"},
            "fo-r3-mix2")
        assert len(r["documentos"]) == 1
        assert r["gap_sem_versao"] is True
        assert r["cursor_seguro"] == "10"
        assert r["nsu_avancou"] is False

    @patch("providers.focusnfe_provider.requests.get")
    def test_item_versao_invalida_nao_dispara_get_xml(
            self, mock_get, provider):
        # Prompt cenario 15: item invalido nao tem GET individual de XML
        # solicitado. Mock com apenas 1 resposta (listagem); um GET extra
        # falharia com StopIteration.
        item_ruim = _item_nfe_versao_arbitraria(0, chave="Z" * 44)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "10"},
                              json_data=[item_ruim])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-r3-noxml")
        assert r["documentos"] == []
        assert r["xmls_baixados"] == 0
        # Apenas 1 chamada HTTP — a listagem. Nenhum GET individual.
        assert mock_get.call_count == 1

    @patch("providers.focusnfe_provider.requests.get")
    def test_contagem_erros_sem_versao_nao_duplica(
            self, mock_get, provider):
        # 3 itens invalidos: cada um incrementa `erros_sem_versao`
        # exatamente 1 vez (nao duplica).
        docs = [
            _item_nfe_versao_arbitraria(0,     chave="X" * 44),
            _item_nfe_versao_arbitraria("",    chave="Y" * 44),
            _item_nfe_versao_arbitraria(None,  chave="W" * 44),
        ]
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "100"},
                              json_data=docs)
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-r3-count")
        assert r["erros_sem_versao"] == 3
        assert len(r["erros"]) == 3
        assert r["gap_sem_versao"] is True

    @patch("providers.focusnfe_provider.requests.get")
    def test_erro_sanitizado_nao_vaza_versao_bruta(
            self, mock_get, provider):
        # Valor bruto sensivel na versao — envelope de erro NAO pode
        # devolver o valor literal (nem `{"segredo": "FISCAL"}` nem
        # a string).
        sentinela = "SENTINELA_QUE_NAO_PODE_VAZAR_1234"
        item = _item_nfe_versao_arbitraria({sentinela: "42"},
                                            chave="Q" * 44)
        listagem = _mock_resp(status=200,
                              headers={"X-Max-Version": "10"},
                              json_data=[item])
        mock_get.side_effect = [listagem]
        r = provider.gov_fetch(
            {"cnpj": "11111111000191", "tipo": "nfe", "ultimo_nsu": "0"},
            "fo-r3-sanit")
        payload = str(r["erros"])
        assert sentinela not in payload
        # Mensagem tem forma canonica (nao contem `str(item)`).
        for e in r["erros"]:
            assert sentinela not in e["erro"]
