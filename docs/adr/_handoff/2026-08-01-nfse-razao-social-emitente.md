# Handoff — razão social do prestador NFS-e

**Data:** 2026-08-01

O payload real da Focus usa `cnpj_prestador` para o documento e
`razao_social_emitente` para o nome. O mapper agora reconhece essa assimetria e
publica `emit_nome`. A prova focada do provider/cursor encerrou com 133 testes
verdes. O deploy deve preceder uma nova importação; o MapOne possui backfill
separado para os 69 Espelhos já gravados.
