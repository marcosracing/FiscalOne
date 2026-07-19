"""
FiscalOne — Gateway fiscal técnico do ecossistema RLogix. Fase 1 DFe recebidos.
ADR-0035: gateway puro sem persistência própria.

Capacidade atual:
  - Parseia XML/PDF/ZIP recebidos: NF-e, CT-e, MDF-e, NFS-e nacional/ABRASF
    e NFS-e PDF Prefeitura de São Paulo (POST /fiscal/documents/import).
  - NÃO assina, NÃO transmite, NÃO consulta SEFAZ — providers são stubs.
  - Busca ativa SEFAZ/DFe: stub (Fase 2 pendente).
  - Emissão de CT-e, MDF-e, cancelamento, eventos fiscais: honest-stub, 501.
  - Produção fiscal: bloqueada por padrão via flags duplas.
  - Sem banco, sem XML raw, sem cooldown, sem certificado em repouso.
  - Toda persistência é responsabilidade da vertical (MapOne, CtrlOne).
  - trace_id propaga em toda operação — não armazena.
Porta: 5002
"""
import os
import logging
import uuid
import time
import zipfile
import io
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from xml_parser import parse_xml, parse_pdf, parse_document

app = Flask(__name__)

# Logger dedicado para avisos de infraestrutura (nao mistura com log JSON
# operacional em stdout via _log_stdout).
logger = logging.getLogger("fiscalone")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

FISCAL_PROVIDER = os.getenv("FISCAL_PROVIDER", "sefaz")
GOV_TLS_INSECURE = os.getenv("GOV_TLS_INSECURE", "").strip() == "1"

# ── Avisos de infraestrutura no boot ─────────────────────────────────────────
if GOV_TLS_INSECURE:
    logger.warning(
        "GOV_TLS_INSECURE=1 ativo — verificacao TLS DESABILITADA. "
        "USO PROIBIDO EM PRODUCAO."
    )
if FISCAL_PROVIDER == "focusnfe":
    logger.warning(
        "FISCAL_PROVIDER=focusnfe — stub. Todas as chamadas retornarao "
        "PROVIDER_NAO_IMPLEMENTADO."
    )


def _classificar_status_lote(processados: int, persistidos: int, erros: int) -> str:
    """
    Regra:
      - 0 processados                        → SEM_DOCUMENTO
      - persistidos > 0 e erros == 0         → SUCESSO_TOTAL
      - persistidos > 0 e erros > 0          → SUCESSO_PARCIAL
      - persistidos == 0 e processados > 0   → FALHA_TOTAL
    """
    if processados == 0:
        return "SEM_DOCUMENTO"
    if persistidos > 0 and erros == 0:
        return "SUCESSO_TOTAL"
    if persistidos > 0 and erros > 0:
        return "SUCESSO_PARCIAL"
    return "FALHA_TOTAL"


# ── Classificador de acao para /fiscal/gov/fetch ─────────────────────────────
# Tabela de cStat SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse:
#   137 → Nenhum documento localizado (SEM_DOCUMENTO, avanca NSU)
#   138 → Documento(s) localizado(s)  (DOCUMENTOS,    avanca NSU)
#   589 → NSU consultado > maxNSU     (REJEITADO,     nao avanca)
#   656 → Consumo Indevido            (REJEITADO,     nao avanca)
#   demais rejeicoes                  (REJEITADO,     nao avanca)
#
# Para NFS-e Nacional (ADN, sem cStat) usamos o campo `status`:
#   DOCUMENTOS_LOCALIZADOS → DOCUMENTOS
#   SEM_DOCUMENTO          → SEM_DOCUMENTO
#   AUTH/HTTP/XML erro     → REJEITADO ou ERRO
_CSTAT_OK_SEM_DOC     = {"137"}
_CSTAT_OK_COM_DOC     = {"138"}
_CSTAT_REJEICAO_SEFAZ = {"656", "589", "108", "109"}

_CODIGO_TECNICO_ERRO = {
    "SEFAZ_INDISPONIVEL", "SEFAZ_HTTP_ERRO", "SEFAZ_XML_INVALIDO", "TLS_ERRO",
    "NFSE_ADN_HTTP_ERRO", "NFSE_ADN_TIMEOUT", "NFSE_ADN_XML_INVALIDO",
    "NFSE_ADN_AUTH_ERRO", "ERRO_INTERNO",
    "CERT_NAO_CONFIGURADO", "CERT_BASE64_INVALIDO", "CERT_ABERTURA_FALHOU",
    "CERT_INVALIDO", "CERT_ENV_INVALIDO", "CERT_CNPJ_DIVERGENTE",
    "CERT_SEM_CNPJ", "CERT_FONTE_NAO_SUPORTADA",
    "PAYLOAD_INVALIDO", "CNPJ_INVALIDO", "TIPO_NAO_SUPORTADO",
    "PROVIDER_NAO_IMPLEMENTADO", "CNPJ_INVALIDO",
}


