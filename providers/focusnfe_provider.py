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
import re
import urllib.parse
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


def _parse_retry_after_int(raw: object) -> int | None:
    """Normaliza header Retry-After somente quando for inteiro positivo.

    G0.2a-R2: nao interpreta data HTTP (RFC 7231); header cru nunca e
    repassado ao cliente. Retorna None se ausente/negativo/nao-inteiro,
    para que caller use apenas politica local.
    """
    if raw is None:
        return None
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


# ── Helpers de configuracao ───────────────────────────────────────────────────
# Bases oficiais sem `/v2`. O prefixo `/v2` e concatenado nas rotas para
# garantir montagem correta independente de como FOCUSNFE_BASE_URL for
# fornecido pelo operador (com ou sem `/v2` no final).
_FOCUSNFE_HOSTS = {
    "producao":     "https://api.focusnfe.com.br",
    "homologacao":  "https://homologacao.focusnfe.com.br",
}

_XML_REDIRECT_HOSTS_ENV = "FISCALONE_XML_REDIRECT_HOSTS"


def _xml_redirect_location_permitida(location: str, original_url: str) -> bool:
    """Valida redirect HTTPS contra allowlist nominal de hosts."""
    try:
        destino = urllib.parse.urlsplit(location)
        origem = urllib.parse.urlsplit(original_url)
        porta = destino.port
    except (TypeError, ValueError):
        return False
    if (
        destino.scheme.lower() != "https"
        or not destino.hostname
        or destino.username is not None
        or destino.password is not None
        or destino.fragment
        or porta not in (None, 443)
    ):
        return False
    permitidos = {str(origem.hostname or "").lower()}
    permitidos.update(
        host.strip().lower()
        for host in os.environ.get(_XML_REDIRECT_HOSTS_ENV, "").split(",")
        if host.strip()
    )
    return destino.hostname.lower() in permitidos


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


# ── Validacao estrita de `versao` FocusNFe (rev.3, 2026-07-24) ────────
# Cursor FocusNFe e' `versao` (inteiro monotonico). Item cuja versao nao
# possa ser confirmada como inteiro positivo NAO pode ser contabilizado
# — o cursor seguro nao pode ultrapassa-lo. Esta funcao e' o unico
# ponto autorizado a decidir "versao valida".
#
# Aceita apenas:
#   - int > 0 (nao bool);
#   - str contendo somente digitos, com valor > 0 apos strip.
# Rejeita:
#   - None, chave ausente, "", "0", 0, negativos, bool, decimal,
#     float, texto nao conversivel, lista, dict.
# Nao inventa valor. Nao deriva de indice/X-Max-Version/chave.
def _versao_focus_valida(raw: Any) -> int | None:
    """Retorna a versao FocusNFe (int > 0) apenas quando ela puder ser
    confirmada com seguranca. Qualquer input ambiguo devolve `None`."""
    if raw is None:
        return None
    # `bool` e' subclasse de `int` — bloqueia antes do isinstance(int).
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str):
        s = raw.strip()
        if not s or not s.isdigit():
            return None
        try:
            n = int(s)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None
    # float/decimal/list/dict/tuple/set/etc.
    return None


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


# ── Mapper NFSe · dois layouts convivendo (2026-07-31 R2) ────────────────────
#
# A listagem `GET /v2/nfsens_recebidas` da FocusNFe retorna, em produção
# real, o layout **DPS Nacional 2026** (padrão nacional NFS-e /
# DPS + DF-e). A documentação pública em
# `https://doc.focusnfe.com.br/reference/consultar_nfsen_recebidas`
# descreve o layout **municipal legado** (`chave_nfse`, `situacao`
# textual, prestador aninhado, etc.). Este mapper aceita **os dois** e
# marca o layout escolhido em `_layout_focus`.
#
# **Layout DPS Nacional 2026** (real — confirmado por prova operacional
# 2026-07-31):
#
#   - `numero_dfse` (identidade DF-e nacional; opaca);
#   - `id_dps` (fallback de identidade interno FocusNFe);
#   - `versao` (int incremental — cursor opaco);
#   - `cnpj_prestador`, `razao_social_prestador`,
#     `inscricao_municipal_prestador` (planos no root);
#   - `cnpj_tomador`, `razao_social_tomador` (planos no root);
#   - `valor_servico`, `valor_liquido`, `iss_valor` (planos no root);
#   - `data_emissao`, `data_processo`, `data_competencia`;
#   - `numero_dps`, `serie_dps`, `numero`;
#   - `descricao_servico`;
#   - `documentos` (aninhado — refs a documentos filhos, incluindo
#     eventuais cancelamentos/substituições).
#
# Situação no layout DPS: a listagem só devolve documentos válidos.
# Cancelamento e substituição são inferidos por **sinais explícitos**
# (`data_cancelamento`, `chave_nfse_substituida`/`chave_substituida`).
# Sem sinais → default `autorizada` — nunca por chute silencioso; o
# schema DPS é auto-descritivo neste ponto.
#
# **Layout municipal legado** (documentação Focus 2026):
#
#   - `chave_nfse` (identidade opaca);
#   - `situacao` (textual): "autorizado" | "cancelado" | "substituido";
#   - `nome_prestador`, `documento_prestador` (planos);
#   - `nome_tomador`, `documento_tomador` (planos);
#   - `valor_total`, `valor_iss`, `valor_liquido` (planos);
#   - `data_emissao`, `data_geracao`;
#   - opcionais: `data_cancelamento`, `chave_nfse_substituida`,
#                `numero`, `serie`, `codigo_verificacao`, `competencia`.
#
# **Adaptador histórico explícito** (`_layout_focus="legacy"`): itens
# antigos com `chave`/`chNFe`/`chave_nfe`, `status` numérico (1/2/3),
# ou dicts aninhados `prestador`/`tomador`/`servicos` são aceitos por
# adaptador nomeado — nunca por leitura silenciosa mista.
#
# Regras invariantes:
#   - Ausência de identidade (nenhum dos quatro nomes) → `ValueError`.
#   - `versao` ausente/inválida → `ValueError`.
#   - Situação textual desconhecida no layout `oficial`/`legacy` →
#     `ValueError` (nunca convertida em "autorizado").
#   - No layout DPS, ausência de sinais de cancelamento/substituição
#     → default `autorizada` (design da API — a listagem só devolve
#     documentos válidos).
#   - Consumidor bloqueia cursor antes do item que levantar `ValueError`.
_SITUACAO_NFSE_MAP = {
    # oficial textual → (situacao_canonica, cancelado, substituido)
    "autorizado":  ("autorizada",  0, 0),
    "autorizada":  ("autorizada",  0, 0),
    "cancelado":   ("cancelada",   1, 0),
    "cancelada":   ("cancelada",   1, 0),
    "substituido": ("substituida", 0, 1),
    "substituída": ("substituida", 0, 1),
    "substituida": ("substituida", 0, 1),
}

