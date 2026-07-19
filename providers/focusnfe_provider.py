"""
FocusNFeProvider — HTTP real via API v2 (Fase 2 HTTP).

Endpoints:
- GET /v2/nfes_recebidas?cnpj=<>&versao=<>       (lote incremental)
- GET /v2/nfes_recebidas/{chave}.pdf              (DANFE — pode retornar 302)

Autenticacao: HTTP Basic com usuario=token, senha vazia.
  header: `Authorization: Basic base64(f"{token}:")`
  NAO usar Bearer.

Cursor: `versao` incremental (int em JSON Focus). Preservado como string
via `services.nsu_utils.normalizar_nsu("focusnfe", ...)`.

Seguranca (invariantes desta fase):
- Token NUNCA em log, envelope, mensagem de erro ou raw_json_focus.
- Segundo GET de URL pre-assinada de DANFE NUNCA envia Authorization.
- Header `Authorization` nunca serializado no envelope de retorno.
- `EmissaoProibida` bloqueia emitir_* (defesa em profundidade — rotas do
  app.py ja bloqueiam via `bloquear_emissao()`).
"""
import base64
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from providers import GovProvider


# ── Erros de dominio ──────────────────────────────────────────────────────────
class EmissaoProibida(RuntimeError):
    """Emissao via FocusNFe bloqueada por design nesta fase.

    FocusNFe no FiscalOne e usado apenas para recebimento de documentos.
    """


# ── Helpers de credencial ─────────────────────────────────────────────────────
def _masked_token(token: str | None) -> str:
    """Mascara token para logs. Nunca retorna o valor completo."""
    if not token:
        return "***[ausente]"
    token = str(token)
    if len(token) <= 4:
        return "***"
    return f"***{token[-4:]}"


def _basic_auth_header(token: str) -> dict:
    """`Authorization: Basic base64(token:)`. Nunca logar este header."""
    credencial = base64.b64encode(f"{token}:".encode()).decode()
    return {"Authorization": f"Basic {credencial}"}


# ── Helpers de configuracao ───────────────────────────────────────────────────
# Bases oficiais sem `/v2`. O prefixo `/v2` e concatenado nas rotas para
# garantir montagem correta independente de como FOCUSNFE_BASE_URL for
# fornecido pelo operador (com ou sem `/v2` no final).
_FOCUSNFE_HOSTS = {
    "producao":     "https://api.focusnfe.com.br",
    "homologacao":  "https://homologacao.focusnfe.com.br",
}


def _normalizar_base_url(base_url: str) -> str:
    """Remove barra final e sufixo `/v2` para garantir montagem correta.

    Aceita `FOCUSNFE_BASE_URL` com ou sem `/v2` no final; a concatenacao
    das rotas sempre adiciona `/v2/...`, entao esta normalizacao evita
    `/v2/v2` no cenario em que o operador incluir `/v2` no env.
    """
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/v2"):
        url = url[:-3].rstrip("/")
    return url


def _resolve_base_url(env_ambiente: str | None = None) -> str:
    """Base URL do FocusNFe (SEM `/v2` — adicionado na montagem da rota).

    Regras:
      1. Se FOCUSNFE_BASE_URL estiver definido, usa esse valor normalizado.
      2. Senao, usa mapa ambiente → host oficial. Default seguro: homologacao.
    """
    base = os.environ.get("FOCUSNFE_BASE_URL", "").strip()
    if base:
        return _normalizar_base_url(base)
    amb = (env_ambiente or os.environ.get("FOCUSNFE_AMBIENTE") or "homologacao").strip().lower()
    return _FOCUSNFE_HOSTS.get(amb, _FOCUSNFE_HOSTS["homologacao"])


# ── Envelope canonico ─────────────────────────────────────────────────────────
def _envelope_erro(trace_id: str, codigo: str, mensagem: str,
                   extra: dict | None = None) -> dict:
    """Envelope de erro canonico do FocusNFe.

    Mantem contrato do envelope de lote (documentos, resumos, erros) para
    consumidores nao terem que tratar formato diferente.
    """
    env: dict[str, Any] = {
        "ok":          False,
        "provider":    "focusnfe",
        "trace_id":    trace_id,
        "codigo":      codigo,
        "erro":        mensagem,
        "documentos":  [],
        "resumos":     [],
        "erros":       [],
        "nsu_avancou": False,
        "cursor_tipo": "versao",
    }
    if extra:
        env.update(extra)
    return env


# ── Sanitizacao de raw_json ───────────────────────────────────────────────────
# Chaves que jamais podem ir para raw_json_focus (mesmo que Focus retorne).
_CAMPOS_SENSIVEIS = frozenset({
    "authorization", "token", "password", "senha", "secret", "api_key",
    "apikey", "credential", "credentials", "x-auth-token",
})


def _sanitize_focus_item(item: Any) -> Any:
    """Remove/mascara campos sensiveis do JSON Focus antes de guardar como raw."""
    if isinstance(item, dict):
        out = {}
        for k, v in item.items():
            kl = str(k).lower()
            if kl in _CAMPOS_SENSIVEIS:
                out[k] = "***"
            else:
                out[k] = _sanitize_focus_item(v)
        return out
    if isinstance(item, list):
        return [_sanitize_focus_item(x) for x in item]
    return item


def _dump_focus_json(item: Any) -> str:
    """Serializa item Focus para string deterministica; nunca vazio quando ha dado."""
    try:
        return json.dumps(_sanitize_focus_item(item), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"_erro_serializacao": True}, sort_keys=True)


# ── Cap de XMLs baixados por batch (Fase E4a) ─────────────────────────────────
# Limite duro de chamadas extras GET /nfes_recebidas/{chave}.xml por gov_fetch.
# Batch da Focus e ate 100 resumos; cap default 25 mantem tempo total previsivel
# (~25 * 5s = ~2min pior caso). Excedentes viram RESUMO + xml_pending=True para
# segunda passada (E4a-2 — fora desta fase). Override via env
# `FOCUSNFE_XML_BATCH_CAP` para operacao ajustar.
try:
    _XML_BATCH_CAP = int(os.environ.get("FOCUSNFE_XML_BATCH_CAP", "25"))
    if _XML_BATCH_CAP < 0:
        _XML_BATCH_CAP = 25
except (TypeError, ValueError):
    _XML_BATCH_CAP = 25


# ── Stub legado (usado pelos metodos ainda nao implementados HTTP) ────────────
_STUB = {
    "ok":       False,
    "provider": "focusnfe",
    "codigo":   "PROVIDER_NAO_IMPLEMENTADO",
    "erro":     "Provider nao implementa esta operacao.",
}