def _classificar_acao_gov_fetch(result: dict, docs_count: int) -> str:
    """
    Retorna: 'DOCUMENTOS' | 'SEM_DOCUMENTO' | 'REJEITADO' | 'ERRO'.

    Fonte primaria: existencia de documentos no lote.
    Fonte secundaria: cstat (SEFAZ) ou status (ADN).
    Fonte terciaria: codigo tecnico (erro do FiscalOne ou upstream).
    """
    codigo = (result or {}).get("codigo")
    cstat  = str((result or {}).get("cstat") or "").strip()
    status = ((result or {}).get("status") or "").upper()

    # Erro tecnico (FiscalOne ou upstream) → ERRO
    if codigo in _CODIGO_TECNICO_ERRO:
        return "ERRO"

    # Documentos encontrados (via contagem OU via cstat 138 / status ADN) → DOCUMENTOS
    if docs_count > 0:
        return "DOCUMENTOS"
    if cstat in _CSTAT_OK_COM_DOC:
        return "DOCUMENTOS"
    if status == "DOCUMENTOS_LOCALIZADOS":
        return "DOCUMENTOS"

    # Rejeicao SEFAZ (656, 589, etc) → REJEITADO
    if cstat in _CSTAT_REJEICAO_SEFAZ:
        return "REJEITADO"
    # cstat presente e nao esta na lista de sucesso → REJEITADO
    if cstat and cstat not in _CSTAT_OK_SEM_DOC and cstat not in _CSTAT_OK_COM_DOC:
        return "REJEITADO"

    # Sucesso sem documento (cstat 137, status SEM_DOCUMENTO, ou nada retornado)
    return "SEM_DOCUMENTO"


def _nsu_avancou(acao: str) -> bool:
    """NSU avanca APENAS em DOCUMENTOS e SEM_DOCUMENTO. MapOne usa isso
    como sinal para PUT no CtrlOne."""
    return acao in ("DOCUMENTOS", "SEM_DOCUMENTO")
_PROD_AMBIENTES = {"prod", "producao", "produção", "production"}
_TRUE_VALUES = {"1", "true", "yes", "sim", "on"}

def _ambiente():
    return os.getenv("FISCALONE_AMBIENTE", "homologacao").strip().lower()

def _flag(name):
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES

_REQUIRED_PRODUCAO_FLAGS = (
    "FISCALONE_ENABLE_PRODUCAO",
    "MAPONE_FISCAL_PRODUCAO_READY",
    "FISCALONE_DFE_RECEBIDO_ONLY",
)

def _producao_bloqueada():
    """
    Producao liberada APENAS para DFe recebido, e apenas com as tres flags.
    FISCALONE_DFE_RECEBIDO_ONLY torna a autorizacao explicita: nao ha uso
    generalista de producao — so consulta/recepcao DFe.
    """
    if _ambiente() not in _PROD_AMBIENTES:
        return False
    return not all(_flag(f) for f in _REQUIRED_PRODUCAO_FLAGS)

def _bloqueio_producao(operacao, trace_id, source_system="desconhecido"):
    faltantes = [f for f in _REQUIRED_PRODUCAO_FLAGS if not _flag(f)]
    _log_stdout(
        operacao,
        "bloqueado_producao",
        trace_id,
        source_system=source_system,
        erro_msg=f"producao_bloqueada · flags faltantes: {','.join(faltantes) or 'nenhuma'}",
    )
    return jsonify({
        "ok": False,
        "trace_id": trace_id,
        "codigo": "FISCALONE_PRODUCAO_BLOQUEADA",
        "erro": (
            "Operacao em producao bloqueada. Producao e liberada APENAS para "
            "DFe recebido, e apenas com as tres flags de autorizacao explicita."
        ),
        "ambiente": _ambiente(),
        "required_flags": [f"{f}=1" for f in _REQUIRED_PRODUCAO_FLAGS],
        "flags_faltantes": faltantes,
        "escopo_liberado": "dfe_recebido_apenas",
    }), 403

def bloquear_emissao(operacao, trace_id, source_system="desconhecido"):
    """
    Guard central absoluto: emissao fiscal permanece bloqueada mesmo com
    todas as flags de producao liberadas. Ignora _producao_bloqueada().
    """
    _log_stdout(
        operacao,
        "bloqueado_emissao",
        trace_id,
        source_system=source_system,
        erro_msg="emissao fiscal permanentemente bloqueada nesta fase",
    )
    return jsonify({
        "ok": False,
        "trace_id": trace_id,
        "codigo": "EMISSAO_BLOQUEADA",
        "erro": "FiscalOne liberado apenas para DFe recebido; emissao fiscal permanece bloqueada.",
        "escopo_liberado": "dfe_recebido_apenas",
    }), 403

