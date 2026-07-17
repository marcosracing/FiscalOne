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
        if tipo != "nfe":
            return _envelope_erro(
                trace_id, "FOCUS_TIPO_NAO_SUPORTADO",
                "FocusNFe nesta fase suporta apenas tipo='nfe'.",
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
        url = f"{base_url}/v2/nfes_recebidas"
        headers = {
            **_basic_auth_header(token),
            "Accept": "application/json",
        }
        params = {"cnpj": cnpj, "versao": versao_entrada}

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
        documentos: list[dict] = []
        erros: list[dict] = []
        max_versao_itens = 0
        for idx, item in enumerate(body):
            try:
                doc = _mapear_nfe_focus(item, trace_id)
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

        # ── XML completo por chave (Fase E4a) ────────────────────────────
        # Para itens com nfe_completa=True, baixa nfeProc via endpoint
        # separado /nfes_recebidas/{chave}.xml. Cap duro `_XML_BATCH_CAP`
        # para nao estourar tempo; excedentes viram RESUMO + xml_pending.
        # Nota CANCELADA nao baixa XML nesta fase — evento/XML de
        # cancelamento fica para E4b (documentado no handoff).
        # Falha individual (404/timeout) nunca derruba batch — item vira
        # RESUMO + xml_pending, log warn estruturado sem token.
        xml_baixados = 0
        xml_pendentes = 0
        for doc in documentos:
            if doc.get("cancelado") == 1:
                # Preserva situacao/data_cancelamento/justificativa. XML
                # de cancelamento fica para E4b.
                continue
            if not doc.get("nfe_completa"):
                continue  # RESUMO (correto — Focus ainda nao tem XML).
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
                # Log estruturado sem token/chave nao vaza credenciais.
                # (Usa print em modulo puro — evita dependencia de logging
                # nao configurado nos consumidores. Consumidor decide.)
                # NOTA: nao usar logger aqui — modulo puro nao configura
                # handlers; consumidor (app.py) faz a captura via stderr.

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