# ── Mapper Focus → NFeDoc ─────────────────────────────────────────────────────
def _get_str(item: dict, *keys, default: str = "") -> str:
    """Primeiro valor nao-vazio para qualquer das chaves fornecidas."""
    for k in keys:
        v = item.get(k)
        if v not in (None, "", []):
            return str(v)
    return default


def _mapear_nfe_focus(item: dict, trace_id: str) -> dict:
    """Mapeia item Focus (schema NfeRecebidaResumo) para dict compativel com
    NFeDoc/NFeDocOpcional.

    Fase E4a — alinhado com a doc oficial FocusNFe:
      - CNPJ do emitente sai de `documento_emitente` (nome real da Focus).
      - `nfe_completa` decide se o item merece XML completo (endpoint
        separado /nfes_recebidas/{chave}.xml — buscado no gov_fetch).
      - `situacao` (autorizada|cancelada|denegada) dirige cStat/xMotivo
        conforme tabela SEFAZ:
          autorizada -> cStat 100
          cancelada  -> cStat 101  (Cancelamento homologado)
          denegada   -> cStat 110  (Uso denegado)
      - `status_xml` (RESUMO|COMPLETO) NUNCA mais influencia cStat —
        distincao fica so em status_xml. Antes: RESUMO virava cStat=101,
        marcando nota autorizada como cancelada (bug fiscal grave).

    Tolerante a variacao de nomes de campo (Focus documenta variantes).
    Nunca inclui Authorization/token. Preserva `raw_json_focus` sanitizado.
    """
    if not isinstance(item, dict):
        raise ValueError(f"item nao e dict: {type(item).__name__}")

    # Chave (obrigatoria para NF-e recebida)
    chave = _get_str(item, "chave_nfe", "chave", "chNFe")
    if not chave:
        raise ValueError("chave NF-e ausente no item Focus")

    # Campos novos da doc oficial Focus (schema NfeRecebidaResumo).
    nfe_completa = bool(item.get("nfe_completa"))
    situacao     = _get_str(item, "situacao").strip().lower()
    tipo_nfe     = _get_str(item, "tipo_nfe")
    manifestacao = _get_str(item, "manifestacao_destinatario")
    data_cancel  = _get_str(item, "data_cancelamento")
    just_cancel  = _get_str(item, "justificativa_cancelamento")

    # Versao (cursor Focus)
    versao_raw = item.get("versao") or item.get("versao_nfe") or 0
    try:
        versao = int(versao_raw)
    except (TypeError, ValueError):
        versao = 0

    # Valores numericos — string ou number, deixa como veio (schema aceita)
    v_nf   = _get_str(item, "valor_total", "vNF", "valor_nfe")
    v_icms = _get_str(item, "valor_icms",  "vICMS")

    # cStat / xMotivo — regra por situacao (E4a). NAO usar tem_xml para cStat.
    if situacao == "cancelada":
        cStat_r, xMotivo_r = "101", "Cancelamento homologado"
        cancelado_r = 1
    elif situacao == "denegada":
        cStat_r, xMotivo_r = "110", "Uso denegado"
        cancelado_r = 0
    else:
        # autorizada ou vazio (default seguro — Focus so lista notas
        # com evento autorizador). xMotivo refletira COMPLETO/RESUMO
        # apos anexacao do XML no gov_fetch.
        cStat_r     = "100"
        xMotivo_r   = "Resumo FocusNFe"
        cancelado_r = 0

    doc = {
        "chNFe":           chave,
        "nProt":           _get_str(item, "protocolo", "nProt"),
        "dhRecbto":        _get_str(item, "data_recebimento", "dhRecbto", "data_emissao"),
        "CNPJ_emit":       _get_str(item, "documento_emitente", "cnpj_emitente", "CNPJ_emit"),
        "CNPJ_dest":       _get_str(item, "cnpj_destinatario", "CNPJ_dest"),
        "vNF":             v_nf,
        "vICMS":           v_icms,
        "numero":          _get_str(item, "numero", "nNF"),
        "serie":           _get_str(item, "serie", "serie_nfe"),
        "emit_nome":       _get_str(item, "nome_emitente", "razao_social_emitente", "emit_nome"),
        "dh_emi":          _get_str(item, "data_emissao", "dh_emi"),
        "cStat":           cStat_r,
        "xMotivo":         xMotivo_r,
        # RESUMO por default — gov_fetch decide se vira COMPLETO baixando o XML.
        "status_xml":      "RESUMO",
        "import_origin":   "fiscalone_focusnfe",
        "trace_id":        trace_id,
        "parser_version":  "focus_v2",
        # Campos opcionais Focus (E4a)
        "versao":          versao,
        "raw_json_focus":  _dump_focus_json(item),
        "danfe_sha256":    "",
        "danfe_fonte":     "focusnfe",
        "nfe_completa":    nfe_completa,
        "tipo_nfe":        tipo_nfe,
        "manifestacao":    manifestacao,
        "situacao_focus":  situacao,
        "cancelado":       cancelado_r,
    }
    if data_cancel:
        doc["data_cancelamento"] = data_cancel
    if just_cancel:
        doc["justificativa_cancelamento"] = just_cancel
    return doc


# ── Normalizadores NFSe (fix 2026-07-18 — servicos lista/dict) ────────────────
# Schema oficial FocusNFe admite `servicos` como dict OU lista de objetos. O
# mapper original tratava apenas dict e descartava silenciosamente listas,
# zerando valores fiscais. Os dois helpers abaixo normalizam ambos os formatos
# ANTES do mapper acessar campos.
_ISS_RETIDO_TRUE = frozenset({"true", "1", "sim", "s"})


def _normalizar_iss_retido_nfse(raw: Any) -> bool:
    """`iss_retido` como bool. Aceita bool/int/float/string.

    Regras:
      - bool: valor direto.
      - int/float: True se > 0.
      - string: "true"/"1"/"sim"/"s" (case-insensitive) → True;
        senao tenta interpretar como numero e retorna True se > 0.
      - None ou outros: False.
    """
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        try:
            return raw > 0
        except TypeError:
            return False
    if isinstance(raw, str):
        s = raw.strip().lower()
        if not s:
            return False
        if s in _ISS_RETIDO_TRUE:
            return True
        try:
            return Decimal(s) > 0
        except (InvalidOperation, ValueError):
            return False
    return False


def _dec_str_estavel(total: Decimal) -> str:
    """Formata Decimal como string estavel com 2 casas (padrao monetario).

    Compatibilidade com contrato atual do mapper (strings tipo '1500.00').
    """
    try:
        return format(total.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError):
        return format(total, "f")