def _provider_response(operacao, payload, status_padrao=501):
    status = 200 if payload.get("ok") else status_padrao
    return jsonify(payload), status

# Allowlist de providers aceitos por requisicao. Blindagem contra instanciacao
# de classes arbitrarias vindas do payload.
_PROVIDERS_SUPORTADOS = frozenset({"sefaz", "focusnfe"})


def get_provider(provider_name: str | None = None, token: str | None = None):
    """Resolve provider por requisicao (Fase D) com fallback ao env FISCAL_PROVIDER.

    Args:
        provider_name: `"sefaz"` | `"focusnfe"` | None. Se None/vazio, cai no
            fallback historico via env `FISCAL_PROVIDER`.
        token: opcional; SO usado para `focusnfe`. Precedencia: injetado > env.

    Raises:
        ValueError: se `provider_name` fornecido nao estiver em `_PROVIDERS_SUPORTADOS`.
            O handler HTTP deve validar antes de chamar (para retornar 400 controlado).

    Nota: `token` para SEFAZ e ignorado — SefazProvider usa certificado A1
    resolvido de outra forma (cert_provider.resolve_cert do payload/env).
    """
    resolved = (provider_name or "").strip().lower() or FISCAL_PROVIDER
    if resolved == "focusnfe":
        from providers.focusnfe_provider import FocusNFeProvider
        return FocusNFeProvider(token=token)
    if resolved == "sefaz":
        from providers.sefaz_provider import SefazProvider
        return SefazProvider()
    # Provider desconhecido: se veio do env, mantem comportamento legado (retorna
    # SEFAZ como fallback silencioso — igual antes). Se veio do parametro,
    # levanta para o handler HTTP tratar como PROVIDER_INVALIDO.
    if provider_name:
        raise ValueError(f"provider nao suportado: {provider_name!r}")
    from providers.sefaz_provider import SefazProvider
    return SefazProvider()

def _trace(req):
    """Extrai ou gera trace_id. FiscalOne gera fo- se ausente."""
    tid = req.headers.get("X-Trace-Id", "").strip()
    return tid if tid else f"fo-{uuid.uuid4().hex}"

def _log_stdout(operacao, resultado, trace_id, **kwargs):
    """
    Log técnico estruturado em stdout — sem banco próprio (ADR-0035).
    A vertical persiste o log consultável em op_fiscal_log.
    """
    import json
    entry = {
        "ts":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service":     "fiscalone",
        "operacao":    operacao,
        "resultado":   resultado,
        "trace_id":    trace_id,
        "source":      kwargs.get("source_system", "desconhecido"),
        "cnpj":        kwargs.get("company_cnpj"),
        "doc_type":    kwargs.get("doc_type"),
        "chave":       kwargs.get("chave_doc"),
        # Campos novos para gov_fetch (MapOne consome via journal/stdout)
        "cstat":            kwargs.get("cstat"),
        "acao":             kwargs.get("acao"),
        "ultimo_nsu_antes": kwargs.get("ultimo_nsu_antes"),
        "ultimo_nsu":       kwargs.get("ultimo_nsu"),
        "max_nsu":          kwargs.get("max_nsu"),
        "nsu_avancou":      kwargs.get("nsu_avancou"),
        "duracao_ms":  kwargs.get("duracao_ms"),
        "erro":        kwargs.get("erro_msg"),
    }
    # Filtro: mantem False (nsu_avancou pode ser false — precisa aparecer),
    # remove apenas None.
    entry = {k: v for k, v in entry.items() if v is not None}
    try:
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    except (BrokenPipeError, OSError):
        # O stdout do processo pode estar fechado quando o FiscalOne roda em
        # terminal/daemon transitório. Log quebrado nunca pode derrubar API.
        pass


@app.errorhandler(Exception)
def _json_error_handler(exc):
    """Garante JSON em exceções internas das rotas fiscais."""
    if isinstance(exc, HTTPException):
        return exc
    trace_id = "fo-erro"
    try:
        trace_id = _trace(request)
        _log_stdout(
            "erro_http",
            "erro",
            trace_id,
            source_system=request.headers.get("X-Source-System", "desconhecido"),
            erro_msg=f"{type(exc).__name__}: excecao nao tratada",
        )
    except Exception:
        pass
    return jsonify({
        "ok": False,
        "trace_id": trace_id,
        "codigo": "ERRO_INTERNO",
        "erro": "Erro interno no FiscalOne — verifique logs do serviço",
    }), 500

# ── Health ─────────────────────────────────────────────────────────────────────

