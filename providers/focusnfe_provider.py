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
    """Mapeia item Focus para dict compativel com NFeDoc/NFeDocOpcional.

    Tolerante a variacao de nomes de campo (Focus documenta variantes).
    Nunca inclui Authorization/token. Preserva `raw_json_focus` sanitizado.
    """
    if not isinstance(item, dict):
        raise ValueError(f"item nao e dict: {type(item).__name__}")

    # Chave (obrigatoria para NF-e recebida)
    chave = _get_str(item, "chave_nfe", "chave", "chNFe")
    if not chave:
        raise ValueError("chave NF-e ausente no item Focus")

    # XML — quando presente, item vira COMPLETO; senao RESUMO
    xml = _get_str(item, "xml", "xml_nfe", "xml_nota_fiscal")
    tem_xml = bool(xml)

    # Versao (cursor Focus)
    versao_raw = item.get("versao") or item.get("versao_nfe") or 0
    try:
        versao = int(versao_raw)
    except (TypeError, ValueError):
        versao = 0

    # Valores numericos — string ou number, deixa como veio (schema aceita)
    v_nf   = _get_str(item, "valor_total", "vNF", "valor_nfe")
    v_icms = _get_str(item, "valor_icms",  "vICMS")

    doc = {
        "chNFe":           chave,
        "nProt":           _get_str(item, "protocolo", "nProt"),
        "dhRecbto":        _get_str(item, "data_recebimento", "dhRecbto", "data_emissao"),
        "CNPJ_emit":       _get_str(item, "cnpj_emitente", "CNPJ_emit"),
        "CNPJ_dest":       _get_str(item, "cnpj_destinatario", "CNPJ_dest"),
        "vNF":             v_nf,
        "vICMS":           v_icms,
        "numero":          _get_str(item, "numero", "nNF"),
        "serie":           _get_str(item, "serie", "serie_nfe"),
        "emit_nome":       _get_str(item, "nome_emitente", "razao_social_emitente", "emit_nome"),
        "dh_emi":          _get_str(item, "data_emissao", "dh_emi"),
        "cStat":           "100" if tem_xml else "101",
        "xMotivo":         "Autorizado" if tem_xml else "Resumo FocusNFe",
        "status_xml":      "COMPLETO" if tem_xml else "RESUMO",
        "import_origin":   "fiscalone_focusnfe",
        "trace_id":        trace_id,
        "parser_version":  "focus_v2",
        # Campos opcionais Focus
        "versao":          versao,
        "raw_json_focus":  _dump_focus_json(item),
        "danfe_sha256":    "",
        "danfe_fonte":     "focusnfe",
    }
    if tem_xml:
        doc["xml_bruto"] = xml
        doc["xml_hash_sha256"] = hashlib.sha256(xml.encode("utf-8", errors="replace")).hexdigest()
    return doc


# ── Provider ──────────────────────────────────────────────────────────────────
class FocusNFeProvider(GovProvider):
    def __init__(self):
        self._token = os.environ.get("FOCUSNFE_TOKEN", "")
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

        # ── Cursor ────────────────────────────────────────────────────────
        max_version_hdr = resp.headers.get("X-Max-Version")
        if max_version_hdr:
            ultimo_nsu = str(max_version_hdr).strip()
        elif max_versao_itens > 0:
            ultimo_nsu = str(max_versao_itens)
        else:
            ultimo_nsu = versao_entrada

        return {
            "ok":           True,
            "provider":     "focusnfe",
            "trace_id":     trace_id,
            "documentos":   documentos,
            "resumos":      [],
            "erros":        erros,
            "ultimo_nsu":   ultimo_nsu,
            "max_nsu":      ultimo_nsu,
            "cursor_tipo":  "versao",
            "nsu_avancou":  ultimo_nsu != versao_entrada,
            "total_count":  total_count,
            "http_status":  200,
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
