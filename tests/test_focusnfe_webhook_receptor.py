"""ADR-0051 Fase 2D-R1 · FiscalOne — receptor de webhook FocusNFe.

Cobre apenas o comportamento local (validação de path/eventos) — o
forward para o MapOne é mockado, portanto zero HTTP real.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MAPONE_WEBHOOK_INBOUND_TOKEN", "tok-fake")
    import app as _app
    _app.app.config["TESTING"] = True
    return _app.app.test_client()


def test_nfsen_recebida_no_enum_do_receptor():
    import app as _app
    assert "nfsen_recebida" in _app._WEBHOOK_EVENTOS_PERMITIDOS


def test_evento_desconhecido_retorna_404(client):
    r = client.post(
        "/webhooks/focusnfe/IDENT-XYZ/evento_falso",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert r.status_code == 404
    assert r.get_json()["codigo"] == "EVENTO_NAO_PERMITIDO"


def test_identifier_muito_longo_retorna_400(client):
    r = client.post(
        f"/webhooks/focusnfe/{'A' * 65}/nfsen_recebida",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert r.status_code == 400
    assert r.get_json()["codigo"] == "IDENTIFIER_INVALIDO"


def test_content_type_invalido_rejeita(client):
    r = client.post(
        "/webhooks/focusnfe/IDENT-XYZ/nfsen_recebida",
        data="<xml/>",
        content_type="application/xml",
    )
    assert r.status_code == 415
    assert r.get_json()["codigo"] == "CONTENT_TYPE_INVALIDO"


def test_payload_vazio_rejeita(client):
    r = client.post(
        "/webhooks/focusnfe/IDENT-XYZ/nfsen_recebida",
        data=b"",
        content_type="application/json",
    )
    assert r.status_code == 400
    assert r.get_json()["codigo"] == "PAYLOAD_VAZIO"


def test_nfsen_recebida_encaminha_para_mapone_com_header_fixo(client):
    payload = {"cnpj": "12345678000199", "chave": "35" + "0" * 42}
    captured = {}

    class _FakeResp:
        def getcode(self):
            return 202
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        r = client.post(
            "/webhooks/focusnfe/IDENT-XYZ/nfsen_recebida",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"X-Focus-Webhook-Token": "tok-do-mapone-123"},
        )
    assert r.status_code == 202
    assert captured["body"]["identifier"] == "IDENT-XYZ"
    assert captured["body"]["event"] == "nfsen_recebida"
    assert captured["body"]["authorization_value"] == "tok-do-mapone-123"
    assert captured["body"]["authorization_header"] == "X-Focus-Webhook-Token"
    # header M2M canônico
    assert captured["headers"]["X-source-system"] == "fiscalone"