# Adaptador legacy — `status` numérico do layout histórico. Nunca é
# tratado como contrato oficial; só existe para não perder documentos
# de payloads antigos que ainda estejam em trânsito.
_SITUACAO_NFSE_STATUS_INT = {
    1: "autorizado",
    2: "cancelado",
    3: "substituido",
}


def _digitos_documento(v: Any) -> str:
    """Retorna apenas os dígitos de `v` (para CNPJ/CPF)."""
    import re as _re
    return _re.sub(r"\D", "", str(v or ""))


def _mapear_nfse_focus(item: dict, trace_id: str) -> dict:
    """Mapeia item da listagem `/v2/nfsens_recebidas` (contrato oficial
    FocusNFe) para dict canônico consumido pelo MapOne.

    Preferência estrita pelo contrato oficial:
      - `chave_nfse` como identidade;
      - `situacao` textual como fonte de estado;
      - campos planos `nome_prestador`/`documento_prestador`/
        `valor_total`/`data_emissao`/`data_geracao` no root.

    Adaptador legacy explícito (sinalizado em `_layout_focus`):
      - `chave` como identidade;
      - `status` numérico (1/2/3) traduzido para situação textual;
      - dicts aninhados `prestador`/`tomador`/`servicos`.

    Nunca inventa situação. Nunca decodifica payload fiscal para
    reconstruir Espelho. `raw_json_focus` é sanitizado (sem token).
    """
    if not isinstance(item, dict):
        raise ValueError(f"item nao e dict: {type(item).__name__}")

    # ── Identidade ────────────────────────────────────────────────────
    # Precedência:
    #   1. `chave_nfse`             — layout municipal legado (Focus doc).
    #   2. `chave`/`chNFe`/`chave_nfe` — retrocompat (marca `legacy`).
    #   3. `numero_dfse`            — layout DPS Nacional 2026 (real).
    #   4. `id_dps`                 — fallback DPS (id interno FocusNFe).
    chave = _get_str(item, "chave_nfse")
    layout = "oficial"
    if not chave:
        chave = _get_str(item, "chave", "chNFe", "chave_nfe")
        if chave:
            layout = "legacy"
    if not chave:
        chave = _get_str(item, "numero_dfse")
        if chave:
            layout = "dps_nacional"
    if not chave:
        chave = _get_str(item, "id_dps")
        if chave:
            layout = "dps_nacional"
    if not chave:
        raise ValueError(
            "identidade NFS-e ausente no item Focus (nem chave_nfse, chave, "
            "numero_dfse, id_dps)"
        )

    # ── Versão (cursor opaco — obrigatória por doc) ───────────────────
    # `_versao_focus_valida` já é a decisão canônica de "versão válida";
    # aqui rejeitamos antes de qualquer default silencioso para 0.
    versao_valida = _versao_focus_valida(item.get("versao"))
    if versao_valida is None:
        raise ValueError("versao FocusNFe ausente/invalida no item NFS-e")
    versao = versao_valida

    # ── Situação ──────────────────────────────────────────────────────
    # Layout oficial/legacy: `situacao` textual (root) ou `status` int.
    # Layout DPS Nacional: sinais explícitos no root (`data_cancelamento`,
    # `chave_nfse_substituida`); ausência → default `autorizada` (design
    # da API — a listagem só retorna documentos válidos).
    situacao_raw = _get_str(item, "situacao").strip().lower()
    if not situacao_raw:
        status_int_raw = item.get("status")
        if status_int_raw is not None:
            try:
                si = int(status_int_raw)
                situacao_raw = _SITUACAO_NFSE_STATUS_INT.get(si, "")
                if situacao_raw:
                    layout = "legacy"
            except (TypeError, ValueError):
                situacao_raw = ""
    if not situacao_raw and layout == "dps_nacional":
        # Sinais nominais de estado no layout DPS. Cancelamento e
        # substituição vêm de campos explícitos; ausência → autorizada.
        if _get_str(item, "data_cancelamento"):
            situacao_raw = "cancelado"
        elif _get_str(item, "chave_nfse_substituida", "chave_substituida"):
            situacao_raw = "substituido"
        else:
            situacao_raw = "autorizado"
    if not situacao_raw:
        raise ValueError(
            "situacao NFS-e ausente no item Focus (nem status legacy)"
        )
    mapa_sit = _SITUACAO_NFSE_MAP.get(situacao_raw)
    if mapa_sit is None:
        # NUNCA convertemos situação desconhecida em "autorizado" — o
        # consumidor precisa saber que o documento veio com estado
        # que não corresponde ao contrato oficial.
        raise ValueError(f"situacao NFS-e desconhecida: {situacao_raw!r}")
    situacao_nfse, cancelado_r, substituido_r = mapa_sit

    # ── Prestador / Tomador (planos: oficial e DPS; aninhado: legacy) ─
    # Ordem de leitura:
    #   1. `nome_prestador`/`documento_prestador` (contrato municipal
    #      oficial, planos no root);
    #   2. `cnpj_prestador`/`cpf_prestador`/`razao_social_prestador` (DPS
    #      Nacional 2026, planos no root);
    #   3. `prestador.{cnpj|cpf|razao_social}` (adaptador aninhado).
    prest_doc = _digitos_documento(
        _get_str(item, "documento_prestador", "cnpj_prestador",
                 "cpf_prestador")
    )
    # O layout DPS Nacional observado pela Focus identifica o prestador por
    # ``cnpj_prestador``, mas publica sua razão social em
    # ``razao_social_emitente``.  Não confundir com o tomador: o emitente da
    # NFS-e é o prestador do serviço.
    prest_nome = _get_str(
        item,
        "nome_prestador",
        "razao_social_prestador",
        "razao_social_emitente",
        "nome_fantasia_emitente",
    )
    prest_ie   = _get_str(item, "inscricao_municipal_prestador")
    prestador_aninh = item.get("prestador") if isinstance(item.get("prestador"), dict) else {}
    if not prest_doc:
        d = str(prestador_aninh.get("cnpj") or prestador_aninh.get("cpf")
                or prestador_aninh.get("cpf_cnpj") or "")
        prest_doc = _digitos_documento(d)
        if prest_doc:
            layout = "legacy"
    if not prest_nome:
        prest_nome = _get_str(prestador_aninh, "razao_social", "nome_fantasia")
    if not prest_ie:
        prest_ie = _get_str(prestador_aninh, "inscricao_municipal")
    prest_tipo = "cnpj" if len(prest_doc) > 11 else ("cpf" if prest_doc else "")

    tom_doc = _digitos_documento(
        _get_str(item, "documento_tomador", "cnpj_tomador", "cpf_tomador")
    )
    tom_nome = _get_str(item, "nome_tomador", "razao_social_tomador")
    tomador_aninh = item.get("tomador") if isinstance(item.get("tomador"), dict) else {}
    if not tom_doc:
        d = str(tomador_aninh.get("cnpj") or tomador_aninh.get("cpf")
                or tomador_aninh.get("cpf_cnpj") or "")
        tom_doc = _digitos_documento(d)
        if tom_doc:
            layout = "legacy"
    if not tom_nome:
        tom_nome = _get_str(tomador_aninh, "razao_social")
    tom_tipo = "cnpj" if len(tom_doc) > 11 else ("cpf" if tom_doc else "")

    # ── Valores / metadados ───────────────────────────────────────────
    # Oficial municipal: `valor_total`. DPS Nacional: `valor_servico`
    # (bruto) e `valor_liquido` (com deduções). Legacy aninhado:
    # `servicos.valor_servicos`.
    v_total = _get_str(item, "valor_total", "valor_liquido_nfse",
                       "valor_servico")
    servicos_aninh = _normalizar_servicos_nfse(item.get("servicos"))
    v_servicos = _get_str(servicos_aninh, "valor_servicos") if servicos_aninh else ""
    v_iss = (_get_str(item, "valor_iss")
             or _get_str(item, "iss_valor")
             or _get_str(servicos_aninh, "valor_iss"))
    v_liquido = _get_str(item, "valor_liquido") or _get_str(servicos_aninh, "valor_liquido")
    if v_servicos and not v_total:
        v_total = v_servicos
        layout = "legacy"

    iss_retido = _normalizar_iss_retido_nfse(
        item.get("iss_retido") if item.get("iss_retido") is not None
        else servicos_aninh.get("iss_retido")
    )
    discriminacao = (_get_str(item, "discriminacao")
                     or _get_str(item, "descricao_servico")
                     or _get_str(servicos_aninh, "discriminacao"))
    item_lista_servico = (_get_str(item, "item_lista_servico")
                          or _get_str(servicos_aninh, "item_lista_servico"))
    codigo_cnae = (_get_str(item, "codigo_cnae")
                   or _get_str(servicos_aninh, "codigo_cnae"))

    # ── Datas — oficial: `data_emissao`/`data_geracao`;
    #             DPS Nacional: `data_processo` no lugar de data_geracao.
    dh_emi     = _get_str(item, "data_emissao")
    dh_geracao = _get_str(item, "data_geracao", "data_processo")

    # ── Campos de cancelamento e substituição opcionais ───────────────
    data_cancel  = _get_str(item, "data_cancelamento")
    chave_subst  = _get_str(item, "chave_nfse_substituida", "chave_substituida")

    doc = {
        "ok":              True,
        "type":            "nfse",
        "doc_type":        "nfse",
        "trace_id":        trace_id,
        # Identidade opaca — publica os três nomes (oficial + compat).
        "chave_nfse":      chave,
        "chave":           chave,
        "chNFe":           chave,
        "numero":          _get_str(item, "numero", "numero_dfse", "numero_dps"),
        "serie":           _get_str(item, "serie", "serie_dps"),
        "codigo_verificacao": _get_str(item, "codigo_verificacao"),
        "versao":          versao,
        "competencia":     _get_str(item, "competencia", "data_competencia"),
        # Prestador → emit_* (fornecedor da NFSe recebida).
        "emit_cnpj":       prest_doc,
        "emit_doc_tipo":   prest_tipo,
        "emit_nome":       prest_nome,
        "emit_ie":         prest_ie,
        # Tomador → dest_* (tenant nesta fase — NFSe recebida).
        "dest_cnpj":       tom_doc,
        "dest_doc_tipo":   tom_tipo,
        "dest_nome":       tom_nome,
        # Datas / valores.
        "dh_emi":          dh_emi,
        "dh_emi_utc":      dh_emi[:19] if dh_emi else "",
        "data_geracao":    dh_geracao,
        "valor_total":     v_total,
        "valor_iss":       v_iss,
        "valor_liquido":   v_liquido,
        "iss_retido":      iss_retido,
        "discriminacao":   discriminacao,
        "item_lista_servico": item_lista_servico,
        "codigo_cnae":     codigo_cnae,
        "xinf":            discriminacao[:500] if discriminacao else "",
        # Status/situacao NFSe — nao usar cStat SEFAZ.
        # 2026-07-31 R2: NFS-e recebida da Focus é documento canônico
        # próprio (não "resumo de NF-e"). O payload da listagem
        # `/v2/nfsens_recebidas` já é a fonte de entrada do Espelho —
        # XML é auxiliar. Estado nominal `ESPELHO_DISPONIVEL` sinaliza
        # ao consumidor que o item pode ser persistido como Espelho
        # sem depender de XML.
        "status_xml":      "ESPELHO_DISPONIVEL",
        "xml_pending":     False,
        "situacao_nfse":   situacao_nfse,
        "situacao_focus":  situacao_raw,     # textual bruta (autorizado etc.)
        "cancelado":       cancelado_r,
        "substituido":     substituido_r,
        "data_cancelamento":       data_cancel,
        "chave_nfse_substituida":  chave_subst,
        "url_xml":         _get_str(item, "url_xml"),
        # Rastreabilidade / persistencia.
        "import_origin":   "fiscalone_focusnfe_nfse",
        "status_sefaz":    "focusnfe",
        "parser_version":  "focus_nfse_v1",
        "raw_json_focus":  _dump_focus_json(item),
        # Sinaliza se o adaptador legacy foi acionado — apenas telemetria.
        "_layout_focus":   layout,
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
        # `/v2/nfsens_recebidas` (NFSe Nacional recebida). Cursor `versao` incremental
        # eh comum aos dois — nao ha divergencia de contrato.
        if tipo == "nfse":
            url = f"{base_url}/v2/nfsens_recebidas"
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
            focus_codigo = ""
            try:
                body_400 = resp.json()
                if isinstance(body_400, dict):
                    focus_codigo = str(body_400.get("codigo") or "").strip().lower()
            except (ValueError, TypeError):
                focus_codigo = ""
            if tipo == "nfse" and focus_codigo == "empresa_nao_habilitada":
                return _envelope_erro(
                    trace_id, "FOCUS_NFSE_NAO_HABILITADA",
                    "Empresa nao habilitada no FocusNFe para NFSe Nacional. "
                    "Contate o suporte Focus para habilitar o CNPJ antes de "
                    "acionar buscas.",
                    {"http_status": 400,
                     "ultimo_nsu": versao_entrada, "max_nsu": versao_entrada},
                )
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
        # Erro de mapper NAO derruba batch; preserva `versao`/`chave` extraidas
        # pre-mapper para permitir que o consumidor bloqueie o cursor antes do
        # gap. Sem `versao`, o consumidor deve tratar como pendencia da pagina.
        mapper = _mapear_nfse_focus if tipo == "nfse" else _mapear_nfe_focus
        documentos: list[dict] = []
        erros: list[dict] = []
        max_versao_itens = 0
        # `erros_sem_versao` conta itens que falharam no mapper SEM que a
        # versao FocusNFe pudesse ser extraida do payload bruto. Sem
        # versao, o cursor nao pode ser bloqueado com precisao — a unica
        # opcao segura e' NAO avancar o cursor nesta pagina inteira.
        # Ver bloco de cursor abaixo (gap_sem_versao).
        erros_sem_versao = 0
        for idx, item in enumerate(body):
            # Pre-mapper: extrai `versao_pre` estrita pelo helper
            # canonico (aceita apenas int/str-digit > 0). Serve tambem
            # como fallback quando o mapper levantar excecao.
            versao_pre: int | None = None
            chave_pre: str | None = None
            if isinstance(item, dict):
                versao_pre = _versao_focus_valida(
                    item.get("versao") if item.get("versao") is not None
                    else item.get("versao_nfe")
                )
                chave_pre = _get_str(
                    item, "chave_nfe", "chave", "chNFe", "chave_nfse",
                ) or None

            # Bail-out pre-mapper (rev.3): quando a versao bruta e'
            # invalida (ausente, 0, negativa, bool, decimal, lista,
            # dict, texto etc), NAO chama o mapper — os mappers NF-e/
            # NFS-e normalizam silenciosamente para 0/1 e devolvem doc
            # "valido", mascarando o gap. Aqui bloqueamos antes disso.
            # A unica ressalva: se `versao_pre is None` mas o mapper
            # SUCEDER com uma versao valida (contratos futuros?), o
            # post-mapper permite. Hoje isso nao ocorre.
            if isinstance(item, dict) and versao_pre is None:
                entry: dict[str, Any] = {
                    "ok":         False,
                    "codigo":     "FOCUS_ITEM_VERSAO_INVALIDA",
                    "erro":       "item com versao FocusNFe invalida",
                    "indice":     idx,
                    "provider":   "focusnfe",
                    "sem_versao": True,
                }
                if chave_pre:
                    entry["chave"] = chave_pre
                erros.append(entry)
                erros_sem_versao += 1
                continue

            try:
                doc = mapper(item, trace_id)
            except Exception as exc:
                # Mensagem SANITIZADA — apenas o nome da excecao. O texto
                # arbitrario de exc pode conter payload fiscal (CNPJ,
                # chave, valor) ou pedaco do dict Focus; nunca deve
                # retornar ao consumidor. `indice` e' posicional, seguro.
                entry: dict[str, Any] = {
                    "ok":       False,
                    "codigo":   "FOCUS_ITEM_INVALIDO",
                    "erro":     f"mapper falhou ({type(exc).__name__})",
                    "indice":   idx,
                    "provider": "focusnfe",
                }
                if versao_pre is not None:
                    entry["versao"] = versao_pre
                else:
                    # Marca explicitamente no proprio item de erro para o
                    # consumidor saber que este item nao contribui para
                    # `menor_versao_pendente_ou_erro` — o cursor precisa
                    # ficar em `versao_entrada` para nao ultrapassa-lo.
                    entry["sem_versao"] = True
                    erros_sem_versao += 1
                if chave_pre:
                    entry["chave"] = chave_pre
                erros.append(entry)
                continue

            # Pos-mapper (rev.3, 2026-07-24): valida versao devolvida.
            # Os mappers NF-e/NFS-e normalizam versao ausente/invalida
            # para 0 (por retrocompatibilidade com contratos antigos).
            # Zero e' invalido — item nao pode ser contabilizado, nao
            # pode entrar em `documentos[]`, nao pode ter XML baixado
            # e forca `gap_sem_versao=True` para bloquear o cursor.
            v_ok = _versao_focus_valida(doc.get("versao"))
            if v_ok is None:
                erros.append({
                    "ok":         False,
                    "codigo":     "FOCUS_ITEM_VERSAO_INVALIDA",
                    "erro":       "item com versao FocusNFe invalida",
                    "indice":     idx,
                    "provider":   "focusnfe",
                    "sem_versao": True,
                })
                erros_sem_versao += 1
                continue
            documentos.append(doc)
            if v_ok > max_versao_itens:
                max_versao_itens = v_ok

        # ── XML completo (fluxo NF-e apenas) ────────────────────────────
        # 2026-07-31 R2 — Correção de premissa estrutural (ADR-0049
        # retificado): NFS-e NÃO passa mais pelo loop de recuperação
        # individual de XML. O payload da listagem `/v2/nfsens_recebidas`
        # é a fonte canônica do EspelhoNFSe (contrato próprio); XML é
        # auxiliar e opcional. DANFSe permanece sob demanda pelo
        # endpoint individual (baixar_xml_nfse_por_chave), NUNCA no
        # loop de importação.
        #
        # NF-e (E4a) mantém o contrato original: quando `nfe_completa=True`,
        # busca XML por chave via GET /v2/nfes_recebidas/{chave}.xml.
        # CANCELADA NF-e não baixa XML — E4b.
        xml_baixados = 0
        xml_pendentes = 0
        if tipo == "nfse":
            # NFS-e: nada a fazer no loop de XML. Documentos já vêm com
            # `status_xml="ESPELHO_DISPONIVEL"` e `xml_pending=False`
            # do mapper. Cancelada/substituída também são Espelho
            # canônico (com flag `cancelado`/`substituido` explícita).
            pass
        else:
            for doc in documentos:
                if doc.get("cancelado") == 1:
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

        # ── Cursor seguro (versao) ───────────────────────────────────────
        # X-Max-Version e X-Total-Count sao os headers oficiais FocusNFe.
        # X-Max-Version = maior versao contida na pagina (limite da pagina),
        # NAO autorizacao para confirmar persistencia. O cursor seguro nunca
        # pode ultrapassar a menor versao com pendencia (XML nao baixado)
        # ou com erro de mapper — se ultrapassar, na proxima consulta o
        # FocusNFe nao devolve o item e o documento vira perda logica.
        max_version_hdr = resp.headers.get("X-Max-Version")
        if max_version_hdr:
            try:
                versao_pagina_int = int(str(max_version_hdr).strip())
            except (TypeError, ValueError):
                versao_pagina_int = max_versao_itens or 0
        elif max_versao_itens > 0:
            versao_pagina_int = max_versao_itens
        else:
            try:
                versao_pagina_int = int(versao_entrada)
            except (TypeError, ValueError):
                versao_pagina_int = 0

        try:
            versao_entrada_int = int(versao_entrada)
        except (TypeError, ValueError):
            versao_entrada_int = 0

        # Menor versao pendente ou com erro (bloqueia cursor).
        pendentes_versoes: list[int] = []
        for d in documentos:
            if d.get("xml_pending"):
                try:
                    pendentes_versoes.append(int(d.get("versao") or 0))
                except (TypeError, ValueError):
                    pass
        for e in erros:
            v = e.get("versao")
            if v is None:
                continue
            try:
                pendentes_versoes.append(int(v))
            except (TypeError, ValueError):
                pass
        pendentes_versoes = [v for v in pendentes_versoes if v > 0]
        menor_versao_pendente = min(pendentes_versoes) if pendentes_versoes else None

        # Gap sem versao — se houve erro de mapper sem versao extraivel, a
        # unica opcao segura e' NAO avancar o cursor NESTA pagina. Nao ha
        # como bloquear "antes do item" quando nao sabemos a versao dele;
        # invencionar versao seria criar cursor falso. `gap_sem_versao`
        # trava tudo em `versao_entrada` e forca reconsulta.
        gap_sem_versao = erros_sem_versao > 0

        # Cursor seguro:
        #   1. gap_sem_versao → versao_entrada (bloqueio total);
        #   2. menor pendente/erro com versao → min(versao_pagina, menor-1);
        #   3. sem pendencia → versao_pagina.
        # Nunca regride abaixo de `versao_entrada`; nunca ultrapassa
        # `versao_pagina_int`.
        if gap_sem_versao:
            cursor_seguro_int = versao_entrada_int
        elif menor_versao_pendente is not None:
            cursor_seguro_int = max(
                versao_entrada_int,
                min(versao_pagina_int, menor_versao_pendente - 1),
            )
        else:
            cursor_seguro_int = max(versao_entrada_int, versao_pagina_int)

        cursor_seguro = str(cursor_seguro_int)

        # has_more: pagina cheia (>=100 é o teto oficial FocusNFe) sugere
        # proxima pagina; pendencias/erros/gap_sem_versao forcam reconsulta.
        quantidade_retornada = len(body)
        has_more = (
            quantidade_retornada >= 100
            or xml_pendentes > 0
            or bool(erros)
            or gap_sem_versao
            or (total_count > quantidade_retornada)
        )

        return {
            "ok":              True,
            "provider":        "focusnfe",
            "trace_id":        trace_id,
            "documentos":      documentos,
            "resumos":         [],
            "erros":           erros,
            # Cursores legados apontam para o cursor seguro — consumidores
            # antigos que leem `ultimo_nsu`/`max_nsu` ficam automaticamente
            # protegidos contra ultrapassar pendencias.
            "ultimo_nsu":      cursor_seguro,
            "max_nsu":         cursor_seguro,
            "cursor_tipo":     "versao",
            "nsu_avancou":     cursor_seguro != versao_entrada,
            # Contrato v2 — cursor seguro explicito.
            "versao_entrada":                versao_entrada,
            "versao_pagina":                 str(versao_pagina_int),
            "quantidade_retornada":          quantidade_retornada,
            "has_more":                      has_more,
            "menor_versao_pendente_ou_erro": (
                str(menor_versao_pendente) if menor_versao_pendente is not None else None
            ),
            "cursor_seguro":                 cursor_seguro,
            # `gap_sem_versao=True` quando existe pelo menos um item
            # invalido cuja versao FocusNFe nao pode ser extraida do
            # payload. Nesse caso o cursor NAO pode avancar: nao ha
            # como bloquear "antes do item" sem inventar versao. O
            # consumidor deve tratar como GAP_SEM_VERSAO (nunca
            # BACKLOG_ZERADO nem REJEITADO).
            "gap_sem_versao":                gap_sem_versao,
            "erros_sem_versao":              erros_sem_versao,
            # Compat / telemetria.
            "total_count":     total_count,
            "http_status":     200,
            "xmls_baixados":   xml_baixados,
            "xmls_pendentes":  xml_pendentes,
            "recebidos_da_focus": quantidade_retornada,
            "xmls_recuperados": xml_baixados,
            "documentos_mapeados": len(documentos),
            "resumos_pendentes": xml_pendentes,
            "erros_de_mapeamento": len(erros),
            "cancelados": sum(1 for d in documentos if d.get("cancelado") == 1),
            "substituidos": sum(1 for d in documentos if d.get("substituido") == 1),
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

    # ── DANFSe HTML (NFS-e recebida — FocusNFe) ────────────────────────
    _DANFSE_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB — defesa contra flood
    _DANFSE_ACCEPT = "text/html"

    def baixar_danfse_nfse(self, chave: str,
                             ambiente: str | None = None) -> dict:
        """Baixa DANFSe HTML da NFS-e recebida via FocusNFe.

        Endpoint oficial:
          ``GET {base_url}/v2/nfsens_recebidas/{chave}.html``

        Fluxo (mesmo padrão de ``baixar_danfe``):
          1. GET com Authorization Basic, ``allow_redirects=False``,
             ``Accept: text/html``.
          2. Redirect (301/302/303/307/308) → segundo GET SEM
             Authorization, host validado pela allowlist
             (:func:`_xml_redirect_location_permitida`).
          3. 200 direto → aceita corpo.
          4. Valida ``Content-Type`` = ``text/html`` (MIME inesperado
             falha nominal).
          5. Valida tamanho <= ``_DANFSE_MAX_BYTES``.

        Envelope de sucesso:
          ``{ok, provider, bytes, mime, tamanho}``

        Erros nominais (nunca expõem token/detalhe de driver):
          ``FOCUS_BAD_REQUEST`` — chave vazia.
          ``FOCUS_TOKEN_AUSENTE`` — token não configurado.
          ``DANFSE_REQUEST_ERROR`` — falha de conexão.
          ``DANFSE_TIMEOUT`` — timeout.
          ``DANFSE_NAO_ENCONTRADA`` — 404.
          ``DANFSE_NAO_AUTORIZADA`` — 401.
          ``DANFSE_NO_LOCATION`` — redirect sem Location.
          ``DANFSE_HOST_PROIBIDO`` — redirect para host fora da allowlist.
          ``DANFSE_DOWNLOAD_ERROR`` — falha no GET pré-assinado.
          ``DANFSE_HTTP_ERROR`` — status inesperado no storage.
          ``DANFSE_UNEXPECTED_HTTP`` — status inesperado na origem.
          ``DANFSE_MIME_INESPERADO`` — Content-Type != text/html.
          ``DANFSE_VAZIO`` — corpo vazio.
          ``DANFSE_MUITO_GRANDE`` — corpo excede o limite defensivo.
        """
        chave = str(chave or "").strip()
        if not chave:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "chave obrigatoria.",
            }
        # Escape defensivo: aceita apenas caracteres seguros no path
        # (dígitos, letras, hífen, ponto, underscore). Nunca interpola
        # arbitrário na URL. Chaves NFS-e Nacional têm formato
        # ``NFSe<44dígitos>`` ou variantes municipais alfanuméricas.
        import re as _re
        if not _re.match(r"^[A-Za-z0-9._-]{1,80}$", chave):
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "chave em formato invalido.",
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
        url = f"{base_url}/v2/nfsens_recebidas/{chave}.html"
        headers_auth = {
            **_basic_auth_header(token),
            "Accept": self._DANFSE_ACCEPT,
        }
        try:
            resp = requests.get(
                url, headers=headers_auth, allow_redirects=False,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "DANFSE_TIMEOUT",
                "erro":     f"Timeout ({self._timeout}s) ao consultar FocusNFe.",
            }
        except requests.exceptions.RequestException as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "DANFSE_REQUEST_ERROR",
                "erro":     f"Erro HTTP: {type(exc).__name__}.",
            }

        body: bytes | None = None
        mime: str = ""

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "").strip()
            if not location:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "DANFSE_NO_LOCATION",
                    "erro":     f"Redirect {resp.status_code} sem Location.",
                }
            if not _xml_redirect_location_permitida(location, url):
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "DANFSE_HOST_PROIBIDO",
                    "erro":     "Redirect para host fora da allowlist.",
                }
            try:
                # CRÍTICO: segundo GET nunca envia Authorization.
                resp2 = requests.get(
                    location,
                    headers={"Accept": self._DANFSE_ACCEPT},
                    allow_redirects=False, timeout=self._timeout,
                )
            except requests.exceptions.Timeout:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "DANFSE_TIMEOUT",
                    "erro":     f"Timeout ({self._timeout}s) no download pré-assinado.",
                }
            except requests.exceptions.RequestException as exc:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "DANFSE_DOWNLOAD_ERROR",
                    "erro":     f"Erro no download pré-assinado: {type(exc).__name__}.",
                }
            if resp2.status_code != 200:
                return {
                    "ok":          False,
                    "provider":    "focusnfe",
                    "codigo":      "DANFSE_HTTP_ERROR",
                    "erro":        f"Storage devolveu {resp2.status_code}.",
                    "http_status": resp2.status_code,
                }
            body = resp2.content
            mime = resp2.headers.get("Content-Type", "").split(";")[0].strip().lower()
        elif resp.status_code == 200:
            body = resp.content
            mime = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        elif resp.status_code == 401:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "DANFSE_NAO_AUTORIZADA",
                "erro":        "Credencial rejeitada pela FocusNFe.",
                "http_status": 401,
            }
        elif resp.status_code == 404:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "DANFSE_NAO_ENCONTRADA",
                "erro":        "Documento não encontrado na FocusNFe.",
                "http_status": 404,
            }
        else:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "DANFSE_UNEXPECTED_HTTP",
                "erro":        f"Status HTTP inesperado ({resp.status_code}).",
                "http_status": resp.status_code,
            }

        if body is None or len(body) == 0:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "DANFSE_VAZIO",
                "erro":     "Resposta HTML vazia.",
            }
        if len(body) > self._DANFSE_MAX_BYTES:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "DANFSE_MUITO_GRANDE",
                "erro":     f"Resposta HTML excede o limite ({self._DANFSE_MAX_BYTES} bytes).",
            }
        # Aceita ``text/html`` (com ou sem charset). Origem pode omitir
        # Content-Type em storages pré-assinados — nesse caso aplica o
        # padrão text/html conforme header ``Accept``.
        if mime and mime != "text/html":
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "DANFSE_MIME_INESPERADO",
                "erro":     f"Content-Type inesperado: {mime!r}.",
            }

        return {
            "ok":       True,
            "provider": "focusnfe",
            "bytes":    body,
            "mime":     "text/html",
            "tamanho":  len(body),
        }

    # ── Helper interno comum: GET XML upstream em BYTES (Fase G0.2a) ───
    def _http_get_xml_bytes_upstream(
        self,
        url: str,
        *,
        permitir_redirect: bool = False,
        timeout: int | None = None,
    ) -> dict:
        """GET de XML recebido, preservando bytes upstream sem decode.

        Usado por baixar_xml_completo (NF-e), baixar_xml_nfse (via url_xml),
        baixar_xml_nfse_por_chave (NFSe), baixar_xml_cte_por_chave (CT-e)
        e baixar_xml_bytes_por_chave (rota G0.2a).

        Contrato:
        - Basic auth via self._require_token(); nunca Bearer.
        - Accept: application/xml.
        - resp.content (bytes); SHA-256 direto sobre esses bytes.
        - allow_redirects=False. Se permitir_redirect=True e chegar 3xx:
          valida Location e faz segundo GET SEM Authorization
          (URL pre-assinada carrega assinatura no query string).
        - Trata timeout/conexao/401/403/404/429/5xx separadamente.
        - Token, Authorization, XML, URL pre-assinada nunca em erro/log.

        Retorno OK:  {ok, provider, xml_bytes, xml_hash_sha256, tamanho, http_status}
        Retorno erro: envelope tipado sem conteudo/token.
        """
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_TOKEN_AUSENTE",
                "erro":     str(exc),
            }
        headers_auth = {**_basic_auth_header(token),
                        "Accept": "application/xml"}
        tmo = int(timeout) if timeout else (
            min(self._timeout, 5) if self._timeout else 5
        )
        try:
            resp = requests.get(url, headers=headers_auth,
                                allow_redirects=False, timeout=tmo)
        except requests.exceptions.Timeout:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_TIMEOUT",
                "erro":     f"Timeout ({tmo}s) baixando XML upstream.",
            }
        except requests.exceptions.RequestException as exc:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_ERRO",
                "erro":     f"Erro HTTP: {type(exc).__name__}.",
            }
        finally:
            try:
                del token
            except NameError:
                pass
            try:
                del headers_auth
            except NameError:
                pass

        status = resp.status_code

        # Redirect (opt-in) — segundo GET SEM Authorization
        if permitir_redirect and status in (301, 302, 303, 307, 308):
            location = (resp.headers.get("Location") or "").strip()
            if not location or not _xml_redirect_location_permitida(location, url):
                return {
                    "ok":          False,
                    "provider":    "focusnfe",
                    "codigo":      "FOCUS_XML_REDIRECT_NAO_PERMITIDO",
                    "erro":        "Redirect upstream ausente ou nao permitido.",
                    "http_status": status,
                }
            try:
                resp2 = requests.get(
                    location,
                    headers={"Accept": "application/xml"},
                    allow_redirects=False, timeout=tmo,
                )
            except requests.exceptions.Timeout:
                return {
                    "ok":       False,
                    "provider": "focusnfe",
                    "codigo":   "FOCUS_XML_TIMEOUT",
                    "erro":     f"Timeout ({tmo}s) baixando XML pre-assinado.",
                }
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
            body_bytes = resp2.content or b""
            upstream_status = resp2.status_code
        elif status == 200:
            body_bytes = resp.content or b""
            upstream_status = 200
        elif status in (401, 403):
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_AUTH_ERROR",
                "erro":        f"Autenticacao rejeitada pelo upstream ({status}).",
                "http_status": status,
            }
        elif status == 404:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_NAO_ENCONTRADO",
                "erro":        "XML nao encontrado no FocusNFe (404).",
                "http_status": 404,
            }
        elif status == 429:
            env = {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_RATE_LIMIT",
                "erro":        "Rate limit atingido no FocusNFe (429).",
                "http_status": 429,
            }
            # G0.2a-R2: Retry-After normalizado apenas quando inteiro positivo.
            # Nao interpreta data HTTP nesta fase. Header cru nunca repassado.
            ra = _parse_retry_after_int(resp.headers.get("Retry-After"))
            if ra is not None:
                env["retry_after_seg"] = ra
            return env
        elif status >= 500:
            env = {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_UPSTREAM_UNAVAILABLE",
                "erro":        f"Upstream indisponivel ({status}).",
                "http_status": status,
            }
            # 503 pode carregar Retry-After tambem.
            if status == 503:
                ra = _parse_retry_after_int(resp.headers.get("Retry-After"))
                if ra is not None:
                    env["retry_after_seg"] = ra
            return env
        else:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_HTTP_ERROR",
                "erro":        f"Status HTTP inesperado ({status}).",
                "http_status": status,
            }

        if not body_bytes:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_VAZIO",
                "erro":     "Focus devolveu corpo vazio para XML.",
            }
        # 2026-07-31 — corpo não XML nunca é persistido como XML.
        # Content-Type explicitamente HTML/JSON/texto rejeita nominal;
        # o primeiro byte também vira sanity check contra HTML sem
        # header (ex.: proxy respondendo <html>).
        upstream_resp = resp2 if permitir_redirect and status in (
            301, 302, 303, 307, 308) else resp
        ct_raw = ""
        try:
            ct_raw = str(upstream_resp.headers.get("Content-Type") or "").lower()
        except Exception:  # pragma: no cover — defensivo
            ct_raw = ""
        ct = ct_raw.split(";")[0].strip()
        # Aceita: application/xml, text/xml, application/*+xml, ou
        # ausência (algumas storages pré-assinadas omitem).
        aceito = (
            not ct
            or ct == "application/xml"
            or ct == "text/xml"
            or ct.startswith("application/")
            and ct.endswith("+xml")
        )
        if not aceito:
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_CONTENT_TYPE_INVALIDO",
                "erro":        "Corpo do endpoint XML não é XML.",
                "http_status": upstream_status,
            }
        # Sanity check leve — se começa com <html/<HTML mesmo sem
        # Content-Type, rejeita (proxy erro típico).
        prefix = body_bytes[:16].lstrip().lower()
        if prefix.startswith(b"<html") or prefix.startswith(b"<!doctype html"):
            return {
                "ok":          False,
                "provider":    "focusnfe",
                "codigo":      "FOCUS_XML_CONTENT_TYPE_INVALIDO",
                "erro":        "Corpo do endpoint XML aparenta ser HTML.",
                "http_status": upstream_status,
            }
        sha = hashlib.sha256(body_bytes).hexdigest()
        return {
            "ok":              True,
            "provider":        "focusnfe",
            "xml_bytes":       body_bytes,
            "xml_hash_sha256": sha,
            "tamanho":         len(body_bytes),
            "http_status":     upstream_status,
        }

    # ── baixar_xml_bytes_por_chave — RECUPERACAO INDIVIDUAL (G0.2a) ──
    def baixar_xml_bytes_por_chave(
        self,
        doc_type: str,
        identificador: str,
        ambiente: str | None = None,
    ) -> dict:
        """Recuperacao bruta de XML upstream por chave/identificador.

        Contrato G0.2a — bytes puros (nunca decode/reencode).

        - doc_type='nfe'  -> GET {base}/v2/nfes_recebidas/{chave}.xml
        - doc_type='cte'  -> GET {base}/v2/ctes_recebidas/{chave}.xml
        - doc_type='nfse' -> GET {base}/v2/nfsens_recebidas/{ident_esc}.xml

        A validacao (chave 44 dig / DV / escape de segmento NFSe) e
        responsabilidade da camada superior (rota `/fiscal/xml/por-chave`).
        Este metodo nao interpreta conteudo fiscal.

        Retorno OK:  {ok, provider, xml_bytes, xml_hash_sha256, tamanho, http_status}
        Retorno erro: envelope tipado sem conteudo/token.
        """
        dt = str(doc_type or "").strip().lower()
        ident = str(identificador or "").strip()
        if not ident:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "identificador obrigatorio.",
            }
        if dt == "nfe":
            path = f"/v2/nfes_recebidas/{ident}.xml"
        elif dt == "cte":
            path = f"/v2/ctes_recebidas/{ident}.xml"
        elif dt == "nfse":
            # Escape ja aplicado pelo caller (rota) para NFSe.
            path = f"/v2/nfsens_recebidas/{ident}.xml"
        else:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_XML_TIPO_NAO_SUPORTADO",
                "erro":     f"doc_type nao suportado: {dt!r}",
            }
        base_url = self._base_url_for(ambiente)
        url = f"{base_url}{path}"
        return self._http_get_xml_bytes_upstream(url, permitir_redirect=False)

    # ── baixar_xml_completo — NF-e por chave (Fase E4a, G0.2a refactor) ──
    def baixar_xml_completo(self, chave: str, ambiente: str | None = None) -> dict:
        """Baixa XML nfeProc da FocusNFe pelo endpoint separado.

        Endpoint: GET {base_url}/v2/nfes_recebidas/{chave}.xml
        Refatorado em G0.2a: delega ao helper interno; contrato do lote
        preservado — `xml_bruto` (str) mantido para consumidores existentes.

        Retorno OK:  {ok, provider, xml_bruto, xml_hash_sha256, tamanho, ...}
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
        res = self.baixar_xml_bytes_por_chave("nfe", chave, ambiente)
        if not res.get("ok"):
            return res
        # Wrapper legado: expoe xml_bruto (str) para gov_fetch e testes E4a.
        # SHA-256 continua sobre bytes upstream (correcao G0.2a).
        body_bytes: bytes = res["xml_bytes"]
        return {
            "ok":              True,
            "provider":        "focusnfe",
            "xml_bruto":       body_bytes.decode("utf-8", errors="replace"),
            "xml_bytes":       body_bytes,
            "xml_hash_sha256": res["xml_hash_sha256"],
            "tamanho":         res["tamanho"],
        }

    # ── baixar_xml_nfse — nfse XML via url_xml (Fase E4c, G0.2a refactor) ──
    def baixar_xml_nfse(self, url_xml: str) -> dict:
        """Baixa XML NFSe Nacional a partir da `url_xml` fornecida pelo item
        da listagem `/v2/nfsens_recebidas`.

        Refatorado em G0.2a: delega ao helper com permitir_redirect=True.
        Contrato do lote preservado.
        """
        url = str(url_xml or "").strip()
        if not url:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "url_xml obrigatoria.",
            }
        res = self._http_get_xml_bytes_upstream(url, permitir_redirect=True)
        if not res.get("ok"):
            return res
        body_bytes: bytes = res["xml_bytes"]
        return {
            "ok":              True,
            "provider":        "focusnfe",
            "xml_bruto":       body_bytes.decode("utf-8", errors="replace"),
            "xml_bytes":       body_bytes,
            "xml_hash_sha256": res["xml_hash_sha256"],
            "tamanho":         res["tamanho"],
        }

    # ── baixar_xml_nfse_por_chave — NFSe por chave (E4c, G0.2a refactor) ─
    def baixar_xml_nfse_por_chave(self, chave: str,
                                   ambiente: str | None = None) -> dict:
        """Baixa XML NFSe Nacional pelo endpoint oficial FocusNFe por chave.

        Endpoint: GET {base_url}/v2/nfsens_recebidas/{chave}.xml
        Refatorado em G0.2a: delega ao helper com permitir_redirect=True
        (endpoint pode redirecionar para storage pre-assinado).
        """
        chave_s = str(chave or "").strip()
        if not chave_s:
            return {
                "ok":       False,
                "provider": "focusnfe",
                "codigo":   "FOCUS_BAD_REQUEST",
                "erro":     "chave obrigatoria.",
            }
        base_url = self._base_url_for(ambiente)
        url = f"{base_url}/v2/nfsens_recebidas/{chave_s}.xml"
        res = self._http_get_xml_bytes_upstream(url, permitir_redirect=True)
        if not res.get("ok"):
            return res
        body_bytes: bytes = res["xml_bytes"]
        return {
            "ok":              True,
            "provider":        "focusnfe",
            "xml_bruto":       body_bytes.decode("utf-8", errors="replace"),
            "xml_bytes":       body_bytes,
            "xml_hash_sha256": res["xml_hash_sha256"],
            "tamanho":         res["tamanho"],
        }

    def baixar_json_nfse_por_chave(self, chave: str,
                                    ambiente: str | None = None,
                                    versao_origem=None) -> dict:
        """Consulta a NFS-e Nacional individual em JSON.

        Endpoint oficial FocusNFe:
        ``GET /v2/nfsens_recebidas/{chave}.json``.
        A chave deve vir nominalmente de ``chave_nfse``; numero_dfse e
        id_dps não são aceitos pelo caller como substitutos.
        """
        chave_s = str(chave or "").strip()
        if not re.match(r"^[A-Za-z0-9._-]{1,200}$", chave_s):
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_BAD_REQUEST",
                    "erro": "chave NFS-e invalida."}
        try:
            token = self._require_token()
        except RuntimeError as exc:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_TOKEN_AUSENTE", "erro": str(exc)}
        url = (f"{self._base_url_for(ambiente)}"
               f"/v2/nfsens_recebidas/{chave_s}.json")
        try:
            resp = requests.get(
                url,
                headers={**_basic_auth_header(token),
                         "Accept": "application/json"},
                allow_redirects=False,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_TIMEOUT",
                    "erro": "Timeout consultando JSON NFS-e."}
        except requests.exceptions.RequestException as exc:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_REQUEST_ERROR",
                    "erro": f"Erro HTTP: {type(exc).__name__}."}
        finally:
            try:
                del token
            except NameError:
                pass

        if resp.status_code == 404:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_NAO_ENCONTRADO",
                    "erro": "JSON individual NFS-e nao encontrado.",
                    "http_status": 404}
        if resp.status_code in (401, 403):
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_AUTH_ERROR",
                    "erro": "Credencial rejeitada pela FocusNFe.",
                    "http_status": resp.status_code}
        if resp.status_code == 429:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_RATE_LIMIT",
                    "erro": "Rate limit no JSON individual NFS-e.",
                    "http_status": 429}
        if resp.status_code != 200:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_HTTP_ERROR",
                    "erro": f"Upstream devolveu {resp.status_code}.",
                    "http_status": resp.status_code}
        mime = (resp.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if mime != "application/json":
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_MIME_INVALIDO",
                    "erro": "Content-Type inesperado no JSON NFS-e."}
        if len(resp.content or b"") > 2 * 1024 * 1024:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_MUITO_GRANDE",
                    "erro": "JSON individual excede 2 MiB."}
        try:
            payload = resp.json()
        except ValueError:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_INVALIDO",
                    "erro": "Resposta individual nao e JSON valido."}
        if not isinstance(payload, dict):
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_SCHEMA_INVALIDO",
                    "erro": "JSON individual deve ser objeto."}
        if payload.get("versao") is None and versao_origem is not None:
            payload = dict(payload)
            payload["versao"] = versao_origem
        try:
            documento = _mapear_nfse_focus(payload, "nfse-json-individual")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "provider": "focusnfe",
                    "codigo": "FOCUS_NFSE_JSON_SCHEMA_INVALIDO",
                    "erro": str(exc)[:300]}
        return {"ok": True, "provider": "focusnfe",
                "documento": documento, "http_status": 200}

    # ── baixar_xml_cte_por_chave — CT-e por chave (Fase G0.2a) ─────────
    def baixar_xml_cte_por_chave(self, chave: str,
                                  ambiente: str | None = None) -> dict:
        """Baixa XML CT-e pelo endpoint oficial FocusNFe por chave.

        Endpoint: GET {base_url}/v2/ctes_recebidas/{chave}.xml
        Contrato G0.2a — bytes puros, sem redirect padrao.
        """
        return self.baixar_xml_bytes_por_chave("cte", chave, ambiente)

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