@app.route("/fiscal/health")
def health():
    return jsonify({
        "ok":                  True,
        "provider":            FISCAL_PROVIDER,
        "ambiente":            _ambiente(),
        "producao_bloqueada":  _producao_bloqueada(),
        "fase":                "fase_1_dfe_recebidos",
        "escopo_liberado":     "dfe_recebido_apenas",
        "flags_producao": {
            f: _flag(f) for f in _REQUIRED_PRODUCAO_FLAGS
        },
        "flags_producao_faltantes": [
            f for f in _REQUIRED_PRODUCAO_FLAGS if not _flag(f)
        ],
        "sefaz_ativo":         True,
        "tls_insecure":        GOV_TLS_INSECURE,
        "tls_warning":         (
            "GOV_TLS_INSECURE ativo — uso proibido em producao."
            if GOV_TLS_INSECURE else None
        ),
        "emissao_ativa":       False,
        "emissao_bloqueada_por_design": True,
        "persistencia_propria": False,
        "capacidade": {
            "parse_xml_zip":       True,
            "parse_pdf":           True,
            "parse_nfse_xml":      True,
            "parse_nfse_pdf":      True,
            "gov_fetch_dfe":       True,
            "gov_fetch_nfse":      True,
            "nfse_adn_inicio_operacional": "2026-07-01",
            "emitir_nfe":          False,
            "emitir_cte":          False,
            "emitir_mdfe":         False,
            "cancelar":            False,
            "inutilizar":          False,
            "cce":                 False,
            "encerrar_mdfe":       False,
            "condutor_mdfe":       False,
            "certificado_em_transito": True,
        },
        "version":             "0.5.0",
        "adr":                 "ADR-0035",
    })

# ── POST /fiscal/documents/import ─────────────────────────────────────────────

@app.route("/fiscal/documents/import", methods=["POST"])
def import_documents():
    """
    Recebe XML, PDF ou ZIP com XML/PDF, parseia e retorna JSON normalizado.
    Não persiste nada — a vertical persiste no próprio banco.
    """
    trace_id      = _trace(request)
    import_origin = request.args.get("origin", "fiscalone_upload")
    source_system = request.headers.get("X-Source-System", "desconhecido")
    t0            = time.monotonic()

    files = request.files.getlist("files") or request.files.getlist("arquivo")
    if not files:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PARSE_ERROR",
            "erro":     "Nenhum arquivo enviado",
        }), 400

    # Expandir ZIPs (XML e PDF internos)
    raw = []  # cada item: (filename, data) ou (filename, None, erro)
    for f in files:
        fname = f.filename or ""
        data  = f.read()
        low   = fname.lower()
        if low.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for name in z.namelist():
                        nlow = name.lower()
                        if nlow.endswith(".xml") or nlow.endswith(".pdf"):
                            raw.append((f"{fname}/{name}", z.read(name)))
            except Exception as e:
                raw.append((fname, None, str(e)))
        elif low.endswith(".xml") or low.endswith(".pdf"):
            raw.append((fname, data))
        else:
            raw.append((fname, None, "Extensão não suportada (aceita .xml, .pdf, .zip)"))

    if not raw:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PARSE_ERROR",
            "erro":     "Nenhum XML/PDF encontrado nos arquivos enviados",
        }), 400

    results = []
    for item in raw:
        fname = item[0]
        data  = item[1] if len(item) >= 2 else None

        if data is None:
            err = item[2] if len(item) >= 3 else "erro desconhecido"
            results.append({
                "ok": False, "file": fname, "trace_id": trace_id,
                "codigo": "PARSE_ERROR", "erro": err,
            })
            continue

        parsed = parse_document(
            data, fname,
            import_origin=import_origin,
            trace_id=trace_id,
        )
        results.append(parsed)

    duracao = int((time.monotonic() - t0) * 1000)
    persistidos = sum(1 for r in results if r.get("ok"))
    resumos_n   = sum(1 for r in results if r.get("status_xml") == "RESUMO")
    eventos_n   = sum(1 for r in results if r.get("status_xml") == "EVENTO")
    erros_n     = sum(1 for r in results if not r.get("ok"))
    processados = len(results)
    status_lote = _classificar_status_lote(processados, persistidos, erros_n)

    # Log operacional preciso: reflete falha em massa (nao mais mascarado).
    resultado_log = ("ok" if status_lote == "SUCESSO_TOTAL"
                     else "parcial" if status_lote == "SUCESSO_PARCIAL"
                     else "erro")
    _log_stdout("parse_xml", resultado_log, trace_id,
                source_system=source_system, duracao_ms=duracao,
                erro_msg=(None if resultado_log == "ok"
                          else f"status_lote={status_lote} persistidos={persistidos} erros={erros_n}"))

    return jsonify({
        "ok":           True,
        "trace_id":     trace_id,
        "status_lote":  status_lote,
        "recebidos":    processados,
        "processados":  processados,
        "persistidos":  persistidos,
        "duplicados":   0,      # duplicidade e escopo do MapOne
        "resumos":      resumos_n,
        "eventos":      eventos_n,
        "erros":        erros_n,
        "total":        persistidos,        # compat legada
        "results":      results,
    })

