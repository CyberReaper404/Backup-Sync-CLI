# Backup Sync CLI

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-Hist%C3%B3rico%20Local-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![CLI](https://img.shields.io/badge/Interface-CLI-222222?logo=gnubash&logoColor=white)](https://pt.wikipedia.org/wiki/Interface_de_linha_de_comando)
[![Testes](https://img.shields.io/badge/Testes-unittest-0F766E)](https://docs.python.org/pt-br/3/library/unittest.html)
[![CI](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml/badge.svg)](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml)

Ferramenta de sincronização e backup de pastas feita para rodar 100% no terminal, com foco em segurança operacional, rastreabilidade e restauração de snapshots.

Em vez de ser apenas um script simples de cópia de arquivos, este projeto registra histórico local em SQLite, armazena blobs versionados, oferece um modo de pré-visualização antes da escrita, exporta relatórios JSON e permite restaurar uma execução anterior em uma pasta separada.

## Intenção do projeto

Este projeto foi desenvolvido para estudo e portfólio.

O objetivo principal é aprofundar conhecimentos práticos em Python, SQLite, design de CLI, manipulação de arquivos, validação de integridade, testes automatizados e pipelines de CI.

Este é um projeto sem fins lucrativos, assim como os outros projetos deste portfólio. Ele não foi criado com a intenção de ser vendido como um produto de backup pronto para o mercado.

Por isso, o repositório deve ser entendido como um exercício de engenharia com foco em implementação cuidadosa e decisões seguras de design, e não como substituto de softwares de backup maduros e consolidados.

## Por que este projeto vale a pena

- resolve um problema real, em vez de ser apenas mais um CRUD genérico
- demonstra I/O de arquivos, hashing, persistência, relatórios e design de CLI
- prioriza padrões seguros, como modo preview, restore separado e bloqueios de caminhos perigosos
- inclui testes automatizados e CI, deixando o repositório com uma apresentação mais profissional

## O que ele faz

- sincroniza arquivos da pasta de origem para a pasta de destino
- compara o conteúdo usando hash SHA-256
- suporta regras de exclusão por nome ou padrão glob
- registra o histórico das execuções em SQLite
- armazena blobs versionados para restauração posterior
- exporta relatórios JSON
- restaura um snapshot salvo em outra pasta
- usa cópia atômica com verificação de integridade antes de substituir arquivos
- rejeita symlinks e reparse points por padrão para reduzir risco operacional

## Escolhas de segurança

Estas decisões foram intencionais:

- não apaga arquivos extras do destino na v1
- roda em modo preview por padrão e só escreve com `--apply`
- bloqueia origem e destino sobrepostos
- bloqueia `state-dir` sobreposto à origem ou ao destino
- rejeita symlinks e reparse points na origem, no destino e no restore
- trata o restore como uma operação separada, sem sobrescrever a origem
- verifica a integridade após a cópia antes de considerar a operação concluída

Isso deixa a ferramenta mais prudente e mais coerente com o risco de mexer em arquivos.

## Exemplo rápido

```bash
backup sync "C:\Projetos" "D:\Backups\Projetos" --ignore node_modules --ignore "*.log"
```

```text
Run: 1
Status: completed
Mode: dry-run
Source: C:\Projetos
Destination: D:\Backups\Projetos
Scanned: 42
Copied: 30
Updated: 4
Skipped: 8
Bytes copied: 182044
```

## Instalação

### Rodar diretamente no projeto

```bash
python -m safesync.cli --help
```

### Instalar como comando

```bash
pip install -e .
backup --help
```

## Uso

### Visualizar a sincronização antes de escrever

```bash
backup sync "C:\Projetos" "D:\Backups\Projetos" --ignore node_modules --ignore "*.tmp"
```

### Executar a sincronização de verdade e salvar um relatório

```bash
backup sync "C:\Projetos" "D:\Backups\Projetos" --ignore node_modules --apply --report reports\ultimo-run.json
```

### Ver as últimas execuções

```bash
backup history --limit 5
```

### Exportar uma execução antiga em JSON

```bash
backup report 3 --output reports\run-3.json
```

### Restaurar um snapshot anterior

```bash
backup restore 3 "C:\Restore\Run-3"
```

## Estado e armazenamento

Por padrão, a CLI salva o estado fora das pastas sincronizadas, em um diretório de estado da aplicação no sistema operacional.

Esse estado contém:

- `backup.db`: banco SQLite com o histórico das execuções
- `blobs/`: cópias versionadas dos arquivos, organizadas por hash

Se quiser definir outro local, use `--state-dir`:

```bash
backup --state-dir "C:\SafeSyncData" history
```

## Comandos

### `backup sync`

Sincroniza arquivos da origem para o destino.

Argumentos:

- `source`: pasta de origem
- `destination`: pasta de destino
- `--ignore PATTERN`: ignora por nome de arquivo, nome de pasta ou padrão glob
- modo padrão: preview, sem escrita
- `--apply`: escreve alterações verificadas no disco
- `--dry-run`: preview explícito
- `--report PATH`: exporta a execução em JSON ao final

### `backup history`

Lista execuções recentes armazenadas no SQLite.

### `backup report`

Exporta uma execução gravada para JSON.

### `backup restore`

Restaura um snapshot salvo para outra pasta.

## Estrutura do projeto

```text
backup-sync-cli/
|-- pyproject.toml
|-- README.md
|-- LICENSE
|-- safesync/
|   |-- cli.py
|   |-- database.py
|   |-- engine.py
|   |-- hashing.py
|   |-- models.py
|   `-- safety.py
`-- tests/
    |-- test_cli.py
    |-- test_database.py
    `-- test_engine.py
```

## Testes

Execute a suíte completa com:

```bash
python -m unittest discover -s tests -v
```

Atualmente, o projeto conta com **38 testes automatizados** na suíte principal, além de validações manuais de smoke test e stress test realizadas durante o desenvolvimento.

Cobertura automatizada atual:

- testes de integração da CLI
- fluxos de sync e update
- comportamento de preview por padrão e `--apply`
- comportamento de `dry-run`
- regras de ignore
- restore
- relatórios JSON
- persistência de falha
- validação com arquivo binário grande
- caminhos profundamente aninhados
- liberação do SQLite no Windows
- rollback quando a verificação final falha
- rejeição de `state-dir` inseguro

Documentação detalhada dos testes:

- [TESTING.md](./TESTING.md)

## Integração contínua

O projeto inclui um workflow de GitHub Actions que roda a suíte de testes em:

- `ubuntu-latest`
- `windows-latest`

Arquivo do workflow:

- [`.github/workflows/backup-sync-cli.yml`](./.github/workflows/backup-sync-cli.yml)
- [Workflow no GitHub Actions](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml)

## Por que este projeto faz sentido no GitHub

- tem escopo focado e resolve um problema real
- trata risco com seriedade
- tem uma UX clara de terminal
- combina persistência local, restore, testes e CI
- documenta tanto o uso quanto a intenção do projeto

## Possíveis próximos passos

- perfis nomeados de sincronização
- modo opcional de exclusão com confirmação explícita
- barra de progresso para syncs longos
- compactação de blobs antigos
- filtros por extensão, tamanho ou data de modificação
- comando `doctor` para validar ambiente e caminhos antes do sync

## Licença

Este projeto está licenciado sob a MIT License.
