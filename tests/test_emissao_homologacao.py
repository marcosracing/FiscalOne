"""Aceite do F2 (EMISSOR-FISCAL-F2-20261006): contrato C2 do FiscalOne.

Emissao, consulta e cancelamento de CT-e e NFS-e Nacional SOMENTE em
homologacao, via FocusNFe, com `requests` dublado.

Fontes da Focus consultadas em 2026-10-06:
  - https://doc.focusnfe.com.br/reference/emitir_cte
        POST /v2/cte?ref=<ref>, 202 Accepted (confirmado)
  - https://doc.focusnfe.com.br/reference/emitir_dps_nacional
        POST /v2/nfsen?ref=<ref>, 202 Accepted (confirmado)
  - https://doc.focusnfe.com.br/reference/consultar_nfse_nacional
        GET /v2/nfsen/<ref>; campos status, numero, url_danfse,
        caminho_xml_nota_fiscal (confirmado)
  - https://doc.focusnfe.com.br/reference/cancelar_nfse_nacional
        DELETE /v2/nfsen/<ref>, corpo opcional {justificativa}, resposta
        sincrona 200 {status: cancelado | erro_cancelamento} (confirmado)
  - CT-e consulta GET /v2/cte/<ref> e cancelamento DELETE /v2/cte/<ref>:
        confirmados apenas por resultado de busca (a pagina de referencia
        nao abriu); os testes afirmam host e metodo e que o caminho comeca
        em /v2/cte e termina na ref, sem fixar mais que isso.
  - Campos de arquivo do CT-e: caminho_xml_nota_fiscal, caminho_dacte e
    chave_cte, pela documentacao da API v2 da Focus (busca em 06/10/2026,
    focusnfe.com.br/api/doc e campos.focusnfe.com.br; a pagina de
    referencia do CT-e nao abriu). Mesmo nome de XML do NF-e e da NFS-e.
  - Autenticacao: HTTP Basic, token como usuario e senha vazia.

Dublê: `requests.sessions.Session.request` e substituido; toda chamada e
registrada e qualquer host diferente do de homologacao vira violacao
(afirmada ao fim de cada teste). Token fictício, sem rede.
"""
import base64
import importlib
import importlib.util
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

HOMOLOG = "https://homologacao.focusnfe.com.br"
HOMOLOG_HOST = "homologacao.focusnfe.com.br"
PROD_URL = "https://api.focusnfe.com.br"
M2M = "m2m-teste-f2"
TK_FOCUS = "tok-homolog-teste"
TOKEN_GLOBAL = "tok-global-NAO-USAR"
CNPJ = "07219398000109"
REF = "bipe-123_cte-1"
CHAVE = "35261007219398000109570010000001231000001231"
XML_BYTES = "<?xml version='1.0'?><cteProc><x>Ação</x></cteProc>".encode("utf-8")
PDF_BYTES = b"%PDF-1.4 dacte-homolog-teste"

TIPOS = ["cte", "nfsen"]
TODAS_AS_ROTAS = [
    ("POST", "/fiscal/cte"),
    ("POST", "/fiscal/cte/" + REF + "/consultar"),
    ("DELETE", "/fiscal/cte/" + REF),
    ("POST", "/fiscal/nfsen"),
    ("POST", "/fiscal/nfsen/" + REF + "/consultar"),
    ("DELETE", "/fiscal/nfsen/" + REF),
]


# ── dublê do requests ─────────────────────────────────────────────────────
class Focus:
    """Servidor Focus falso + registro de chamadas."""

    def __init__(self):
        self.calls = []
        self.violacoes = []
        self.respostas = {}   # (METODO, tipo) -> (status, dict|bytes)
        self.arquivos = {}    # path -> (status, bytes, headers)
        self.token_basic = TK_FOCUS

    # -- registro --
    def call(self, metodo, url, kw):
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        params = kw.get("params")
        if isinstance(params, dict):
            for k, v in params.items():
                q.setdefault(k, []).append(str(v))
        headers = {k.lower(): v for k, v in (kw.get("headers") or {}).items()}
        body_json = kw.get("json")
        if body_json is None and kw.get("data"):
            data = kw["data"]
            try:
                body_json = json.loads(data if isinstance(data, (str, bytes))
                                       else str(data))
            except (ValueError, TypeError):
                body_json = None
        rec = {
            "method": metodo.upper(), "url": url, "host": parts.hostname,
            "scheme": parts.scheme, "path": parts.path, "query": q,
            "headers": headers, "json": body_json,
            "allow_redirects": kw.get("allow_redirects", True),
            "auth": kw.get("auth"),
        }
        self.calls.append(rec)
        return rec

    # -- respostas --
    def responder(self, rec):
        if rec["host"] != HOMOLOG_HOST or rec["scheme"] != "https":
            self.violacoes.append(rec["url"])
            raise requests.exceptions.ConnectionError("host fora da homologacao")
        m = re.match(r"^/v2/(cte|nfsen)(?:/([^/]+))?/?$", rec["path"])
        if m:
            tipo = m.group(1)
            chave = (rec["method"], tipo)
            if chave in self.respostas:
                st, corpo = self.respostas[chave]
                return _resp(st, corpo, rec["url"])
            return _resp(500, {"codigo": "dublê_sem_resposta"}, rec["url"])
        if rec["path"] in self.arquivos:
            st, corpo, hdr = self.arquivos[rec["path"]]
            return _resp(st, corpo, rec["url"], hdr)
        return _resp(404, {"codigo": "nao_encontrado"}, rec["url"])

    # -- consultas --
    def chamadas_focus(self):
        return [c for c in self.calls if c["path"].startswith("/v2/")]

    def downloads(self):
        return [c for c in self.calls if not c["path"].startswith("/v2/")]

    def urls(self):
        return [c["url"] for c in self.calls]


