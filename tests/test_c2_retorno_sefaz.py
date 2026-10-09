"""Retorno completo do C2 pela documentação oficial da Focus (09/10/2026).

Primeiro CT-e real em homologação (OC-0014 da R1): a SEFAZ rejeitou e a tela mostrou
"Rejeitado pela Focus" sem motivo. A consulta da Focus traz `status_sefaz` e
`mensagem_sefaz` (doc consultar_cte_cte_os), que o envelope descartava. A mesma página
mostra que o CT-e autorizado vem com `chave` ("CTe" + 44 dígitos), `caminho_xml`,
`caminho_dacte` e, com `completa=1`, o objeto `protocolo`. O C2 procurava `chave_cte` e
`caminho_xml_nota_fiscal`, nomes de busca de quando a página não abria.
"""
import json

import pytest

from tests.test_emissao_homologacao import (  # noqa: F401  (fixtures)
    CHAVE, PDF_BYTES, REF, XML_BYTES, _configurar_focus, _corpo, _h, _json, _payload,
    _sem_violacao, cli, focus)


def _consultar(cli_, tipo="cte"):
    return _json(cli_.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo()))


def test_rejeicao_traz_codigo_e_motivo_da_sefaz(cli, focus):
    _configurar_focus(focus, "cte")
    focus.respostas[("GET", "cte")] = (200, {
        "cnpj_emitente": "14339031000186", "ref": REF, "status": "erro_autorizacao",
        "status_sefaz": "704", "mensagem_sefaz": "Rejeição: Data de emissão atrasada",
        "erros": [{"codigo": "", "mensagem": "Data de emissão atrasada"}]})
    j = _consultar(cli)
    assert j["status"] == "erro_autorizacao" and j["ok"] is False
    assert j["status_sefaz"] == "704"
    assert j["mensagem_sefaz"] == "Rejeição: Data de emissão atrasada"
    assert j["erros"][0]["mensagem"] == "Data de emissão atrasada"
    _sem_violacao(focus)


def test_rejeicao_vai_para_o_log_com_cstat_e_motivo(cli, focus, capsys):
    _configurar_focus(focus, "cte")
    focus.respostas[("GET", "cte")] = (200, {
        "status": "erro_autorizacao", "status_sefaz": "539",
        "mensagem_sefaz": "Rejeição: Duplicidade de CT-e, com diferença na chave"})
    _consultar(cli)
    linhas = [json.loads(l) for l in capsys.readouterr().out.splitlines()
              if l.startswith("{") and "c2_consultar_cte" in l]
    assert linhas, "sem log do c2_consultar_cte"
    assert linhas[-1]["cstat"] == "539"
    assert "Duplicidade de CT-e" in linhas[-1]["erro"]


def test_autorizado_no_formato_da_doc_traz_chave_xml_e_protocolo(cli, focus):
    _configurar_focus(focus, "cte")
    focus.respostas[("GET", "cte")] = (200, {
        "cnpj_emitente": "14339031000186", "ref": REF, "status": "autorizado",
        "status_sefaz": "100", "mensagem_sefaz": "Autorizado o uso do CT-e",
        "chave": "CTe" + CHAVE, "numero": "2", "serie": "1", "modelo": "57",
        "caminho_xml": "/arquivos/cte.xml", "caminho_dacte": "/arquivos/cte.pdf",
        "protocolo": {"protocolo": "135260000000099", "motivo": "Autorizado o uso do CT-e",
                      "data_recebimento": "2026-10-09T17:10:00-03:00"}})
    j = _consultar(cli)
    assert j["status"] == "autorizado" and j["ok"] is True
    assert j["chave"] == CHAVE, "chave sem o prefixo CTe, 44 dígitos"
    assert j["protocolo"] == "135260000000099"
    assert j["status_sefaz"] == "100"
    assert "xml_base64" in j and "pdf_base64" in j
    consulta = [c for c in focus.chamadas_focus() if c["method"] == "GET"][-1]
    assert consulta["query"].get("completa") == ["1"], "consulta do CT-e com completa=1"
    _sem_violacao(focus)


def test_nomes_antigos_seguem_aceitos(cli, focus):
    # `_configurar_focus` usa chave_cte/caminho_xml_nota_fiscal/protocolo texto.
    _configurar_focus(focus, "cte")
    j = _consultar(cli)
    assert j["status"] == "autorizado" and j["chave"] == CHAVE
    assert j["protocolo"] == "135260000000001" and "xml_base64" in j


def test_pre_validacao_traz_cada_erro_da_lista(cli, focus):
    _configurar_focus(focus, "cte")
    focus.respostas[("POST", "cte")] = (400, {
        "codigo": "requisicao_invalida", "mensagem": "Erros de validação",
        "erros": [{"numero_correcao": 1,
                   "erros": ["campo cfop inválido", "campo uf_inicio obrigatório"]}]})
    r = cli.post("/fiscal/cte", headers=_h(), json=_corpo(payload=_payload("cte")))
    j = _json(r)
    assert j["status"] == "erro_autorizacao" and j["ok"] is False
    textos = [e["mensagem"] for e in j["erros"]]
    assert "Erros de validação" in textos
    assert "campo cfop inválido" in textos and "campo uf_inicio obrigatório" in textos
    _sem_violacao(focus)


@pytest.mark.parametrize("bruta", ["NFe" + CHAVE[:-1], "", None])
def test_chave_fora_de_44_digitos_nao_e_inventada(cli, focus, bruta):
    _configurar_focus(focus, "cte")
    corpo = {"status": "erro_autorizacao", "status_sefaz": "999", "mensagem_sefaz": "x"}
    if bruta is not None:
        corpo["chave"] = bruta
    focus.respostas[("GET", "cte")] = (200, corpo)
    j = _consultar(cli)
    assert j.get("chave") in (None, "", bruta)