# ── POST /fiscal/gov/fetch ─────────────────────────────────────────────────────

@app.route("/fiscal/gov/fetch", methods=["POST"])
def gov_fetch():
    """
    Busca ativa DFe:
      - tipo=nfe|cte → SEFAZ NFeDistDFeInteresse / CTeDistDFeInteresse
      - tipo=nfse    → ADN NFS-e Nacional por NSU (inicio operacional 2026-07-01)

    Gateway puro (ADR-0035): nao persiste NSU, XML ou cooldown.
    Certificado A1 vem por requisicao (cert_pfx_base64+cert_password) ou via env
    (fallback homologacao). Descartado ao final da chamada.

    Payload:
      cnpj_tenant, ambiente ("producao"|"homologacao"),
      tipo ("nfe"|"cte"|"nfse"), ultimo_nsu,
      cert_pfx_base64 (opcional), cert_password (opcional),
      cert_source (opcional: "inline_base64"|"env"),
      data_inicio ("YYYY-MM-DD", metadado NFS-e — eco no retorno; corte
        real por data e responsabilidade do MapOne).

    Sempre devolve JSON — nunca traceback HTML.
    """
    trace_id      = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    t0            = time.monotonic()

    if _producao_bloqueada():
        return _bloqueio_producao("gov_fetch", trace_id, source_system)

    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    if not isinstance(payload, dict) or not payload:
        _log_stdout("gov_fetch", "erro", trace_id,
                    source_system=source_system,
                    erro_msg="payload JSON ausente ou invalido")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PAYLOAD_INVALIDO",
            "erro":     "Payload JSON obrigatorio (cnpj_tenant, ambiente, tipo, ultimo_nsu)",
        }), 400

    cnpj_tenant = (payload.get("cnpj_tenant") or "").strip()
    tipo        = (payload.get("tipo") or "").strip().lower()

    if not cnpj_tenant or len(cnpj_tenant.replace(".", "").replace("/", "").replace("-", "")) < 14:
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "CNPJ_INVALIDO",
            "erro":     "cnpj_tenant obrigatorio (14 digitos)",
        }), 400

    if tipo not in ("nfe", "cte", "nfse"):
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "TIPO_NAO_SUPORTADO",
            "erro":     "tipo obrigatorio: 'nfe', 'cte' ou 'nfse'",
        }), 400

    # ── Fase D — provider por requisicao ─────────────────────────────────────
    # `provider` vem do payload (MapOne ja envia hoje). Se ausente, fallback
    # ao env FISCAL_PROVIDER (comportamento legado). Se presente mas invalido,
    # 400 imediato sem fallback silencioso.
    provider_payload = (payload.get("provider") or "").strip().lower()
    if provider_payload and provider_payload not in _PROVIDERS_SUPORTADOS:
        _log_stdout("gov_fetch", "erro", trace_id, source_system=source_system,
                    erro_msg=f"PROVIDER_INVALIDO: {provider_payload!r}")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PROVIDER_INVALIDO",
            "erro":     "provider nao suportado",
        }), 400

    # Extracao defensiva do token FocusNFe: POP remove a chave do payload
    # antes de qualquer serializacao/log posterior. So captura se o provider
    # da requisicao for focusnfe; para SEFAZ, remove sem usar.
    if provider_payload == "focusnfe":
        focusnfe_token = payload.pop("focusnfe_token", None)
        # Blindagem: se provider=focusnfe, ignorar defensivamente qualquer campo
        # de certificado A1 que MapOne possa ter enviado por engano. O contrato
        # correto e MapOne nao enviar cert quando provider=focusnfe, mas o
        # FiscalOne precisa estar protegido antes do ajuste no MapOne.
        payload.pop("cert_pfx_base64", None)
        payload.pop("cert_password", None)
        payload.pop("cert_cnpj", None)
        payload.pop("cert_valid_until", None)
    else:
        # Provider=sefaz ou fallback env: remove focusnfe_token se vier por engano;
        # SefazProvider nunca precisa desse campo.
        payload.pop("focusnfe_token", None)
        focusnfe_token = None

    try:
        provider = get_provider(provider_payload or None, token=focusnfe_token)
        result = provider.gov_fetch(payload, trace_id)
    except NotImplementedError as e:
        _log_stdout("gov_fetch", "erro", trace_id, source_system=source_system,
                    erro_msg=f"PROVIDER_NAO_IMPLEMENTADO: {FISCAL_PROVIDER}")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PROVIDER_NAO_IMPLEMENTADO",
            "erro":     f"Provider '{FISCAL_PROVIDER}' nao implementa gov_fetch.",
        }), 501
    except Exception as e:
        # Nao vaza traceback nem stack — apenas tipo de erro
        _log_stdout("gov_fetch", "erro", trace_id,
                    source_system=source_system,
                    erro_msg=f"{type(e).__name__}: excecao interna")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "ERRO_INTERNO",
            "erro":     "Erro interno ao consultar SEFAZ — verifique logs do FiscalOne",
        }), 500

    duracao_ms = int((time.monotonic() - t0) * 1000)

    docs_arr    = result.get("documentos") or []
    resumos_arr = result.get("resumos") or []
    erros_arr   = result.get("erros") or []
    # Fase E1B: providers modernos (FocusNFe) preenchem `documentos[]` mas nao
    # `results[]`. MapOne consome `results[]`. Fallback: se provider nao entregou
    # `results` explicito, espelhamos `documentos` para preservar compat.
    # Providers legados que ja preenchem `results` (ex.: SEFAZ) tem esse array
    # preservado — nunca sobrescrito.
    results_arr = result.get("results") or docs_arr
    persistidos = len(docs_arr)
    resumos_n   = len(resumos_arr)
    eventos_n   = sum(1 for d in docs_arr + results_arr
                      if d.get("status_xml") == "EVENTO")
    erros_n     = len(erros_arr)
    processados = persistidos + resumos_n + erros_n

    # Classificacao definitiva para MapOne — decide se atualiza NSU no CtrlOne.
    docs_efetivos    = persistidos + resumos_n + eventos_n
    acao             = _classificar_acao_gov_fetch(result, docs_efetivos)
    nsu_avancou      = _nsu_avancou(acao)
    ultimo_nsu_antes = (payload.get("ultimo_nsu") or "0")
    ultimo_nsu_pos   = result.get("ultimo_nsu")
    max_nsu          = result.get("max_nsu")

    envelope = {
        "ok":                       bool(result.get("ok")),
        "trace_id":                 trace_id,
        "codigo":                   result.get("codigo"),
        "acao":                     acao,
        "nsu_avancou":              nsu_avancou,
        "status_lote":              _classificar_status_lote(processados, persistidos, erros_n),
        "recebidos":                processados,
        "processados":              processados,
        "persistidos":              persistidos,
        "duplicados":               0,   # duplicidade e escopo do MapOne
        "resumos_count":            resumos_n,
        "eventos":                  eventos_n,
        "erros_count":              erros_n,
        "cstat":                    result.get("cstat"),
        "xmotivo":                  result.get("xmotivo"),
        "provider":                 result.get("provider"),
        "ambiente_adn":             result.get("ambiente_adn"),
        "status":                   result.get("status"),
        "status_processamento":     result.get("status_processamento"),
        "ultimo_nsu_antes":         ultimo_nsu_antes,
        "ultimo_nsu":               ultimo_nsu_pos,
        "max_nsu":                  max_nsu,
        "cooldown_recomendado_seg": result.get("cooldown_recomendado_seg"),
        "documentos":               docs_arr,
        "resumos":                  resumos_arr,
        "erros":                    erros_arr,
        "results":                  results_arr,
        "duracao_ms":               result.get("duracao_ms") or duracao_ms,
        "cert_fonte":               result.get("cert_fonte"),
        "erro":                     result.get("erro") if not result.get("ok") else None,
        "ambiente":                 (payload.get("ambiente") or "homologacao").lower(),
        "tipo":                     tipo,
        "data_inicio":              payload.get("data_inicio") or result.get("data_inicio"),
    }
    _ARRAY_KEYS = ("documentos", "resumos", "erros", "results")
    envelope = {k: v for k, v in envelope.items() if v is not None or k in _ARRAY_KEYS or k == "ok"}

    # Log operacional: cstat, acao, ultimo_nsu_antes, max_nsu, nsu_avancou.
    resultado_log = "ok" if result.get("ok") else "erro"
    _log_stdout("gov_fetch", resultado_log, trace_id,
                source_system=source_system,
                company_cnpj=cnpj_tenant, doc_type=tipo,
                cstat=envelope.get("cstat"),
                acao=acao,
                ultimo_nsu_antes=ultimo_nsu_antes,
                ultimo_nsu=ultimo_nsu_pos,
                max_nsu=max_nsu,
                nsu_avancou=nsu_avancou,
                duracao_ms=duracao_ms,
                erro_msg=result.get("codigo") if not result.get("ok") else None)

    status = 200 if envelope["ok"] else _status_para_codigo(envelope.get("codigo"))
    return jsonify(envelope), status