def _resp(status, corpo, url, headers=None):
    r = requests.Response()
    r.status_code = status
    r.url = url
    if isinstance(corpo, (dict, list)):
        r._content = json.dumps(corpo).encode("utf-8")
        r.headers["Content-Type"] = "application/json"
    else:
        r._content = corpo
    r.encoding = "utf-8"
    for k, v in (headers or {}).items():
        r.headers[k] = v
    return r


def _configurar_focus(focus, tipo):
    """Respostas padrao: emitir 202, consultar autorizado, cancelar 200."""
    arq_xml, arq_pdf = f"/arquivos/{tipo}.xml", f"/arquivos/{tipo}.pdf"
    focus.arquivos[arq_xml] = (200, XML_BYTES, {"Content-Type": "application/xml"})
    focus.arquivos[arq_pdf] = (200, PDF_BYTES, {"Content-Type": "application/pdf"})
    if tipo == "cte":
        autorizado = {
            "status": "autorizado", "chave_cte": CHAVE, "numero": "123",
            "serie": "1", "protocolo": "135260000000001",
            "caminho_xml_nota_fiscal": arq_xml, "caminho_dacte": arq_pdf,
        }
        cancelado = {"status": "cancelado", "caminho_xml_cancelamento": arq_xml}
    else:
        autorizado = {
            "status": "autorizado", "numero": "77", "ref": REF,
            "caminho_xml_nota_fiscal": arq_xml,
            "url_danfse": HOMOLOG + arq_pdf,
        }
        cancelado = {"status": "cancelado"}
    focus.respostas[("POST", tipo)] = (202, {"status": "processando_autorizacao",
                                             "ref": REF})
    focus.respostas[("GET", tipo)] = (200, autorizado)
    focus.respostas[("DELETE", tipo)] = (200, cancelado)


@pytest.fixture
def focus(monkeypatch):
    f = Focus()

    def fake_request(self, method, url, params=None, data=None, headers=None,
                     cookies=None, files=None, auth=None, timeout=None,
                     allow_redirects=True, proxies=None, hooks=None,
                     stream=None, verify=None, cert=None, json=None):
        kw = {"params": params, "data": data, "headers": headers, "auth": auth,
              "allow_redirects": allow_redirects, "json": json}
        rec = f.call(method, url, kw)
        return f.responder(rec)

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)
    return f


@pytest.fixture
def cli(monkeypatch, focus):
    """App em homologacao, M2M configurado, sem token/URL globais."""
    monkeypatch.setenv("FISCALONE_AMBIENTE", "homologacao")
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", M2M)
    monkeypatch.setenv("FISCAL_PROVIDER", "sefaz")
    monkeypatch.setenv("GOV_TLS_INSECURE", "0")
    for v in ("FOCUSNFE_BASE_URL", "FOCUSNFE_AMBIENTE", "FOCUSNFE_TOKEN",
              "FISCALONE_ENABLE_PRODUCAO", "MAPONE_FISCAL_PRODUCAO_READY",
              "FISCALONE_DFE_RECEBIDO_ONLY"):
        monkeypatch.delenv(v, raising=False)
    import app
    importlib.reload(app)
    return app.app.test_client()


def _producao_hostil(monkeypatch):
    """Tres flags de producao + overrides de producao + token global."""
    monkeypatch.setenv("FISCALONE_AMBIENTE", "producao")
    monkeypatch.setenv("FISCALONE_ENABLE_PRODUCAO", "1")
    monkeypatch.setenv("MAPONE_FISCAL_PRODUCAO_READY", "1")
    monkeypatch.setenv("FISCALONE_DFE_RECEBIDO_ONLY", "1")
    monkeypatch.setenv("FOCUSNFE_BASE_URL", PROD_URL + "/v2")
    monkeypatch.setenv("FOCUSNFE_AMBIENTE", "producao")
    monkeypatch.setenv("FOCUSNFE_TOKEN", TOKEN_GLOBAL)
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", M2M)  # M2M configurado, como no fixture cli (revisão do planejador, 07/10)
    import app
    importlib.reload(app)
    return app.app.test_client()


def _h(trace="t-f2-1"):
    return {"X-RLogix-Service-Token": M2M, "X-Trace-Id": trace,
            "X-Source-System": "mapone"}


def _corpo(**extra):
    c = {"ambiente": "homologacao", "ref": REF, "cnpj_emitente": CNPJ,
         "focusnfe_token": TK_FOCUS}
    c.update(extra)
    return c


