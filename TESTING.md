# TESTING

Este arquivo reúne, de forma mais objetiva, as camadas de teste aplicadas ao projeto.

## Objetivo

Como esta ferramenta lida com arquivos, o foco dos testes não é apenas verificar se os comandos "funcionam", mas também validar:

- comportamento seguro por padrão
- integridade após cópia
- reação adequada a cenários perigosos
- consistência do histórico local
- previsibilidade da CLI em casos de erro

## Como rodar localmente

Na raiz do projeto:

```bash
python -m unittest discover -s tests -v
```

## Cobertura automatizada atual

A suíte automatizada cobre atualmente **38 testes**.

Ela está dividida em três frentes:

### 1. Testes do motor de sincronização

Arquivo:

- [`tests/test_engine.py`](./tests/test_engine.py)

Cobre, entre outros pontos:

- cópia inicial e atualização de arquivos
- `dry-run` sem escrita real
- regras de ignore
- restore de snapshots
- bloqueio de restore sem `--overwrite`
- rejeição de snapshot de `dry-run`
- exportação de relatório JSON
- rejeição de configurações perigosas de caminhos
- persistência de execução com status `failed`
- arquivo binário grande com validação byte a byte
- preservação de caminhos profundamente aninhados
- deduplicação de blobs
- liberação do SQLite após uso
- rollback quando a verificação final de integridade falha
- bloqueio de execução concorrente por lock
- stress test com centenas de arquivos

### 2. Testes do banco e histórico

Arquivo:

- [`tests/test_database.py`](./tests/test_database.py)

Cobre:

- reutilização de perfil existente
- ordenação e limite do histórico
- erro ao exportar relatório de execução inexistente

### 3. Testes de integração da CLI

Arquivo:

- [`tests/test_cli.py`](./tests/test_cli.py)

Cobre:

- `--help`
- mensagens e códigos de saída
- preview por padrão
- necessidade explícita de `--apply`
- fluxo completo `sync -> history -> report -> restore`
- criação de relatórios em diretórios aninhados
- respeito às regras de ignore pela CLI
- bloqueio por lock já existente
- rejeição de `state-dir` inseguro

## Testes manuais já executados

Além da suíte automatizada, também foram executados testes manuais no ambiente local durante o desenvolvimento:

- smoke test completo com preview, apply, history, report e restore
- update real de arquivo já sincronizado
- restore de snapshot anterior com validação do conteúdo
- stress test manual com 500 arquivos
- simulação de lock manual para recusa segura de sync concorrente

## O que ainda depende de ambiente externo

Alguns cenários não são totalmente garantidos apenas com testes locais automatizados:

- arquivos abertos por outros programas reais
- discos externos
- pastas em rede
- ACLs/permissões específicas de ambientes corporativos
- diferenças entre máquinas e configurações do sistema operacional

## Integração contínua

O workflow de CI executa a suíte automaticamente em:

- `ubuntu-latest`
- `windows-latest`

Links úteis:

- [`.github/workflows/backup-sync-cli.yml`](./.github/workflows/backup-sync-cli.yml)
- [Workflow no GitHub Actions](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml)
