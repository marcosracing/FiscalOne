# Handoff — FiscalOne preparado para requisições multiempresa

**Data:** 2026-07-11
**Produto:** FiscalOne
**Escopo:** DFe recebido via MapOne multiempresa

## Objetivo

Permitir que o MapOne chame o FiscalOne para mais de um tenant no mesmo ciclo
operacional, sem depender de usuário logado e sem persistência própria no
gateway.

## Ajuste aplicado

`app.py` agora sobe o Flask local/VM simples com:

```python
app.run(port=5002, debug=False, threaded=True)
```

Isso permite atender chamadas simultâneas controladas do agendador MapOne.

## Limites de segurança preservados

- FiscalOne não guarda certificado A1 em repouso.
- Certificado chega em trânsito por payload e é descartado após a requisição.
- Logs não devem conter PFX, senha, base64, PEM ou XML completo.
- Emissão fiscal segue bloqueada por design.
- Persistência de XML, eventos, NSU e cooldown é responsabilidade da vertical.

## Observação operacional

Em produção definitiva, o mesmo comportamento deve ser obtido por WSGI/gunicorn
com workers/threads controlados. O ajuste atual atende o ambiente local e a VM
de teste onde o serviço roda via `python app.py`.