def _normalizar_servicos_nfse(raw: Any) -> dict:
    """Normaliza `servicos` do item Focus NFSe para dict canonico.

    Aceita:
      - dict: retorna copia (sem mutar original). Comportamento legado.
      - list: soma monetarios com Decimal, concatena discriminacao com ' | ',
              iss_retido = OR entre itens, item_lista_servico/codigo_cnae
              pegam o primeiro valor nao vazio.
      - None/outros: `{}` (sem excecao).

    Retorna `{}` se lista vazia ou sem itens validos (dict).
    """
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, list):
        return {}

    campos_soma = ("valor_servicos", "valor_iss", "valor_liquido")
    totais = {c: Decimal("0") for c in campos_soma}
    somou = {c: False for c in campos_soma}
    discriminacoes: list[str] = []
    item_lista = ""
    codigo_cnae = ""
    iss_retido_algum = False
    houve_item_valido = False

    for item in raw:
        if not isinstance(item, dict):
            continue
        houve_item_valido = True
        for c in campos_soma:
            v = item.get(c)
            if v in (None, ""):
                continue
            try:
                totais[c] += Decimal(str(v))
                somou[c] = True
            except (InvalidOperation, ValueError):
                continue
        if _normalizar_iss_retido_nfse(item.get("iss_retido")):
            iss_retido_algum = True
        desc = item.get("discriminacao")
        if desc not in (None, ""):
            desc_str = str(desc).strip()
            if desc_str:
                discriminacoes.append(desc_str)
        if not item_lista:
            il = item.get("item_lista_servico")
            if il not in (None, ""):
                item_lista = str(il).strip()
        if not codigo_cnae:
            cc = item.get("codigo_cnae")
            if cc not in (None, ""):
                codigo_cnae = str(cc).strip()

    if not houve_item_valido:
        return {}

    out: dict[str, Any] = {}
    for c in campos_soma:
        if somou[c]:
            out[c] = _dec_str_estavel(totais[c])
    out["iss_retido"] = iss_retido_algum
    if discriminacoes:
        out["discriminacao"] = " | ".join(discriminacoes)
    if item_lista:
        out["item_lista_servico"] = item_lista
    if codigo_cnae:
        out["codigo_cnae"] = codigo_cnae
    return out


# ── Mapper NFSe Nacional (Fase E4c) ───────────────────────────────────────────
def _mapear_nfse_focus(item: dict, trace_id: str) -> dict:
    """Mapeia item Focus (schema `NfseRecebida`) para dict compativel com
    MapOne. Contrato distinto do NF-e: NFSe nao tem chave DFe 44 digitos,
    nao usa cStat SEFAZ, e o XML vem via URL separada (`url_xml`).

    Campos criticos do item Focus:
      - chave (string opaca — nao validar DV)
      - status (int): 1 autorizado | 2 cancelado | 3 substituido
      - prestador/tomador: dicts com cpf/cnpj + razao_social
      - servicos: dict com valor_servicos, valor_iss, iss_retido,
                  valor_liquido, discriminacao
      - versao (int — cursor incremental)
      - url_xml (opcional — se presente, `gov_fetch` baixa)

    NUNCA inclui Authorization/token. Preserva `raw_json_focus` sanitizado.
    """
    if not isinstance(item, dict):
        raise ValueError(f"item nao e dict: {type(item).__name__}")

    chave = _get_str(item, "chave")
    if not chave:
        raise ValueError("chave NFSe ausente no item Focus")

    status_raw = item.get("status")
    try:
        status_int = int(status_raw) if status_raw is not None else 1
    except (TypeError, ValueError):
        status_int = 1

    if status_int == 2:
        situacao_nfse, cancelado_r, substituido_r = "cancelada", 1, 0
    elif status_int == 3:
        situacao_nfse, cancelado_r, substituido_r = "substituida", 0, 1
    else:
        situacao_nfse, cancelado_r, substituido_r = "autorizada", 0, 0

    prestador = item.get("prestador") if isinstance(item.get("prestador"), dict) else {}
    tomador   = item.get("tomador")   if isinstance(item.get("tomador"),   dict) else {}
    # `servicos` pode vir como dict (comportamento legado) OU list de objetos
    # (schema oficial FocusNFe). Normaliza para dict antes de acessar campos.
    # Sem essa normalizacao, listas eram descartadas e todos os campos
    # financeiros/fiscais viravam vazios silenciosamente.
    servicos = _normalizar_servicos_nfse(item.get("servicos"))

    def _doc_e_tipo(entidade: dict) -> tuple[str, str]:
        """Extrai (documento_digitos, tipo). Aceita `cnpj` ou `cpf`."""
        cnpj = str(entidade.get("cnpj") or "").strip()
        if cnpj:
            return _get_str({"v": cnpj}, "v"), "cnpj"
        cpf = str(entidade.get("cpf") or "").strip()
        if cpf:
            return _get_str({"v": cpf}, "v"), "cpf"
        # Documento pode vir consolidado em `cpf_cnpj`
        cc = str(entidade.get("cpf_cnpj") or "").strip()
        if cc:
            tipo_h = "cpf" if len(cc) <= 11 else "cnpj"
            return cc, tipo_h
        return "", ""

    prest_doc, prest_tipo = _doc_e_tipo(prestador)
    tom_doc,   tom_tipo   = _doc_e_tipo(tomador)

    # Normaliza documentos para so digitos (mesmo padrao do _mapear_nfe_focus)
    import re as _re
    prest_doc = _re.sub(r"\D", "", prest_doc)
    tom_doc   = _re.sub(r"\D", "", tom_doc)

    versao_raw = item.get("versao") or 0
    try:
        versao = int(versao_raw)
    except (TypeError, ValueError):
        versao = 0

    v_servicos    = _get_str(servicos, "valor_servicos")
    v_iss         = _get_str(servicos, "valor_iss")
    v_liquido     = _get_str(servicos, "valor_liquido")
    # iss_retido normalizado para bool. Antes era string via _get_str, o que
    # deixava "False" (truthy) causar leitura errada em consumidores. Agora
    # aceita bool/int/float/string (ver `_normalizar_iss_retido_nfse`).
    iss_retido    = _normalizar_iss_retido_nfse(servicos.get("iss_retido"))
    discriminacao = _get_str(servicos, "discriminacao")
    item_lista_servico = _get_str(servicos, "item_lista_servico")
    codigo_cnae   = _get_str(servicos, "codigo_cnae")

    dh_emi = _get_str(item, "data_emissao")

    doc = {
        "ok":              True,
        "type":            "nfse",
        "doc_type":        "nfse",
        "trace_id":        trace_id,
        "chave":           chave,
        "chNFe":           chave,           # compat com consumers que leem chNFe
        "numero":          _get_str(item, "numero"),
        "serie":           _get_str(item, "serie"),
        "codigo_verificacao": _get_str(item, "codigo_verificacao"),
        "versao":          versao,
        "competencia":     _get_str(item, "competencia"),
        # Prestador → emit_* (fornecedor da NFSe recebida).
        "emit_cnpj":       prest_doc,
        "emit_doc_tipo":   prest_tipo,
        "emit_nome":       _get_str(prestador, "razao_social", "nome_fantasia"),
        "emit_ie":         _get_str(prestador, "inscricao_municipal"),
        # Tomador → dest_* (tenant nesta fase — NFSe recebida).
        "dest_cnpj":       tom_doc,
        "dest_doc_tipo":   tom_tipo,
        "dest_nome":       _get_str(tomador, "razao_social"),
        # Datas / valores.
        "dh_emi":          dh_emi,
        "dh_emi_utc":      dh_emi[:19] if dh_emi else "",
        "valor_total":     v_servicos,
        "valor_iss":       v_iss,
        "valor_liquido":   v_liquido,
        "iss_retido":      iss_retido,
        "discriminacao":   discriminacao,
        "item_lista_servico": item_lista_servico,
        "codigo_cnae":     codigo_cnae,
        "xinf":            discriminacao[:500] if discriminacao else "",
        # Status/situacao NFSe — nao usar cStat SEFAZ.
        "status_xml":      "RESUMO",         # promovido a COMPLETO pelo gov_fetch
        "situacao_nfse":   situacao_nfse,
        "cancelado":       cancelado_r,
        "substituido":     substituido_r,
        "url_xml":         _get_str(item, "url_xml"),
        # Rastreabilidade / persistencia.
        "import_origin":   "fiscalone_focusnfe_nfse",
        "status_sefaz":    "focusnfe",
        "parser_version":  "focus_nfse_v1",
        "raw_json_focus":  _dump_focus_json(item),
    }
    return doc