def _status_para_codigo(codigo):
    """Mapeia codigo de erro controlado -> HTTP status."""
    if codigo in ("CERT_NAO_CONFIGURADO", "CERT_CNPJ_DIVERGENTE",
                  "CERT_ABERTURA_FALHOU", "CERT_BASE64_INVALIDO",
                  "CERT_INVALIDO", "CERT_ENV_INVALIDO",
                  "CERT_SEM_CNPJ", "CERT_FONTE_NAO_SUPORTADA"):
        return 400
    if codigo in ("SEFAZ_INDISPONIVEL", "SEFAZ_HTTP_ERRO", "SEFAZ_XML_INVALIDO", "TLS_ERRO",
                  "NFSE_ADN_HTTP_ERRO", "NFSE_ADN_TIMEOUT",
                  "NFSE_ADN_XML_INVALIDO", "NFSE_ADN_AUTH_ERRO"):
        return 502
    if codigo == "PROVIDER_NAO_IMPLEMENTADO":
        return 501
    # Fase D — validacao de payload no handler retorna 400 com este codigo,
    # mas o mapeamento explicito abaixo cobre casos em que o codigo aparece
    # em result do provider (defesa em profundidade).
    if codigo == "PROVIDER_INVALIDO":
        return 400
    # Fase 2 HTTP FocusNFe — mapeamento de codigos do provider Focus.
    if codigo in ("FOCUS_TOKEN_AUSENTE", "FOCUS_BAD_REQUEST",
                  "FOCUS_TIPO_NAO_SUPORTADO"):
        return 400
    if codigo == "FOCUS_AUTH_ERROR":
        return 401
    if codigo == "FOCUS_FORBIDDEN":
        return 403
    if codigo == "FOCUS_RATE_LIMIT":
        return 429
    if codigo in ("FOCUS_TIMEOUT", "FOCUS_UNAVAILABLE", "FOCUS_SERVER_ERROR",
                  "FOCUS_HTTP_ERROR", "FOCUS_PARSE_ERROR", "FOCUS_SCHEMA_ERROR"):
        return 502
    # Fase E4b-1A — codigos de manifestacao de Ciencia (evento 210210).
    if codigo in ("FOCUS_MANIFESTO_TIPO_NAO_SUPORTADO",
                  "FOCUS_MANIFESTO_CHAVE_INVALIDA",
                  "FOCUS_MANIFESTO_PROVIDER_INVALIDO",
                  "FOCUS_MANIFESTO_INVALIDO"):
        return 400
    if codigo == "FOCUS_MANIFESTO_NAO_ENCONTRADO":
        return 404
    if codigo == "FOCUS_MANIFESTO_CONFLITO":
        return 409
    if codigo == "FOCUS_MANIFESTO_HTTP_ERROR":
        return 502
    return 500

