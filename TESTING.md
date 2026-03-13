# TESTING

Este arquivo reúne, de forma objetiva, as camadas de teste aplicadas ao projeto.

## Objetivo

Como esta ferramenta lida com arquivos, o foco dos testes não é apenas verificar se os comandos "funcionam", mas também validar:

- comportamento seguro por padrão
- integridade após cópia
- reação adequada a cenários perigosos
- consistência do histórico local
- previsibilidade da CLI em casos de erro
- confiabilidade do restore após compactação de blobs

## Como rodar localmente

Na raiz do projeto:

```bash
python -m unittest discover -s tests -v
```

## Cobertura automatizada atual

A suíte automatizada cobre atualmente **51 testes**.

Ela está dividida em três frentes:

## 1. Testes do motor de sincronização

Arquivo:

- [`tests/test_engine.py`](./tests/test_engine.py)

Cobre, entre outros pontos:

- cópia inicial e atualização de arquivos
- `dry-run` sem escrita real
- regras de ignore
- filtros por extensão, tamanho e data de modificação
- perfis nomeados salvos e executados pelo motor
- barra de progresso via callback
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
- compactação de blobs com preservação do restore

## 2. Testes do banco e histórico

Arquivo:

- [`tests/test_database.py`](./tests/test_database.py)

Cobre:

- reutilização de perfil existente
- persistência e leitura de perfis nomeados
- persistência de filtros avançados nas execuções
- ordenação e limite do histórico
- atualização do formato de armazenamento de blobs compactados
- erro ao exportar relatório de execução inexistente

## 3. Testes de integração da CLI

Arquivo:

- [`tests/test_cli.py`](./tests/test_cli.py)

Cobre:

- `--help`
- mensagens e códigos de saída
- preview por padrão
- necessidade explícita de `--apply`
- filtros pela CLI
- barra de progresso
- fluxo completo `sync -> history -> report -> restore`
- criação de relatórios em diretórios aninhados
- perfis nomeados pela CLI
- compactação via comando `compact`
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
- ACLs e permissões específicas de ambientes corporativos
- diferenças entre máquinas e configurações do sistema operacional
- criação de symlink em ambientes que não liberam esse recurso

## Integração contínua

O workflow de CI executa a suíte automaticamente em:

- `ubuntu-latest`
- `windows-latest`

Links úteis:

- [`.github/workflows/backup-sync-cli.yml`](./.github/workflows/backup-sync-cli.yml)
- [Workflow no GitHub Actions](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml)