def _payload(tipo):
    if tipo == "cte":
        return {"modal": "01", "cfop": "5353", "valor_total": "2964.60",
                "observacao": "Carga Ação", "cnpj_emitente": CNPJ}
    return {"prestador": {"cnpj": CNPJ}, "servico": {"valor": "150.00",
            "discriminacao": "Frete Ação"}}


def _json(r):
    """JSON de resposta de uma rota C2: so existe `status` se a rota emite."""
    j = r.get_json(silent=True)
    assert isinstance(j, dict), f"resposta nao JSON ({r.status_code})"
    return j


def _exige_rota_c2(r, esperado_ok=True):
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA", \
        "rota ainda bloqueada (bloquear_emissao)"
    assert "status" in j, f"resposta sem `status` do C2: {j}"
    if esperado_ok is not None:
        assert j.get("ok") is esperado_ok, j
    return j


def _sem_violacao(focus):
    assert focus.violacoes == [], f"chamada fora da homologacao: {focus.violacoes}"
    for c in focus.calls:
        assert c["allow_redirects"] is False, \
            f"allow_redirects != False em {c['method']} {c['url']}"


def _basic_ok(rec, tk=TK_FOCUS):
    auth = rec["headers"].get("authorization", "")
    if auth.startswith("Basic "):
        return base64.b64decode(auth[6:]).decode() == f"{tk}:"
    a = rec.get("auth")
    return isinstance(a, tuple) and a[0] == tk and not a[1]


# ═══ aceite principal ══════════════════════════════════════════════════════
def test_cte_homologacao_emite_consulta_cancela_no_host_fixo(
        monkeypatch, focus):
    """EM-04: ciclo completo do CT-e, host fixo mesmo com overrides de prod."""
    _configurar_focus(focus, "cte")
    monkeypatch.setenv("FOCUSNFE_BASE_URL", PROD_URL + "/v2")
    monkeypatch.setenv("FOCUSNFE_AMBIENTE", "producao")
    monkeypatch.setenv("FOCUSNFE_TOKEN", TOKEN_GLOBAL)
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", M2M)  # M2M configurado, como no fixture cli (revisão do planejador, 07/10)
    import app
    importlib.reload(app)
    cli = app.app.test_client()

    payload = _payload("cte")
    r = cli.post("/fiscal/cte", headers=_h("tr-emit"),
                 json=_corpo(payload=payload))
    j = _exige_rota_c2(r)
    assert j["status"] == "processando_autorizacao"       # 202 nunca e autorizado
    assert j["status"] != "autorizado"
    assert j["ref"] == REF
    assert j["http_status_focus"] == 202
    assert j["trace_id"] == "tr-emit"
    assert "xml_base64" not in j and "pdf_base64" not in j

    emit = focus.chamadas_focus()[-1]
    assert emit["method"] == "POST" and emit["host"] == HOMOLOG_HOST
    assert emit["path"].rstrip("/") == "/v2/cte"
    assert emit["query"].get("ref") == [REF]
    assert emit["json"] == payload                        # repassado intacto
    assert _basic_ok(emit)

    r = cli.post("/fiscal/cte/" + REF + "/consultar", headers=_h("tr-cons"),
                 json=_corpo())
    j = _exige_rota_c2(r)
    assert j["status"] == "autorizado"
    assert j["numero"] in ("123", 123) and str(j["serie"]) == "1"
    assert j["protocolo"] == "135260000000001"
    assert j["chave"] == CHAVE
    assert base64.b64decode(j["xml_base64"]) == XML_BYTES
    assert base64.b64decode(j["pdf_base64"]) == PDF_BYTES
    assert j["http_status_focus"] == 200
    cons = [c for c in focus.chamadas_focus() if c["method"] == "GET"][0]
    assert cons["host"] == HOMOLOG_HOST
    assert cons["path"].startswith("/v2/cte") and cons["path"].rstrip("/").endswith(REF)

    r = cli.delete("/fiscal/cte/" + REF, headers=_h("tr-canc"),
                   json=_corpo(justificativa="Cancelamento de teste em homologacao"))
    j = _exige_rota_c2(r)
    assert j["status"] == "cancelado"
    canc = [c for c in focus.chamadas_focus() if c["method"] == "DELETE"][0]
    assert canc["host"] == HOMOLOG_HOST
    assert canc["path"].startswith("/v2/cte") and canc["path"].rstrip("/").endswith(REF)
    assert (canc["json"] or {}).get("justificativa") == \
        "Cancelamento de teste em homologacao"
    assert TOKEN_GLOBAL not in " ".join(
        str(c["headers"]) + str(c["auth"]) for c in focus.calls)

    assert focus.calls, "nenhuma chamada a Focus foi feita"
    assert {c["host"] for c in focus.calls} == {HOMOLOG_HOST}
    for c in focus.downloads():
        assert c["method"] == "GET"
    assert TK_FOCUS not in " ".join(focus.urls())            # token nunca na URL
    _sem_violacao(focus)


