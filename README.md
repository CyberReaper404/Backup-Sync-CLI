# Backup Sync CLI

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![SQLite](https://img.shields.io/badge/SQLite-Historico%20Local-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![CLI](https://img.shields.io/badge/Interface-CLI-222222?logo=gnubash&logoColor=white)](https://pt.wikipedia.org/wiki/Interface_de_linha_de_comando)
[![Testes](https://img.shields.io/badge/Testes-unittest-0F766E)](https://docs.python.org/pt-br/3/library/unittest.html)
[![CI](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml/badge.svg)](https://github.com/CyberReaper404/Backup-Sync-CLI/actions/workflows/backup-sync-cli.yml)

Ferramenta de backup e sincronização de pastas feita para rodar 100% no terminal, com foco em segurança operacional, rastreabilidade e restauração de snapshots.

Em vez de ser apenas um script simples de cópia de arquivos, este projeto mantém histórico local em SQLite, armazena blobs versionados, trabalha com pré-visualização por padrão, oferece perfis nomeados de sincronização, barra de progresso opcional, filtros reutilizáveis e compactação segura de blobs antigos.

## Intenção do projeto

Este projeto foi desenvolvido para estudo e portfólio.

O objetivo principal é aprofundar conhecimentos práticos em Python, SQLite, design de CLI, manipulação de arquivos, validação de integridade, persistência local, testes automatizados e pipelines de CI.

Este é um projeto sem fins lucrativos, assim como os outros projetos deste portfólio. Ele não foi criado com a intenção de ser vendido como um produto de backup pronto para o mercado.

Por isso, o repositório deve ser entendido como um exercício de engenharia com foco em implementação cuidadosa e decisões seguras de design, e não como substituto de softwares de backup maduros e consolidados.

## Por que este projeto vale a pena

- resolve um problema real, em vez de ser apenas mais um CRUD genérico
- demonstra I/O de arquivos, hashing, persistência, relatórios e design de CLI
- prioriza padrões seguros, como preview por padrão, restore separado e bloqueio de caminhos perigosos
- combina experiência de uso, confiabilidade e documentação
- inclui testes automatizados e CI, deixando o repositório com uma apresentação mais profissional

## O que ele faz

- sincroniza arquivos da origem para o destino
- compara conteúdo usando hash SHA-256
- suporta regras de exclusão por nome ou padrão glob
- filtra por extensão, tamanho mínimo, tamanho máximo e data de modificação
- permite salvar perfis nomeados para reutilizar origem, destino e filtros
- exibe barra de progresso opcional em execuções longas
- registra histórico local das execuções em SQLite
- armazena blobs versionados para restauração posterior
- compacta blobs antigos com gzip quando isso reduz o espaço ocupado
- exporta relatórios JSON
- restaura snapshots salvos em outra pasta
- usa cópia atômica com verificação de integridade antes de substituir arquivos
- rejeita symlinks e reparse points por padrão para reduzir risco operacional

## Escolhas de segurança

Estas decisões foram intencionais:

- não apaga arquivos extras do destino nesta versão
- roda em modo preview por padrão e só escreve com `--apply`
- bloqueia origem e destino sobrepostos
- bloqueia `state-dir` sobreposto à origem ou ao destino
- rejeita symlinks e reparse points na origem, no destino, no estado local e no restore
- trata o restore como operação separada, sem sobrescrever a origem
- usa lock para impedir sync, restore e compactação concorrentes no mesmo estado
- verifica a integridade após a cópia antes de considerar a operação concluída
- só compacta blobs quando o arquivo compactado fica realmente menor e continua íntegro

Isso deixa a ferramenta mais prudente e mais coerente com o risco de mexer em arquivos.

## Exemplo rápido

```bash
backup sync "C:\Projetos" "D:\Backups\Projetos" --ignore node_modules --ignore "*.log"
```

```text
Execucao: 1
Status: completed
Modo: dry-run
Origem: C:\Projetos
Destino: D:\Backups\Projetos
Perfil: execucao avulsa
Lidos: 42
Copiados: 30
Atualizados: 4
Ignorados: 8
Bytes copiados: 182044
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

### Filtrar por extensão e tamanho

```bash
backup sync "C:\Projetos" "D:\Backups\Projetos" --apply --ext .py --min-size-bytes 20 --max-size-bytes 50000
```

### Exibir barra de progresso

```bash
backup sync "C:\Projetos" "D:\Backups\Projetos" --apply --progress
```

### Salvar um perfil nomeado

```bash
backup profile save backend-daily "C:\Projetos\API" "D:\Backups\API" --ignore ".git" --ignore "*.log" --ext .py
```

### Executar um perfil salvo

```bash
backup profile run backend-daily --apply --report reports\backend-daily.json
```

### Ver os perfis disponíveis

```bash
backup profile list --details
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

### Compactar blobs antigos

```bash
backup compact --older-than-days 30
```

## Estado e armazenamento

Por padrão, a CLI salva o estado fora das pastas sincronizadas, em um diretório de estado da aplicação no sistema operacional.

Esse estado contém:

- `backup.db`: banco SQLite com o histórico das execuções e a tabela de perfis nomeados
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
- `--ext EXTENSAO`: sincroniza apenas arquivos com a extensão informada
- `--min-size-bytes N`: inclui apenas arquivos com tamanho igual ou superior a `N`
- `--max-size-bytes N`: inclui apenas arquivos com tamanho igual ou inferior a `N`
- `--modified-after DATA_ISO`: inclui apenas arquivos modificados depois da data informada
- `--modified-before DATA_ISO`: inclui apenas arquivos modificados antes da data informada
- modo padrão: preview, sem escrita
- `--apply`: escreve alterações verificadas no disco
- `--dry-run`: preview explícito
- `--progress`: força a barra de progresso
- `--no-progress`: desabilita a barra de progresso
- `--report PATH`: exporta a execução em JSON ao final

### `backup profile save`

Salva ou atualiza um perfil nomeado com origem, destino e filtros.

### `backup profile list`

Lista os perfis salvos.

### `backup profile show`

Exibe os detalhes de um perfil salvo.

### `backup profile run`

Executa um perfil salvo com os filtros armazenados.

### `backup history`

Lista execuções recentes armazenadas no SQLite.

### `backup report`

Exporta uma execução gravada para JSON.

### `backup restore`

Restaura um snapshot salvo para outra pasta.

### `backup compact`

Compacta blobs antigos com gzip quando isso reduz o espaço em disco.

## Estrutura do projeto

```text
backup-sync-cli/
|-- .github/
|   `-- workflows/
|       `-- backup-sync-cli.yml
|-- pyproject.toml
|-- README.md
|-- TESTING.md
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

Atualmente, o projeto conta com **51 testes automatizados** na suíte principal.

Cobertura automatizada atual:

- testes de integração da CLI
- fluxos de sync e update
- comportamento de preview por padrão e `--apply`
- comportamento de `dry-run`
- perfis nomeados
- filtros por extensão, tamanho e data
- barra de progresso
- compactação de blobs com restore transparente
- regras de ignore
- restore
- relatórios JSON
- persistência de falha
- validação com arquivo binário grande
- caminhos profundamente aninhados
- liberação do SQLite no Windows
- rollback quando a verificação final falha
- rejeição de `state-dir` inseguro
- bloqueio de concorrência por lock

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
- mostra preocupação com UX de terminal
- combina persistência local, restore, filtros, perfis, testes e CI
- documenta tanto o uso quanto a intenção do projeto

## Próximos passos possíveis

- modo opcional de exclusão com confirmação explícita
- comando `doctor` para validar ambiente e caminhos antes do sync
- empacotamento binário para distribuição simplificada

## Licença

Este projeto está licenciado sob a MIT License.