# ── Provider ──────────────────────────────────────────────────────────────────
class FocusNFeProvider(GovProvider):
    def __init__(self, token: str | None = None):
        """Fase D — provider aceita token injetado por requisicao.

        Precedencia: token injetado no construtor > env FOCUSNFE_TOKEN > vazio.
        Sem mutacao de `self._token` em metodos (uma instancia por request via
        `get_provider(...)` em app.py).
        """
        injetado = (token or "").strip() if token is not None else ""
        self._token = injetado or os.environ.get("FOCUSNFE_TOKEN", "")
        self._base_url_env = os.environ.get("FOCUSNFE_BASE_URL", "").strip()
        # base_url resolvido lazy no metodo para respeitar ambiente do payload
        try:
            self._timeout = int(os.environ.get("FOCUSNFE_TIMEOUT", "30"))
        except (TypeError, ValueError):
            self._timeout = 30

    # ── Fail-fast local (usado por gov_fetch/baixar_danfe) ─────────────────
    def _require_token(self) -> str:
        if not self._token:
            raise RuntimeError(
                "FOCUSNFE_TOKEN obrigatorio para provider focusnfe."
            )
        return self._token

    def _base_url_for(self, ambiente: str | None) -> str:
        """Retorna a base URL SEM `/v2` — a concatenacao das rotas adiciona."""
        if self._base_url_env:
            return _normalizar_base_url(self._base_url_env)
        return _resolve_base_url(ambiente)

    # ── gov_fetch — HTTP real ──────────────────────────────────────────────
    def gov_fetch(self, payload: dict, trace_id: str) -> dict:
        """Consulta lote incremental de NF-e recebidas via FocusNFe.

        payload:
          - cnpj (str, 14 digitos) — obrigatorio
          - tipo (str) — deve ser 'nfe' nesta fase
          - ambiente (str) — 'producao' | 'homologacao' (default homologacao)
          - ultimo_nsu (str|int) — cursor 'versao' (Focus). Default '0'.

        Retorno:
          Envelope com documentos[], resumos[] (vazio nesta fase),
          erros[], ultimo_nsu, max_nsu, cursor_tipo='versao', nsu_avancou.
        """
        payload = payload or {}
        tipo = str(payload.get("tipo") or "").lower().strip()
        cnpj = str(payload.get("cnpj") or payload.get("cnpj_tenant") or "").strip()
        ambiente = str(payload.get("ambiente") or "").strip().lower() or None
        ultimo_nsu_entrada = payload.get("ultimo_nsu")
        if ultimo_nsu_entrada is None:
            ultimo_nsu_entrada = "0"
        versao_entrada = str(ultimo_nsu_entrada).strip() or "0"

        # ── Validacoes ────────────────────────────────────────────────────
        if tipo not in ("nfe", "nfse"):
            # Fase E4c — FocusNFe suporta nfe (NF-e recebida) e nfse
            # (NFSe Nacional recebida). CT-e e MDF-e continuam nao
            # suportados pelo FocusNFe (delegar a SEFAZ/outros providers).
            return _envelope_erro(
                trace_id, "FOCUS_TIPO_NAO_SUPORTADO",
                "FocusNFe suporta apenas tipo='nfe' ou 'nfse'.",
                {"ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if not cnpj:
            return _envelope_erro(
                trace_id, "FOCUS_BAD_REQUEST",
                "cnpj obrigatorio no payload.",
                {"ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return _envelope_erro(
                trace_id, "FOCUS_TOKEN_AUSENTE", str(exc),
                {"ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )

        base_url = self._base_url_for(ambiente)
        # Fase E4c — rota canonica por tipo. NFSe usa endpoint separado
        # `/v2/nfses_recebidas` (doc oficial). Cursor `versao` incremental
        # eh comum aos dois — nao ha divergencia de contrato.
        if tipo == "nfse":
            url = f"{base_url}/v2/nfses_recebidas"
        else:
            url = f"{base_url}/v2/nfes_recebidas"
        headers = {
            **_basic_auth_header(token),
            "Accept": "application/json",
        }
        params = {"cnpj": cnpj, "versao": versao_entrada}
        # Fase E4c — NFSe Nacional recebida via Focus vem completa quando
        # `completa=1` (doc oficial). Sem esse flag, so viria resumo.
        if tipo == "nfse":
            params["completa"] = "1"

        # ── HTTP ──────────────────────────────────────────────────────────
        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=self._timeout)
        except requests.exceptions.Timeout:
            return _envelope_erro(
                trace_id, "FOCUS_TIMEOUT",
                f"Timeout ao consultar FocusNFe ({self._timeout}s).",
                {"ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        except requests.exceptions.ConnectionError as exc:
            return _envelope_erro(
                trace_id, "FOCUS_UNAVAILABLE",
                f"Falha de conexao com FocusNFe: {type(exc).__name__}.",
                {"ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        except requests.exceptions.RequestException as exc:
            return _envelope_erro(
                trace_id, "FOCUS_HTTP_ERROR",
                f"Erro HTTP inesperado: {type(exc).__name__}.",
                {"ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )

        # ── HTTP status ──────────────────────────────────────────────────
        status_code = resp.status_code
        if status_code == 400:
            return _envelope_erro(
                trace_id, "FOCUS_BAD_REQUEST",
                "FocusNFe rejeitou o payload (400).",
                {"http_status": 400,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if status_code == 401:
            return _envelope_erro(
                trace_id, "FOCUS_AUTH_ERROR",
                "Token FocusNFe invalido (401).",
                {"http_status": 401,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if status_code == 403:
            # Fase E4c — Focus devolve `{"codigo":"empresa_nao_habilitada",
            # "mensagem":"..."}` em 403 quando o CNPJ nao esta habilitado
            # para NFSe Nacional (habilitacao operacional via suporte
            # Focus). Traduzido para codigo canonico dedicado para o
            # operador identificar a acao (contato Focus, nao retry).
            focus_codigo = ""
            try:
                _body_403 = resp.json()
                if isinstance(_body_403, dict):
                    focus_codigo = str(_body_403.get("codigo") or "").strip().lower()
            except (ValueError, TypeError):
                focus_codigo = ""
            if focus_codigo == "empresa_nao_habilitada":
                return _envelope_erro(
                    trace_id, "FOCUS_NFSE_NAO_HABILITADA",
                    "Empresa nao habilitada no FocusNFe para NFSe Nacional. "
                    "Contate o suporte Focus para habilitar o CNPJ antes de "
                    "acionar buscas.",
                    {"http_status": 403,
                     "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
                )
            return _envelope_erro(
                trace_id, "FOCUS_FORBIDDEN",
                "FocusNFe negou acesso ao recurso (403).",
                {"http_status": 403,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if status_code == 429:
            retry_after_raw = resp.headers.get("Retry-After", "60")
            try:
                retry_after = int(retry_after_raw)
                if retry_after <= 0:
                    retry_after = 60
            except (TypeError, ValueError):
                retry_after = 60
            return _envelope_erro(
                trace_id, "FOCUS_RATE_LIMIT",
                "Rate limit da FocusNFe atingido (429).",
                {"http_status": 429,
                 "cooldown_recomendado_seg": retry_after,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if status_code >= 500:
            return _envelope_erro(
                trace_id, "FOCUS_SERVER_ERROR",
                f"FocusNFe respondeu erro de servidor ({status_code}).",
                {"http_status": status_code,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if status_code != 200:
            return _envelope_erro(
                trace_id, "FOCUS_HTTP_ERROR",
                f"Status HTTP inesperado da FocusNFe ({status_code}).",
                {"http_status": status_code,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )

        # ── Parse JSON ────────────────────────────────────────────────────
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            return _envelope_erro(
                trace_id, "FOCUS_PARSE_ERROR",
                "Resposta FocusNFe nao e JSON valido.",
                {"http_status": 200,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )
        if not isinstance(body, list):
            return _envelope_erro(
                trace_id, "FOCUS_SCHEMA_ERROR",
                "Resposta FocusNFe deveria ser lista JSON.",
                {"http_status": 200,
                 "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
            )

        total_count_hdr = resp.headers.get("X-Total-Count")
        try:
            total_count = int(total_count_hdr) if total_count_hdr else len(body)
        except (TypeError, ValueError):
            total_count = len(body)

        # ── Mapper ────────────────────────────────────────────────────────
        # Dispatch por tipo: NF-e usa `_mapear_nfe_focus`, NFSe usa
        # `_mapear_nfse_focus` (contrato distinto — sem cStat SEFAZ, sem
        # DV DFe 44, prestador/tomador em vez de emit/dest classicos).
        mapper = _mapear_nfse_focus if tipo == "nfse" else _mapear_nfe_focus
        documentos: list[dict] = []
        erros: list[dict] = []
        max_versao_itens = 0
        for idx, item in enumerate(body):
            try:
                doc = mapper(item, trace_id)
            except Exception as exc:
                erros.append({
                    "ok":       False,
                    "codigo":   "FOCUS_ITEM_INVALIDO",
                    "erro":     f"{type(exc).__name__}: {exc}",
                    "indice":   idx,
                    "provider": "focusnfe",
                })
                continue
            documentos.append(doc)
            v = int(doc.get("versao") or 0)
            if v > max_versao_itens:
                max_versao_itens = v

        # ── XML completo por chave / url_xml (Fase E4a + E4c) ────────────
        # NF-e (E4a): busca XML por chave via GET /nfes_recebidas/{chave}.xml
        #             quando `nfe_completa=True`.
        # NFSe (E4c): busca XML via URL fornecida em `url_xml` (a rota
        #             `/nfses_recebidas/{chave}.xml` NAO faz parte do
        #             contrato oficial). Falha individual nunca derruba
        #             batch — vira RESUMO + xml_pending.
        # Nota CANCELADA (NF-e) nao baixa XML nesta fase — E4b.
        # NFSe status 2/3 (cancelada/substituida) tambem nao baixa —
        # substituicao/cancelamento demandam evento separado (fora do E4c).
        xml_baixados = 0
        xml_pendentes = 0
        for doc in documentos:
            if doc.get("cancelado") == 1:
                continue
            if doc.get("substituido") == 1:
                continue
            if tipo == "nfse":
                # NFSe: url_xml opcional; sem url → RESUMO permanente.
                url_xml = doc.get("url_xml")
                if not url_xml:
                    continue
                if xml_baixados >= _XML_BATCH_CAP:
                    doc["xml_pending"] = True
                    xml_pendentes += 1
                    continue
                res = self.baixar_xml_nfse(url_xml)
                if res.get("ok"):
                    doc["xml_bruto"]       = res["xml_bruto"]
                    doc["xml_hash_sha256"] = res["xml_hash_sha256"]
                    doc["status_xml"]      = "COMPLETO"
                    xml_baixados += 1
                else:
                    doc["xml_pending"] = True
                    xml_pendentes += 1
                continue
            # NF-e (fluxo E4a existente)
            if not doc.get("nfe_completa"):
                continue
            if xml_baixados >= _XML_BATCH_CAP:
                doc["xml_pending"] = True
                xml_pendentes += 1
                continue
            res = self.baixar_xml_completo(doc["chNFe"], ambiente)
            if res.get("ok"):
                doc["xml_bruto"]       = res["xml_bruto"]
                doc["xml_hash_sha256"] = res["xml_hash_sha256"]
                doc["status_xml"]      = "COMPLETO"
                doc["xMotivo"]         = "Autorizado"
                xml_baixados += 1
            else:
                doc["xml_pending"] = True
                xml_pendentes += 1

        # ── Cursor ────────────────────────────────────────────────────────
        max_version_hdr = resp.headers.get("X-Max-Version")
        if max_version_hdr:
            ultimo_nsu = str(max_version_hdr).strip()
        elif max_versao_itens > 0:
            ultimo_nsu = str(max_versao_itens)
        else:
            ultimo_nsu = versao_entrada

        return {
            "ok":              True,
            "provider":        "focusnfe",
            "trace_id":        trace_id,
            "documentos":      documentos,
            "resumos":         [],
            "erros":           erros,
            "ultimo_nsu":      ultimo_nsu,
            "max_nsu":         ultimo_nsu,
            "cursor_tipo":     "versao",
            "nsu_avancou":     ultimo_nsu != versao_entrada,
            "total_count":     total_count,
            "http_status":     200,
            # Fase E4a — telemetria de batch XML.
            "xmls_baixados":   xml_baixados,
            "xmls_pendentes":  xml_pendentes,
        }

    # ── consultar_dfe_nsu — delegacao para gov_fetch ─────────────────────
    def consultar_dfe_nsu(self, cert_pem, key_pem, cnpj, nsu, ambiente, trace_id):
        """FocusNFe nao usa mTLS/cert. Delegacao para gov_fetch por completude
        do contrato GovProvider — cert_pem/key_pem sao ignorados."""
        payload = {
            "cnpj":       cnpj,
            "tipo":       "nfe",
            "ambiente":   ambiente,
            "ultimo_nsu": nsu,
        }
        return self.gov_fetch(payload, trace_id)

    # ── baixar_danfe — HTTP real, redirect 302 sem Authorization ──────────
    def baixar_danfe(self, chave: str, ambiente: str | None = None) -> dict:
        """Baixa DANFE PDF da FocusNFe.

        Fluxo:
          1. GET {base_url}/nfes_recebidas/{chave}.pdf COM Authorization,
             allow_redirects=False.
          2. Se 302, ler Location; segundo GET SEM Authorization.
          3. Se 200 direto, aceitar bytes.
          4. Calcular sha256, mime, tamanho.

        Retorno OK: {ok, bytes, sha256, mime, tamanho}
        Retorno erro: envelope com codigo controlado.
        """
        chave = str(chave or "").strip()
        if not chave:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "chave obrigatoria.",
            }
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_TOKEN_AUSENTE",
                "erro":     str(exc),
            }
        base_url = self._base_url_for(ambiente)
        url = f"{base_url}/v2/nfes_recebidas/{chave}.pdf"
        headers_auth = {**_basic_auth_header(token), "Accept": "application/pdf"}
        try:
            resp = requests.get(url, headers=headers_auth, allow_redirects=False,
                                timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "DANFE_REQUEST_ERROR",
                "erro":     f"Erro HTTP: {type(exc).__name__}.",
            }

        # 302 — segundo GET SEM Authorization
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "").strip()
            if not location:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "DANFE_NO_LOCATION",
                    "erro":     f"Redirect {resp.status_code} sem Location.",
                }
            try:
                # CRITICO: nao enviar Authorization no segundo GET (URL pre-assinada)
                resp2 = requests.get(location, headers={"Accept": "application/pdf"},
                                     allow_redirects=False, timeout=self._timeout)
            except requests.exceptions.RequestException as exc:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "DANFE_DOWNLOAD_ERROR",
                    "erro":     f"Erro no download pre-assinado: {type(exc).__name__}.",
                }
            if resp2.status_code != 200:
                return {
                    "ok":          False,
                    "provider":    "focusnfe",
                    "codigo":      "DANFE_HTTP_ERROR",
                    "erro":        f"Storage devolveu {resp2.status_code}.",
                    "http_status": resp2.status_code,
                }
            body = resp2.content
            mime = resp2.headers.get("Content-Type", "application/pdf").split(";")[0].strip()
        elif resp.status_code == 200:
            body = resp.content
            mime = resp.headers.get("Content-Type", "application/pdf").split(";")[0].strip()
        else:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "DANFE_UNEXPECTED_HTTP",
                "erro":        f"Status HTTP inesperado ({resp.status_code}).",
                "http_status": resp.status_code,
            }

        sha256 = hashlib.sha256(body).hexdigest()
        return {
            "ok":       True,
            "provider": "focusnfe",
            "bytes":    body,
            "sha256":   sha256,
            "mime":     mime,
            "tamanho":  len(body),
        }

    # ── baixar_xml_completo — nfeProc XML por chave (Fase E4a) ─────────────
    def baixar_xml_completo(self, chave: str, ambiente: str | None = None) -> dict:
        """Baixa XML nfeProc da FocusNFe pelo endpoint separado.

        Endpoint: GET {base_url}/v2/nfes_recebidas/{chave}.xml
        Headers:  Authorization Basic + Accept: application/xml
        Sem redirect (diferente do DANFE — Focus entrega XML direto).
        Timeout curto (min(self._timeout, 5)) para nao travar batch.

        Retorno OK:  {ok, provider, xml_bruto, xml_hash_sha256, tamanho}
        Retorno erro:{ok:False, provider, codigo, erro} — token nunca vaza.
        """
        chave = str(chave or "").strip()
        if not chave:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "chave obrigatoria.",
            }
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_TOKEN_AUSENTE",
                "erro":     str(exc),
            }
        base_url = self._base_url_for(ambiente)
        url = f"{base_url}/v2/nfes_recebidas/{chave}.xml"
        headers_auth = {**_basic_auth_header(token), "Accept": "application/xml"}
        # Timeout curto para evitar travar batch de ate 25 XMLs.
        timeout_xml = min(self._timeout, 5) if self._timeout else 5
        try:
            resp = requests.get(url, headers=headers_auth,
                                allow_redirects=False, timeout=timeout_xml)
        except requests.exceptions.Timeout:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_TIMEOUT",
                "erro":     f"Timeout ({timeout_xml}s) baixando XML nfeProc.",
            }
        except requests.exceptions.RequestException as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_ERRO",
                "erro":     f"Erro HTTP: {type(exc).__name__}.",
            }
        finally:
            # Defesa em profundidade — descartar refs a token/header apos uso.
            del token
            del headers_auth

        if resp.status_code == 404:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_NAO_ENCONTRADO",
                "erro":        "XML nfeProc nao encontrado no FocusNFe (404).",
                "http_status": 404,
            }
        if resp.status_code != 200:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_HTTP_ERROR",
                "erro":        f"Status HTTP inesperado ({resp.status_code}).",
                "http_status": resp.status_code,
            }
        body = resp.text or ""
        if not body:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_VAZIO",
                "erro":     "Focus devolveu corpo vazio para XML.",
            }
        sha256 = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        return {
            "ok":              True,
            "provider":        "focusnfe",
            "xml_bruto":       body,
            "xml_hash_sha256": sha256,
            "tamanho":         len(body),
        }

    # ── baixar_xml_nfse — nfse XML via url_xml (Fase E4c) ────────────────
    def baixar_xml_nfse(self, url_xml: str) -> dict:
        """Baixa XML NFSe Nacional a partir da `url_xml` fornecida pelo item
        da listagem `/v2/nfses_recebidas`.

        Diferencas vs `baixar_xml_completo` (NF-e por chave):
        - URL nao eh construida — vem do proprio item Focus.
        - Comportamento de redirect: se URL retornada for pre-assinada
          (ex.: storage externo), o segundo GET pode nao aceitar
          Authorization. Padrao E4c: **envia Authorization no primeiro
          GET**; se receber 3xx sem Location, retorna erro estruturado.
        - Timeout curto (min(self._timeout, 5)) — evita travar batch.

        Retorno OK:  {ok, provider, xml_bruto, xml_hash_sha256, tamanho}
        Retorno erro:{ok:False, provider, codigo, erro} — token nao vaza.
        """
        url = str(url_xml or "").strip()
        if not url:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "url_xml obrigatoria.",
            }
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_TOKEN_AUSENTE",
                "erro":     str(exc),
            }
        headers_auth = {**_basic_auth_header(token), "Accept": "application/xml"}
        timeout_xml = min(self._timeout, 5) if self._timeout else 5
        try:
            resp = requests.get(url, headers=headers_auth,
                                allow_redirects=False, timeout=timeout_xml)
        except requests.exceptions.Timeout:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_TIMEOUT",
                "erro":     f"Timeout ({timeout_xml}s) baixando XML NFSe.",
            }
        except requests.exceptions.RequestException as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_ERRO",
                "erro":     f"Erro HTTP: {type(exc).__name__}.",
            }
        finally:
            del token
            del headers_auth

        # Redirect: URL pre-assinada. Segundo GET SEM Authorization
        # (URL ja carrega assinatura no query string).
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "").strip()
            if not location:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "FOCUS_XML_NO_LOCATION",
                    "erro":     f"Redirect {resp.status_code} sem Location.",
                }
            try:
                resp2 = requests.get(location, headers={"Accept": "application/xml"},
                                     allow_redirects=False, timeout=timeout_xml)
            except requests.exceptions.RequestException as exc:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "FOCUS_XML_ERRO",
                    "erro":     f"Erro download pre-assinado: {type(exc).__name__}.",
                }
            if resp2.status_code != 200:
                return {
                    "ok":          False,
                    "provider":    "focusnfe",
                    "codigo":      "FOCUS_XML_HTTP_ERROR",
                    "erro":        f"Storage devolveu {resp2.status_code}.",
                    "http_status": resp2.status_code,
                }
            body = resp2.text or ""
        elif resp.status_code == 404:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_NAO_ENCONTRADO",
                "erro":        "XML NFSe nao encontrado (404).",
                "http_status": 404,
            }
        elif resp.status_code != 200:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_HTTP_ERROR",
                "erro":        f"Status HTTP inesperado ({resp.status_code}).",
                "http_status": resp.status_code,
            }
        else:
            body = resp.text or ""

        if not body:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_VAZIO",
                "erro":     "Corpo XML vazio.",
            }
        sha256 = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        return {
            "ok":              True,
            "provider":        "focusnfe",
            "xml_bruto":       body,
            "xml_hash_sha256": sha256,
            "tamanho":         len(body),
        }

    # ── manifestar_nfe_recebida — evento 210210 (Fase E4b-1A) ─────────────
    def manifestar_nfe_recebida(self, chave, tipo="ciencia",
                                ambiente=None, trace_id=None) -> dict:
        """Manifestacao de Ciencia da Operacao de NF-e recebida via FocusNFe.

        Endpoint FocusNFe: POST /v2/nfes_recebidas/{chave}/manifesto
        Body: {"tipo": "ciencia"}
        Evento SEFAZ: 210210 (Ciencia da Operacao).

        Travamentos (Fase E4b-1A):
          - `tipo` deve ser exatamente "ciencia". `confirmacao` (210200),
            `desconhecimento` (210220) e `nao_realizada` (210240) ficam
            bloqueados nesta fase — retornam FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO.
          - `chave` deve ter exatamente 44 digitos numericos — senao
            FOCUS_MANIFESTO_CHAVE_INVALIDA.

        Retorno OK (envelope canonico, sem dados sensiveis):
          {ok, provider, codigo=MANIFESTO_OK, trace_id, chave, tipo,
           evento="210210", cstat, xmotivo, protocolo, http_status}

        Retorno erro: envelope minimo `{ok:False, provider, codigo, erro,
        trace_id, http_status?}`. Token, Authorization, XML e payload bruto
        NUNCA aparecem em envelope ou log.
        """
        import logging as _logging
        _log = _logging.getLogger("fiscalone.focusnfe")

        # ── Trava 1: tipo ─────────────────────────────────────────────────
        tipo_norm = str(tipo or "").strip().lower()
        if tipo_norm != "ciencia":
            return {
                "ok":        False,
                "provider":  "focusnfe",
                "codigo":    "FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO",
                "erro":      (
                    "Apenas tipo='ciencia' (evento 210210) e suportado nesta fase. "
                    "confirmacao, desconhecimento e nao_realizada permanecem bloqueados."
                ),
                "trace_id":  trace_id,
            }

        # ── Trava 2: chave ────────────────────────────────────────────────
        chave_norm = str(chave or "").strip()
        if len(chave_norm) != 44 or not chave_norm.isdigit():
            return {
                "ok":        False,
                "provider":  "focusnfe",
                "codigo":    "FOCUS_MANIFESTO_CHAVE_INVALIDA",
                "erro":      "chave NF-e deve ter exatamente 44 digitos numericos.",
                "trace_id":  trace_id,
            }

        # ── Trava 3: token ────────────────────────────────────────────────
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return {
                "ok":        False,
                "provider":  "focusnfe",
                "codigo":    "FOCUS_TOKEN_AUSENTE",
                "erro":      str(exc),
                "trace_id":  trace_id,
            }

        # ── HTTP POST ─────────────────────────────────────────────────────
        base_url = self._base_url_for(ambiente)
        url = f"{base_url}/v2/nfes_recebidas/{chave_norm}/manifesto"
        headers = {
            **_basic_auth_header(token),
            "Content-Type": "application/json",
            "Accept":       "application/json",
        }
        body = {"tipo": "ciencia"}

        # Log INFO com chave mascarada — nunca token/Authorization/body.
        chave_mascarada = f"{chave_norm[:6]}***{chave_norm[-4:]}"
        _log.info("focusnfe.manifesto.request chave=%s tipo=ciencia trace_id=%s",
                  chave_mascarada, trace_id)

        resp = None
        try:
            try:
                resp = requests.post(url, json=body, headers=headers,
                                     timeout=self._timeout,
                                     allow_redirects=False)
            except requests.exceptions.RequestException as exc:
                _log.info(
                    "focusnfe.manifesto.http_error chave=%s tipo=%s trace_id=%s",
                    chave_mascarada, type(exc).__name__, trace_id,
                )
                return {
                    "ok":        False,
                    "provider":  "focusnfe",
                    "codigo":    "FOCUS_MANIFESTO_HTTP_ERROR",
                    "erro":      f"Erro HTTP inesperado: {type(exc).__name__}.",
                    "trace_id":  trace_id,
                }

            status_code = resp.status_code

            # 200/201/202 -> sucesso
            if status_code in (200, 201, 202):
                cstat = ""
                xmotivo = ""
                protocolo = ""
                try:
                    body_json = resp.json()
                except (ValueError, TypeError):
                    body_json = {}
                if isinstance(body_json, dict):
                    cstat     = str(body_json.get("cstat")     or body_json.get("codigo_sefaz") or "").strip()
                    xmotivo   = str(body_json.get("xmotivo")   or body_json.get("mensagem_sefaz") or "").strip()
                    protocolo = str(body_json.get("protocolo") or body_json.get("numero_protocolo") or "").strip()
                _log.info(
                    "focusnfe.manifesto.ok chave=%s cstat=%s http=%s trace_id=%s",
                    chave_mascarada, cstat or "-", status_code, trace_id,
                )
                return {
                    "ok":          True,
                    "provider":    "focusnfe",
                    "codigo":      "MANIFESTO_OK",
                    "trace_id":    trace_id,
                    "chave":       chave_norm,
                    "tipo":        "ciencia",
                    "evento":      "210210",
                    "cstat":       cstat,
                    "xmotivo":     xmotivo,
                    "protocolo":   protocolo,
                    "http_status": status_code,
                }

            # Mapeamento de erros HTTP
            mapa_erro = {
                400: ("FOCUS_MANIFESTO_INVALIDO",
                      "FocusNFe rejeitou o manifesto (400)."),
                401: ("FOCUS_AUTH_ERROR",
                      "Token FocusNFe invalido (401)."),
                403: ("FOCUS_FORBIDDEN",
                      "FocusNFe negou acesso ao manifesto (403)."),
                404: ("FOCUS_MANIFESTO_NAO_ENCONTRADO",
                      "NF-e nao encontrada para manifesto (404)."),
                409: ("FOCUS_MANIFESTO_CONFLITO",
                      "Conflito ao manifestar (409) — evento pode ja existir."),
                422: ("FOCUS_MANIFESTO_CONFLITO",
                      "Manifesto rejeitado por regra SEFAZ (422)."),
                429: ("FOCUS_RATE_LIMIT",
                      "Rate limit da FocusNFe atingido (429)."),
            }
            if status_code in mapa_erro:
                codigo, mensagem = mapa_erro[status_code]
            elif status_code >= 500:
                codigo   = "FOCUS_MANIFESTO_HTTP_ERROR"
                mensagem = f"FocusNFe respondeu erro de servidor ({status_code})."
            else:
                codigo   = "FOCUS_MANIFESTO_HTTP_ERROR"
                mensagem = f"Status HTTP inesperado ({status_code})."

            _log.info(
                "focusnfe.manifesto.erro chave=%s codigo=%s http=%s trace_id=%s",
                chave_mascarada, codigo, status_code, trace_id,
            )
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      codigo,
                "erro":        mensagem,
                "trace_id":    trace_id,
                "http_status": status_code,
            }
        finally:
            # Defesa em profundidade — descarta refs a token/headers apos uso.
            try:
                del token
            except NameError:
                pass
            try:
                del headers
            except NameError:
                pass

    # ── Rotas legadas de consulta (stubs) ──────────────────────────────────
    def sync(self, cnpj):                                        return dict(_STUB)
    def listar_nfe(self, cnpj, pagina=1):                        return dict(_STUB)
    def listar_cte(self, cnpj, pagina=1):                        return dict(_STUB)
    def detalhe_nfe(self, chave):                                return dict(_STUB)
    def detalhe_cte(self, chave):                                return dict(_STUB)
    def status_sefaz(self, uf):                                  return dict(_STUB)

    # ── Emissao — bloqueada por design (defesa em profundidade) ────────────
    def emitir_cte(self, payload):
        raise EmissaoProibida(
            "emitir_cte via FocusNFe bloqueado — FiscalOne nesta fase e apenas "
            "recebimento DFe."
        )

    def emitir_mdfe(self, payload):
        raise EmissaoProibida(
            "emitir_mdfe via FocusNFe bloqueado — FiscalOne nesta fase e apenas "
            "recebimento DFe."
        )

    # ── Operacoes relacionadas (nao sao emitir_*) — mantidas como STUB ─────
    def cancelar_cte(self, chave, justificativa):                return dict(_STUB)
    def encerrar_mdfe(self, chave):                              return dict(_STUB)
    def incluir_condutor_mdfe(self, chave, payload):             return dict(_STUB)


# Compatibilidade retro: alguns modulos importaram `FOCUSNFE_BASE_URL` como
# atributo de modulo. Preservar sem quebrar semantica anterior.
FOCUSNFE_BASE_URL = os.getenv("FOCUSNFE_BASE_URL", "https://api.focusnfe.com.br/v2")