def test_nfsen_homologacao_emite_consulta_cancela_sincrono(monkeypatch, focus):
    """EM-05: ciclo da NFS-e Nacional; cancelamento sincrono (uma chamada)."""
    _configurar_focus(focus, "nfsen")
    monkeypatch.setenv("FOCUSNFE_BASE_URL", PROD_URL + "/v2")
    monkeypatch.setenv("FOCUSNFE_AMBIENTE", "producao")
    monkeypatch.setenv("FOCUSNFE_TOKEN", TOKEN_GLOBAL)
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", M2M)  # M2M configurado, como no fixture cli (revisão do planejador, 07/10)
    import app
    importlib.reload(app)
    cli = app.app.test_client()

    payload = _payload("nfsen")
    r = cli.post("/fiscal/nfsen", headers=_h(), json=_corpo(payload=payload))
    j = _exige_rota_c2(r)
    assert j["status"] == "processando_autorizacao"
    assert j["http_status_focus"] == 202 and j["ref"] == REF
    emit = focus.chamadas_focus()[-1]
    assert emit["method"] == "POST" and emit["host"] == HOMOLOG_HOST
    assert emit["path"].rstrip("/") == "/v2/nfsen"
    assert emit["query"].get("ref") == [REF]
    assert emit["json"] == payload
    assert _basic_ok(emit)

    r = cli.post("/fiscal/nfsen/" + REF + "/consultar", headers=_h(),
                 json=_corpo())
    j = _exige_rota_c2(r)
    assert j["status"] == "autorizado"
    assert str(j["numero"]) == "77"
    assert base64.b64decode(j["xml_base64"]) == XML_BYTES
    assert base64.b64decode(j["pdf_base64"]) == PDF_BYTES
    cons = [c for c in focus.chamadas_focus() if c["method"] == "GET"][0]
    assert cons["path"].rstrip("/") == "/v2/nfsen/" + REF

    n_antes = len(focus.calls)
    r = cli.delete("/fiscal/nfsen/" + REF, headers=_h(),
                   json=_corpo(justificativa="Emissao em duplicidade"))
    j = _exige_rota_c2(r)
    assert j["status"] == "cancelado"                    # sincrono: ja na resposta
    novas = focus.calls[n_antes:]
    assert len(novas) == 1, f"cancelamento sincrono deve ser 1 chamada: {novas}"
    assert novas[0]["method"] == "DELETE"
    assert novas[0]["path"].rstrip("/") == "/v2/nfsen/" + REF
    assert novas[0]["host"] == HOMOLOG_HOST
    assert (novas[0]["json"] or {}).get("justificativa") == "Emissao em duplicidade"
    assert TK_FOCUS not in " ".join(focus.urls())
    assert {c["host"] for c in focus.calls} == {HOMOLOG_HOST}
    _sem_violacao(focus)