# ── POST /fiscal/nfe/recebida/manifesto ───────────────────────────────────────

@app.route("/fiscal/nfe/recebida/manifesto", methods=["POST"])
def nfe_recebida_manifesto():
    """
    Manifestacao de Ciencia da Operacao de NF-e recebida via FocusNFe.

    Fase E4b-1A — FiscalOne stateless. Nao persiste nada.
      - Suporta apenas tipo="ciencia" (evento SEFAZ 210210).
      - Suporta apenas provider="focusnfe".
      - `confirmacao` (210200), `desconhecimento` (210220) e
        `nao_realizada` (210240) permanecem BLOQUEADOS.
      - NF-e/CT-e/NFS-e/MDF-e emissao seguem bloqueadas (rota separada).

    Payload:
      {
        "chave":           "44_digitos",
        "tipo":            "ciencia",
        "ambiente":        "producao"|"homologacao",
        "focusnfe_token":  "<token>",
        "provider":        "focusnfe"   (opcional; default focusnfe)
      }

    focusnfe_token, cert_pfx_base64, cert_password sao removidos do payload
    antes de qualquer log/retorno.
    """
    trace_id      = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")

    if _producao_bloqueada():
        return _bloqueio_producao("nfe_recebida_manifesto", trace_id, source_system)

    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    if not isinstance(payload, dict) or not payload:
        _log_stdout("nfe_recebida_manifesto", "erro", trace_id,
                    source_system=source_system,
                    erro_msg="payload JSON ausente ou invalido")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "PAYLOAD_INVALIDO",
            "erro":     "Payload JSON obrigatorio (chave, tipo, ambiente, focusnfe_token).",
        }), 400

    # Provider — so aceita focusnfe. Default focusnfe se ausente.
    provider_payload = (payload.get("provider") or "focusnfe").strip().lower()
    if provider_payload != "focusnfe":
        _log_stdout("nfe_recebida_manifesto", "erro", trace_id,
                    source_system=source_system,
                    erro_msg=f"FOCUS_MANIFESTO_PROVIDER_INVALIDO: {provider_payload!r}")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "FOCUS_MANIFESTO_PROVIDER_INVALIDO",
            "erro":     "Manifestacao de Ciencia so disponivel via provider='focusnfe'.",
        }), 400

    # Sanitizacao — POP campos sensiveis ANTES de qualquer log/serializacao.
    focusnfe_token = payload.pop("focusnfe_token", None)
    payload.pop("cert_pfx_base64", None)
    payload.pop("cert_password", None)
    payload.pop("cert_cnpj", None)
    payload.pop("cert_valid_until", None)
    # Cabecalho eventualmente injetado no body — remover por defesa.
    payload.pop("Authorization", None)
    payload.pop("authorization", None)

    chave    = str(payload.get("chave") or "").strip()
    tipo     = str(payload.get("tipo") or "").strip().lower()
    ambiente = str(payload.get("ambiente") or "").strip().lower() or None

    try:
        provider = get_provider("focusnfe", token=focusnfe_token)
        resp = provider.manifestar_nfe_recebida(chave, tipo, ambiente, trace_id)
    except Exception as e:
        _log_stdout("nfe_recebida_manifesto", "erro", trace_id,
                    source_system=source_system,
                    erro_msg=f"{type(e).__name__}: excecao interna")
        return jsonify({
            "ok":       False,
            "trace_id": trace_id,
            "codigo":   "ERRO_INTERNO",
            "erro":     "Erro interno ao manifestar — verifique logs do FiscalOne.",
        }), 500

    # Log operacional — chave mascarada, sem token/xml/authorization/body.
    chave_mascarada = f"{chave[:6]}***{chave[-4:]}" if len(chave) == 44 else "***"
    _log_stdout(
        "nfe_recebida_manifesto",
        "ok" if resp.get("ok") else "erro",
        trace_id,
        source_system=source_system,
        chave_doc=chave_mascarada,
        doc_type="nfe",
        cstat=resp.get("cstat"),
        erro_msg=resp.get("codigo") if not resp.get("ok") else None,
    )

    status = 200 if resp.get("ok") else _status_para_codigo(resp.get("codigo"))
    return jsonify(resp), status


