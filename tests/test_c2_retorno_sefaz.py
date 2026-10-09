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


# ── Download do autorizado por redirect ao storage (09/10/2026) ───────────────
# O CT-e nº 4 da OC-0014 foi autorizado (protocolo 135260007168424) e ficou sem XML:
# a Focus serve o arquivo por redirect ao storage, e o C2 recusava todo redirect.
STORAGE = "storage.focus.teste"


def _com_storage(focus, permitido=True):
    from tests.test_emissao_homologacao import _resp
    _configurar_focus(focus, "cte")
    for p, mime in (("/arquivos/cte.xml", "xml"), ("/arquivos/cte.pdf", "pdf")):
        focus.arquivos[p] = (302, b"", {"Location": f"https://{STORAGE}/{mime}?assinatura=1"})
    original = focus.responder

    def responder(rec):
        if rec["host"] == STORAGE:
            assert "authorization" not in rec["headers"], "2º GET sem credencial"
            corpo = XML_BYTES if "xml" in rec["path"] else PDF_BYTES
            return _resp(200, corpo, rec["url"])
        return original(rec)
    focus.responder = responder


def test_xml_segue_redirect_ao_storage_permitido(cli, focus, monkeypatch):
    import base64
    monkeypatch.setenv("FISCALONE_XML_REDIRECT_HOSTS", STORAGE)
    _com_storage(focus)
    j = _consultar(cli)
    assert j["status"] == "autorizado"
    assert base64.b64decode(j["xml_base64"]) == XML_BYTES
    assert base64.b64decode(j["pdf_base64"]) == PDF_BYTES
    assert j["arquivos"] == {"xml": "ok", "pdf": "ok"}
    for c in focus.calls:
        assert c["allow_redirects"] is False


def test_storage_fora_da_allowlist_nao_e_seguido_e_diz_por_que(cli, focus, monkeypatch):
    monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
    _com_storage(focus)
    j = _consultar(cli)
    assert "xml_base64" not in j
    assert j["arquivos"]["xml"] == "redirect_nao_permitido"
    assert STORAGE not in json.dumps(j), "o diagnóstico não expõe o host"
    assert all(c["host"] != STORAGE for c in focus.calls)


def test_log_do_autorizado_traz_o_resultado_do_download(cli, focus, monkeypatch, capsys):
    monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
    _com_storage(focus)
    _consultar(cli)
    linhas = [json.loads(l) for l in capsys.readouterr().out.splitlines()
              if l.startswith("{") and "c2_consultar_cte" in l]
    assert linhas and linhas[-1]["acao"] == "xml=redirect_nao_permitido;pdf=redirect_nao_permitido"
    assert STORAGE not in json.dumps(linhas[-1])


# ── Arquivo como URL completa no storage oficial (doc consultar_cte_cte_os) ───
S3 = "focusnfe.s3.sa-east-1.amazonaws.com"


def test_autorizado_baixa_xml_e_dacte_do_s3_da_focus_sem_token(cli, focus, monkeypatch):
    import base64
    from tests.test_emissao_homologacao import TK_FOCUS, _resp
    monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
    _configurar_focus(focus, "cte")
    focus.respostas[("GET", "cte")] = (200, {
        "status": "autorizado", "status_sefaz": "100", "mensagem_sefaz": "Autorizado o uso do CT-e",
        "chave": "CTe" + CHAVE, "numero": "4", "serie": "1",
        "caminho_xml": f"https://{S3}/arquivos_development/XMLs/{CHAVE}-cte.xml",
        "caminho_dacte": f"https://{S3}/arquivos_development/DACTEs/{CHAVE}.pdf"})
    original = focus.responder

    def responder(rec):
        if rec["host"] == S3:
            assert "authorization" not in rec["headers"], "o token nunca vai ao storage"
            assert TK_FOCUS not in json.dumps(rec["headers"])
            return _resp(200, XML_BYTES if rec["path"].endswith(".xml") else PDF_BYTES, rec["url"])
        return original(rec)
    focus.responder = responder
    j = _consultar(cli)
    assert base64.b64decode(j["xml_base64"]) == XML_BYTES
    assert base64.b64decode(j["pdf_base64"]) == PDF_BYTES
    assert j["arquivos"] == {"xml": "ok", "pdf": "ok"} and j["chave"] == CHAVE


@pytest.mark.parametrize("url", [
    f"http://{S3}/x.xml",                       # sem https
    "https://focusnfe.s3.sa-east-1.amazonaws.com.evil.example/x.xml",
    f"https://user:senha@{S3}/x.xml",
    f"https://{S3}:8443/x.xml",
    "https://outro-bucket.s3.amazonaws.com/x.xml",
])
def test_url_fora_do_storage_oficial_nao_e_baixada(cli, focus, monkeypatch, url):
    monkeypatch.delenv("FISCALONE_XML_REDIRECT_HOSTS", raising=False)
    _configurar_focus(focus, "cte")
    focus.respostas[("GET", "cte")] = (200, {"status": "autorizado", "chave": CHAVE,
                                             "caminho_xml": url})
    j = _consultar(cli)
    assert "xml_base64" not in j
    assert j["arquivos"]["xml"] == "caminho_fora_da_homologacao"
    assert focus.violacoes == []