def test_producao_override_e_token_global_recusados_sem_http(
        monkeypatch, focus):
    """EM-06: producao recusada antes de HTTP; overrides e token global nulos."""
    _configurar_focus(focus, "cte")
    _configurar_focus(focus, "nfsen")
    cli = _producao_hostil(monkeypatch)

    # 1) ambiente != homologacao -> 403 EMISSAO_BLOQUEADA, com as 3 flags ligadas
    for metodo, rota in TODAS_AS_ROTAS:
        r = cli.open(rota, method=metodo, headers=_h(),
                     json=_corpo(ambiente="producao",
                                 payload={"k": "v"}))
        j = _json(r)
        assert r.status_code == 403, f"{metodo} {rota}: {r.status_code}"
        assert j["codigo"] == "EMISSAO_BLOQUEADA" and j["ok"] is False
        # distingue do bloqueio total antigo: o motivo agora e o ambiente
        assert "ambiente" in json.dumps(j).lower(), \
            "recusa deve nomear o ambiente (nao o bloqueio total legado)"
    assert focus.calls == []

    # 2) token global de ambiente nao e fallback
    for metodo, rota in TODAS_AS_ROTAS:
        c = _corpo()
        c.pop("focusnfe_token")
        r = cli.open(rota, method=metodo, headers=_h(), json=c)
        assert _json(r)["codigo"] == "FOCUS_TOKEN_AUSENTE", (metodo, rota)
        assert r.status_code == 400
    assert focus.calls == []

    # 3) com homologacao explicita, override de producao e ignorado
    r = cli.post("/fiscal/cte", headers=_h(), json=_corpo(payload={"a": 1}))
    _exige_rota_c2(r)
    assert focus.calls and {c["host"] for c in focus.calls} == {HOMOLOG_HOST}
    assert all(TOKEN_GLOBAL not in str(c["headers"]) for c in focus.calls)
    _sem_violacao(focus)

    # 4) constante legada perde o padrao de producao
    caminho = Path(__file__).resolve().parent.parent / "providers" / \
        "focusnfe_provider.py"
    monkeypatch.delenv("FOCUSNFE_BASE_URL", raising=False)
    spec = importlib.util.spec_from_file_location("fo_prov_copia", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    legado = getattr(mod, "FOCUSNFE_BASE_URL", "")
    assert "api.focusnfe.com.br" not in str(legado), \
        f"constante legada ainda aponta para producao: {legado!r}"

    # 5) MDF-e e NF-e continuam bloqueados (junto com C2 ja ativo)
    for metodo, rota in [("POST", "/fiscal/mdfe"), ("POST", "/fiscal/nfe"),
                         ("DELETE", "/fiscal/nfe/" + CHAVE)]:
        r = cli.open(rota, method=metodo, headers=_h(), json=_corpo())
        assert r.status_code == 403 and _json(r)["codigo"] == "EMISSAO_BLOQUEADA"


# ═══ negativos: ausente ═══════════════════════════════════════════════════
@pytest.mark.parametrize("metodo,rota", TODAS_AS_ROTAS)
def test_neg_ausente__token_focus_no_corpo(cli, focus, metodo, rota):
    _configurar_focus(focus, "cte")
    _configurar_focus(focus, "nfsen")
    for token in (None, "", "   "):
        c = _corpo()
        if token is None:
            c.pop("focusnfe_token")
        else:
            c["focusnfe_token"] = token
        r = cli.open(rota, method=metodo, headers=_h(), json=c)
        j = _json(r)
        assert j.get("codigo") == "FOCUS_TOKEN_AUSENTE", (token, j)
        assert j["ok"] is False and r.status_code == 400
    assert focus.calls == []


@pytest.mark.parametrize("metodo,rota", TODAS_AS_ROTAS)
def test_neg_ausente__header_m2m(cli, focus, metodo, rota):
    for hdr, codigo, st in (({}, "M2M_NAO_AUTORIZADO", 401),
                            ({"X-RLogix-Service-Token": "errado"},
                             "M2M_NAO_AUTORIZADO", 401)):
        r = cli.open(rota, method=metodo, headers=hdr, json=_corpo())
        assert r.status_code == st, (metodo, rota, r.status_code)
        assert _json(r)["codigo"] == codigo
    assert focus.calls == []


def test_neg_ausente__m2m_nao_configurado_no_servidor(cli, focus, monkeypatch):
    monkeypatch.delenv("FISCALONE_M2M_TOKEN", raising=False)
    r = cli.post("/fiscal/cte", headers=_h(), json=_corpo(payload={"a": 1}))
    assert r.status_code == 503
    assert _json(r)["codigo"] == "M2M_NAO_CONFIGURADO"
    assert focus.calls == []


@pytest.mark.parametrize("tipo", TIPOS)
def test_neg_ausente__ref_na_emissao(cli, focus, tipo):
    c = _corpo(payload=_payload(tipo))
    c.pop("ref")
    r = cli.post("/fiscal/" + tipo, headers=_h(), json=c)
    assert r.status_code == 400
    assert _json(r)["codigo"] == "REF_INVALIDO"
    assert focus.calls == []


@pytest.mark.parametrize("metodo,rota", TODAS_AS_ROTAS)
def test_neg_ausente__ambiente_recusa_fechada(cli, focus, metodo, rota):
    """Sem `ambiente` nao ha como provar homologacao: recusa fail-closed."""
    c = _corpo()
    c.pop("ambiente")
    r = cli.open(rota, method=metodo, headers=_h(), json=c)
    assert r.status_code == 403
    j = _json(r)
    assert j["codigo"] == "EMISSAO_BLOQUEADA"
    assert "ambiente" in json.dumps(j).lower()
    assert focus.calls == []


@pytest.mark.parametrize("metodo,rota", TODAS_AS_ROTAS)
def test_neg_ausente__corpo_json(cli, focus, metodo, rota):
    r = cli.open(rota, method=metodo, headers=_h())
    j = _json(r)
    assert r.status_code == 400 and j["ok"] is False
    assert j["codigo"] in ("PAYLOAD_INVALIDO", "FOCUS_TOKEN_AUSENTE")
    assert focus.calls == []


# ═══ negativos: tipo errado ═══════════════════════════════════════════════
REFS_INVALIDAS = ["a b", "ref.xml", "x" * 65, "ref;rm -rf", "ref\nx", "",
                  "a/b", "ção", 123, ["a"], {"a": 1}, None, True]


@pytest.mark.parametrize("tipo", TIPOS)
@pytest.mark.parametrize("ref", REFS_INVALIDAS,
                         ids=[repr(r)[:20] for r in REFS_INVALIDAS])
def test_neg_tipo_errado__ref_invalido_no_corpo(cli, focus, tipo, ref):
    r = cli.post("/fiscal/" + tipo, headers=_h(),
                 json=_corpo(ref=ref, payload=_payload(tipo)))
    assert r.status_code == 400, f"ref {ref!r}: {r.status_code}"
    assert _json(r)["codigo"] == "REF_INVALIDO"
    assert focus.calls == []


@pytest.mark.parametrize("tipo", TIPOS)
@pytest.mark.parametrize("ref", ["a%20b", "x" * 65, "ref.xml", "ref;x", "%C3%A7"])
def test_neg_tipo_errado__ref_invalido_na_url(cli, focus, tipo, ref):
    for metodo, rota in (("POST", f"/fiscal/{tipo}/{ref}/consultar"),
                         ("DELETE", f"/fiscal/{tipo}/{ref}")):
        r = cli.open(rota, method=metodo, headers=_h(), json=_corpo(ref=ref))
        assert r.status_code == 400, (metodo, rota, r.status_code)
        assert _json(r)["codigo"] == "REF_INVALIDO"
    assert focus.calls == []


@pytest.mark.parametrize("amb", ["producao", "sandbox", "HOMOLOG", 123,
                                 ["homologacao"], None, ""])
@pytest.mark.parametrize("metodo,rota", TODAS_AS_ROTAS)
def test_neg_tipo_errado__ambiente(cli, focus, metodo, rota, amb):
    r = cli.open(rota, method=metodo, headers=_h(),
                 json=_corpo(ambiente=amb, payload={"a": 1}))
    assert r.status_code == 403, (amb, r.status_code)
    j = _json(r)
    assert j["codigo"] == "EMISSAO_BLOQUEADA"
    assert "ambiente" in json.dumps(j).lower(), \
        "recusa deve nomear o ambiente (nao o bloqueio total legado)"
    assert focus.calls == []


@pytest.mark.parametrize("metodo,rota", TODAS_AS_ROTAS)
def test_neg_tipo_errado__corpo_nao_objeto(cli, focus, metodo, rota):
    for corpo in ([1, 2], "texto", 7):
        r = cli.open(rota, method=metodo, headers=_h(), json=corpo)
        j = _json(r)
        assert r.status_code == 400 and j["ok"] is False
        assert j["codigo"] in ("PAYLOAD_INVALIDO", "FOCUS_TOKEN_AUSENTE")
    assert focus.calls == []


@pytest.mark.parametrize("valor_tk", [123, ["x"], {"a": 1}, True])
def test_neg_tipo_errado__token_nao_string(cli, focus, valor_tk):
    r = cli.post("/fiscal/cte", headers=_h(),
                 json=_corpo(**{"focusnfe_token": valor_tk, "payload": {"a": 1}}))
    j = _json(r)
    assert r.status_code == 400 and j["ok"] is False
    assert j["codigo"] in ("FOCUS_TOKEN_AUSENTE", "PAYLOAD_INVALIDO")
    assert focus.calls == []


@pytest.mark.parametrize("payload", ["texto", 5, ["a"]])
@pytest.mark.parametrize("tipo", TIPOS)
def test_neg_tipo_errado__payload_nao_objeto(cli, focus, tipo, payload):
    r = cli.post("/fiscal/" + tipo, headers=_h(),
                 json=_corpo(payload=payload))
    j = _json(r)
    assert r.status_code == 400 and j["ok"] is False
    assert j["codigo"] != "EMISSAO_BLOQUEADA"
    assert focus.calls == []


def test_neg_tipo_errado__consultar_so_aceita_post(cli, focus):
    for tipo in TIPOS:
        r = cli.get(f"/fiscal/{tipo}/{REF}/consultar", headers=_h())
        assert r.status_code == 405, (tipo, r.status_code)
    assert focus.calls == []


# ═══ negativos: fronteira (nenhuma chamada fora da homologacao) ═══════════
@pytest.mark.parametrize("tipo", TIPOS)
def test_neg_fronteira_banco__base_url_hostil_e_ignorada(
        monkeypatch, focus, tipo):
    _configurar_focus(focus, tipo)
    monkeypatch.setenv("FOCUSNFE_BASE_URL", "https://evil.example/v2")
    monkeypatch.setenv("FOCUSNFE_AMBIENTE", "producao")
    monkeypatch.setenv("FISCALONE_M2M_TOKEN", M2M)  # M2M configurado, como no fixture cli (revisão do planejador, 07/10)
    import app
    importlib.reload(app)
    cli = app.app.test_client()
    r = cli.post("/fiscal/" + tipo, headers=_h(),
                 json=_corpo(payload=_payload(tipo)))
    _exige_rota_c2(r)
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    _exige_rota_c2(r)
    r = cli.delete(f"/fiscal/{tipo}/{REF}", headers=_h(),
                   json=_corpo(justificativa="Cancelamento de teste de fronteira"))
    _exige_rota_c2(r)
    assert focus.calls
    assert {c["host"] for c in focus.calls} == {HOMOLOG_HOST}
    assert focus.violacoes == []
    _sem_violacao(focus)


@pytest.mark.parametrize("tipo", TIPOS)
@pytest.mark.parametrize("alvo", ["https://evil.example/x.xml",
                                  PROD_URL + "/arquivos/x.xml",
                                  "http://" + HOMOLOG_HOST + "/arquivos/x.xml",
                                  "//evil.example/x.xml"])
def test_neg_fronteira_banco__caminho_de_arquivo_de_outro_host(
        cli, focus, tipo, alvo):
    """A Focus devolve caminho/URL fora da homologacao: nao pode ser baixado."""
    _configurar_focus(focus, tipo)
    foco = focus.respostas[("GET", tipo)][1]
    for k in list(foco):
        if k.startswith("caminho_") or k == "url_danfse":
            foco[k] = alvo
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert focus.chamadas_focus(), "a consulta a Focus nao ocorreu"
    assert focus.violacoes == []
    assert focus.downloads() == [], \
        f"baixou arquivo de URL fora da homologacao/https: {focus.downloads()}"
    assert "xml_base64" not in j and "pdf_base64" not in j
    assert TK_FOCUS not in json.dumps(j)


@pytest.mark.parametrize("tipo", TIPOS)
def test_neg_fronteira_banco__redirect_de_download_nao_e_seguido(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    for p in (f"/arquivos/{tipo}.xml", f"/arquivos/{tipo}.pdf"):
        focus.arquivos[p] = (302, b"", {"Location": "https://evil.example/roubo"})
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert focus.downloads(), "esperava tentativa de download no host de homologacao"
    assert all(c["host"] == HOMOLOG_HOST for c in focus.calls)
    assert focus.violacoes == []
    _sem_violacao(focus)                                # allow_redirects False
    assert "xml_base64" not in j or not j["xml_base64"]
    assert "evil.example" not in json.dumps(j)          # erro sanitizado


@pytest.mark.parametrize("tipo", TIPOS)
def test_neg_fronteira_banco__download_nao_leva_token(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    _exige_rota_c2(r)
    assert focus.downloads()
    for c in focus.downloads():
        assert TK_FOCUS not in c["url"]
        assert c["host"] == HOMOLOG_HOST and c["scheme"] == "https"
        assert c["allow_redirects"] is False


def test_neg_fronteira_banco__mdfe_e_nfe_seguem_bloqueados_com_c2_ativo(
        cli, focus):
    _configurar_focus(focus, "cte")
    r = cli.post("/fiscal/cte", headers=_h(), json=_corpo(payload={"a": 1}))
    _exige_rota_c2(r)                                   # C2 vivo
    n = len(focus.calls)
    for metodo, rota in [("POST", "/fiscal/mdfe"), ("POST", "/fiscal/nfe"),
                         ("DELETE", "/fiscal/nfe/" + CHAVE),
                         ("POST", "/fiscal/mdfe/" + CHAVE + "/encerrar"),
                         ("POST", "/fiscal/nfe/" + CHAVE + "/cce")]:
        r = cli.open(rota, method=metodo, headers=_h(), json=_corpo())
        assert r.status_code == 403 and _json(r)["codigo"] == "EMISSAO_BLOQUEADA"
    assert len(focus.calls) == n


# ═══ token fora de log e resposta ═════════════════════════════════════════
def test_token_nao_aparece_em_resposta_log_nem_url(cli, focus, caplog, capsys):
    _configurar_focus(focus, "cte")
    _configurar_focus(focus, "nfsen")
    focus.respostas[("POST", "cte")] = (
        422, {"codigo": "requisicao_invalida",
              "mensagem": "falha com Authorization Basic " +
              base64.b64encode(f"{TK_FOCUS}:".encode()).decode()})
    corpos = []
    with caplog.at_level(logging.DEBUG):
        for tipo in TIPOS:
            r = cli.post("/fiscal/" + tipo, headers=_h(),
                         json=_corpo(payload=_payload(tipo)))
            j = _exige_rota_c2(r, esperado_ok=None)
            corpos.append(r.get_data(as_text=True))
            r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(),
                         json=_corpo())
            corpos.append(r.get_data(as_text=True))
            r = cli.delete(f"/fiscal/{tipo}/{REF}", headers=_h(),
                           json=_corpo(justificativa="Cancelamento de teste de log"))
            corpos.append(r.get_data(as_text=True))
    assert focus.chamadas_focus(), "rotas C2 nao chamaram a Focus"
    saida = capsys.readouterr()
    b64 = base64.b64encode(f"{TK_FOCUS}:".encode()).decode()
    alvo = "\n".join(corpos) + caplog.text + saida.out + saida.err + \
        "\n".join(focus.urls())
    assert TK_FOCUS not in alvo
    assert b64 not in alvo
    assert M2M not in alvo


@pytest.mark.parametrize("tipo", TIPOS)
def test_token_nao_fica_no_corpo_repassado_a_focus(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    cli.post("/fiscal/" + tipo, headers=_h(), json=_corpo(payload=_payload(tipo)))
    envio = focus.chamadas_focus()
    assert envio, "emissao nao chamou a Focus"
    for c in envio:
        assert "focusnfe_token" not in json.dumps(c["json"] or {})
        assert TK_FOCUS not in json.dumps(c["json"] or {})
        assert "ambiente" not in (c["json"] or {})


# ═══ semantica de status e erros ══════════════════════════════════════════
@pytest.mark.parametrize("tipo", TIPOS)
def test_emissao_rejeitada_422_vira_erro_com_codigo_e_mensagem(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    focus.respostas[("POST", tipo)] = (
        422, {"codigo": "requisicao_invalida", "mensagem": "Campo X ausente"})
    r = cli.post("/fiscal/" + tipo, headers=_h(),
                 json=_corpo(payload=_payload(tipo)))
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert j["ok"] is False
    assert j["status"] != "autorizado"
    assert j["http_status_focus"] == 422
    assert j["erros"] and {"codigo", "mensagem"} <= set(j["erros"][0])
    assert j["trace_id"]


@pytest.mark.parametrize("tipo", TIPOS)
def test_consulta_em_processamento_nao_baixa_arquivos(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    focus.respostas[("GET", tipo)] = (200, {"status": "processando_autorizacao"})
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    j = _exige_rota_c2(r)
    assert j["status"] == "processando_autorizacao"
    assert "xml_base64" not in j and "pdf_base64" not in j
    assert focus.downloads() == []


@pytest.mark.parametrize("tipo", TIPOS)
def test_consulta_erro_autorizacao_traz_erros(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    focus.respostas[("GET", tipo)] = (200, {
        "status": "erro_autorizacao",
        "erros": [{"codigo": "E160", "mensagem": "Rejeicao teste",
                   "correcao": None}]})
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert j["status"] == "erro_autorizacao" and j["ok"] is False
    assert j["erros"][0]["codigo"] == "E160"
    assert focus.downloads() == []


@pytest.mark.parametrize("tipo", TIPOS)
def test_consulta_404_vira_nao_encontrado(cli, focus, tipo):
    _configurar_focus(focus, tipo)
    focus.respostas[("GET", tipo)] = (404, {"codigo": "nao_encontrado",
                                            "mensagem": "Nota fiscal nao encontrada"})
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert j["status"] == "nao_encontrado" and j["ok"] is False
    assert j["http_status_focus"] == 404


def test_nfsen_cancelamento_recusado_pela_focus_e_erro_cancelamento(cli, focus):
    _configurar_focus(focus, "nfsen")
    focus.respostas[("DELETE", "nfsen")] = (200, {
        "status": "erro_cancelamento",
        "erros": [{"codigo": "V999",
                   "mensagem": "NFSe fora do prazo de cancelamento permitido",
                   "correcao": None}]})
    r = cli.delete("/fiscal/nfsen/" + REF, headers=_h(),
                   json=_corpo(justificativa="Emissao em duplicidade"))
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert j["status"] == "erro_cancelamento" and j["ok"] is False
    assert j["erros"][0]["codigo"] == "V999"
    assert len(focus.chamadas_focus()) == 1


@pytest.mark.parametrize("tipo", TIPOS)
def test_corpo_da_emissao_sem_payload_e_recusado_sem_http(cli, focus, tipo):
    r = cli.post("/fiscal/" + tipo, headers=_h(), json=_corpo())
    j = _json(r)
    assert r.status_code == 400 and j["ok"] is False
    assert j["codigo"] != "EMISSAO_BLOQUEADA"
    assert focus.calls == []


# ═══ health ═══════════════════════════════════════════════════════════════
def test_health_declara_emissao_homologacao_e_nao_producao(cli):
    j = cli.get("/fiscal/health").get_json()
    assert j.get("emissao_homologacao") is True
    assert j.get("emissao_producao") is False


def test_health_com_tres_flags_de_producao_nao_libera_emissao_producao(
        monkeypatch, focus):
    cli = _producao_hostil(monkeypatch)
    j = cli.get("/fiscal/health").get_json()
    assert j.get("emissao_homologacao") is True
    assert j.get("emissao_producao") is False


# ── Rodada de correção (revisão de código do F2, Codex, 07/10) ────────────

STATUS_C2 = {"processando_autorizacao", "autorizado", "erro_autorizacao",
             "cancelado", "erro_cancelamento", "nao_encontrado"}


@pytest.mark.parametrize("tipo", ["cte", "nfsen"])
def test_neg_fronteira_banco__credencial_retirada_do_corpo_recebido(cli, focus, monkeypatch, tipo):
    """C2: a credencial da Focus é retirada do corpo da requisição (não só da
    variável local). O dicionário que o Flask entrega ao handler não pode
    continuar com ela depois da chamada."""
    import flask
    _configurar_focus(focus, tipo)
    recebidos = []
    original = flask.Request.get_json

    def espiao(self, *a, **k):
        d = original(self, *a, **k)
        if isinstance(d, dict):
            recebidos.append(d)
        return d

    monkeypatch.setattr(flask.Request, "get_json", espiao)
    r = cli.post(f"/fiscal/{tipo}", headers=_h(), json=_corpo(payload=_payload(tipo)))
    _exige_rota_c2(r)
    assert recebidos, "o handler lê o corpo pelo get_json"
    for d in recebidos:
        assert "focusnfe_token" not in d, "credencial continua no corpo recebido"


@pytest.mark.parametrize("tipo", ["cte", "nfsen"])
def test_neg_tipo_errado__status_desconhecido_da_focus_vira_erro_contratual(cli, focus, tipo):
    """C2: `status` só sai em um dos 6 valores do contrato. Status que a Focus
    devolver fora deles não é repassado nem vira sucesso."""
    _configurar_focus(focus, tipo)
    focus.respostas[("GET", tipo)] = (200, {"status": "status_inventado_pela_focus"})
    r = cli.post(f"/fiscal/{tipo}/{REF}/consultar", headers=_h(), json=_corpo())
    j = _json(r)
    assert j.get("codigo") != "EMISSAO_BLOQUEADA" and "status" in j, j
    assert j["status"] in STATUS_C2, j["status"]
    assert j["ok"] is False
    assert "status_inventado_pela_focus" != j["status"]