# ── Rotas legadas (provider pattern — mantidas, stubs) ────────────────────────

@app.route("/fiscal/sync/<cnpj>", methods=["POST"])
def sync(cnpj):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("sync", trace_id, source_system)
    return _provider_response("sync", get_provider().sync(cnpj))

@app.route("/fiscal/nfe/<cnpj>")
def listar_nfe(cnpj):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("listar_nfe", trace_id, source_system)
    return _provider_response("listar_nfe", get_provider().listar_nfe(cnpj))

@app.route("/fiscal/cte/<cnpj>")
def listar_cte(cnpj):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("listar_cte", trace_id, source_system)
    return _provider_response("listar_cte", get_provider().listar_cte(cnpj))

@app.route("/fiscal/nfe/chave/<chave>")
def detalhe_nfe(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("detalhe_nfe", trace_id, source_system)
    return _provider_response("detalhe_nfe", get_provider().detalhe_nfe(chave))

@app.route("/fiscal/cte/chave/<chave>")
def detalhe_cte(chave):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("detalhe_cte", trace_id, source_system)
    return _provider_response("detalhe_cte", get_provider().detalhe_cte(chave))

# ── Emissao / cancelamento / MDF-e — BLOQUEIO ABSOLUTO ─────────────────────
# Independem das flags de producao. FiscalOne nesta fase e apenas gateway
# para consulta/recepcao DFe. Assinatura, emissao, cancelamento, inutilizacao,
# CC-e, encerramento MDF-e e condutor MDF-e ficam bloqueados por design.

@app.route("/fiscal/cte", methods=["POST"])
def emitir_cte():
    return bloquear_emissao("emitir_cte", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/mdfe", methods=["POST"])
def emitir_mdfe():
    return bloquear_emissao("emitir_mdfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/mdfe/<chave>/encerrar", methods=["POST"])
def encerrar_mdfe(chave):
    return bloquear_emissao("encerrar_mdfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/mdfe/<chave>/condutor", methods=["POST"])
def incluir_condutor_mdfe(chave):
    return bloquear_emissao("incluir_condutor_mdfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/cte/<chave>", methods=["DELETE"])
def cancelar_cte(chave):
    return bloquear_emissao("cancelar_cte", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

# Rotas defensivas para emissoes futuras — sempre bloqueadas
@app.route("/fiscal/nfe", methods=["POST"])
def emitir_nfe():
    return bloquear_emissao("emitir_nfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/nfe/<chave>", methods=["DELETE"])
def cancelar_nfe(chave):
    return bloquear_emissao("cancelar_nfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/nfe/<chave>/inutilizar", methods=["POST"])
def inutilizar_nfe(chave):
    return bloquear_emissao("inutilizar_nfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/nfe/<chave>/cce", methods=["POST"])
def cce_nfe(chave):
    return bloquear_emissao("cce_nfe", _trace(request),
                            request.headers.get("X-Source-System", "desconhecido"))

@app.route("/fiscal/status/<uf>")
def status_sefaz(uf):
    trace_id = _trace(request)
    source_system = request.headers.get("X-Source-System", "desconhecido")
    if _producao_bloqueada():
        return _bloqueio_producao("status_sefaz", trace_id, source_system)
    return _provider_response("status_sefaz", get_provider().status_sefaz(uf))

if __name__ == "__main__":
    app.run(port=5002, debug=False, threaded=True)
