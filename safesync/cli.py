from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .database import StateDatabase
from .engine import SyncEngine
from .models import ProgressUpdate, SyncFilters
from .safety import default_state_dir


def build_parser() -> argparse.ArgumentParser:
    """Monta a interface de linha de comando da aplicação."""
    parser = argparse.ArgumentParser(
        prog="backup",
        description="CLI de backup e sincronização com histórico local em SQLite.",
    )
    parser.add_argument(
        "--state-dir",
        default=str(default_state_dir()),
        help="Diretório usado para guardar o SQLite, blobs versionados e perfis.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Sincroniza arquivos da origem para o destino.")
    sync_parser.add_argument("source", help="Diretório de origem.")
    sync_parser.add_argument("destination", help="Diretório de destino.")
    _add_filter_arguments(sync_parser)
    _add_mode_arguments(sync_parser)
    _add_progress_arguments(sync_parser)
    sync_parser.add_argument(
        "--report",
        help="Caminho opcional para salvar o relatório JSON desta execução.",
    )

    history_parser = subparsers.add_parser("history", help="Mostra execuções recentes.")
    history_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Quantidade de execuções mostradas.",
    )

    report_parser = subparsers.add_parser("report", help="Exporta uma execução salva em JSON.")
    report_parser.add_argument("run_id", type=int, help="Identificador da execução.")
    report_parser.add_argument(
        "--output",
        required=True,
        help="Arquivo de saída do relatório JSON.",
    )

    restore_parser = subparsers.add_parser(
        "restore",
        help="Restaura um snapshot salvo para outro diretório.",
    )
    restore_parser.add_argument("run_id", type=int, help="Identificador da execução.")
    restore_parser.add_argument("output", help="Diretório que receberá o snapshot restaurado.")
    restore_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Sobrescreve arquivos já existentes na pasta de restore.",
    )

    profile_parser = subparsers.add_parser("profile", help="Gerencia perfis nomeados de sincronização.")
    profile_subparsers = profile_parser.add_subparsers(dest="profile_command", required=True)

    profile_save_parser = profile_subparsers.add_parser(
        "save",
        help="Salva ou atualiza um perfil nomeado.",
    )
    profile_save_parser.add_argument("name", help="Nome do perfil.")
    profile_save_parser.add_argument("source", help="Diretório de origem.")
    profile_save_parser.add_argument("destination", help="Diretório de destino.")
    _add_filter_arguments(profile_save_parser)

    profile_list_parser = profile_subparsers.add_parser(
        "list",
        help="Lista os perfis salvos.",
    )
    profile_list_parser.add_argument(
        "--details",
        action="store_true",
        help="Mostra filtros e caminhos completos para cada perfil.",
    )

    profile_show_parser = profile_subparsers.add_parser(
        "show",
        help="Exibe os detalhes de um perfil salvo.",
    )
    profile_show_parser.add_argument("name", help="Nome do perfil.")

    profile_run_parser = profile_subparsers.add_parser(
        "run",
        help="Executa um perfil salvo.",
    )
    profile_run_parser.add_argument("name", help="Nome do perfil.")
    _add_mode_arguments(profile_run_parser)
    _add_progress_arguments(profile_run_parser)
    profile_run_parser.add_argument(
        "--report",
        help="Caminho opcional para salvar o relatório JSON desta execução.",
    )

    compact_parser = subparsers.add_parser(
        "compact",
        help="Compacta blobs antigos para reduzir o espaço ocupado no estado local.",
    )
    compact_parser.add_argument(
        "--older-than-days",
        type=int,
        help="Compacta somente blobs mais antigos do que a quantidade de dias informada.",
    )
    compact_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria compactado sem alterar os blobs.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    database = StateDatabase(Path(args.state_dir).resolve())
    engine = SyncEngine(database)

    try:
        if args.command == "sync":
            summary = engine.sync(
                source_dir=Path(args.source),
                destination_dir=Path(args.destination),
                filters=_filters_from_args(args),
                dry_run=not args.apply,
                report_path=Path(args.report) if args.report else None,
                progress_callback=_build_progress_callback(args),
            )
            _print_run_summary(summary)
            if args.report:
                print(f"Relatorio salvo em: {Path(args.report).resolve()}")
            return 0

        if args.command == "history":
            database.initialize()
            runs = database.list_runs(limit=args.limit)
            if not runs:
                print("Nenhuma execucao encontrada.")
                return 0

            for run in runs:
                mode = "dry-run" if run["dry_run"] else "live"
                profile_label = f" | perfil: {run['profile_name']}" if run["profile_name"] else ""
                print(
                    f"[{run['id']}] {run['status']} | {mode}{profile_label} | "
                    f"{run['files_scanned']} lidos | {run['files_copied']} copiados | "
                    f"{run['files_updated']} atualizados | {run['files_skipped']} ignorados | "
                    f"{run['started_at']}"
                )
            return 0

        if args.command == "report":
            report_path = engine.write_report(args.run_id, Path(args.output))
            print(f"Relatorio salvo em: {report_path}")
            return 0

        if args.command == "restore":
            restored_path = engine.restore(
                run_id=args.run_id,
                output_dir=Path(args.output),
                overwrite=args.overwrite,
            )
            print(f"Execucao {args.run_id} restaurada em: {restored_path}")
            return 0

        if args.command == "profile":
            return _handle_profile_command(engine, args)

        if args.command == "compact":
            summary = engine.compact_blobs(
                older_than_days=args.older_than_days,
                dry_run=args.dry_run,
            )
            print(
                "\n".join(
                    [
                        f"Blobs analisados: {summary.scanned_blobs}",
                        f"Blobs compactados: {summary.compacted_blobs}",
                        f"Bytes economizados: {summary.saved_bytes}",
                        f"Modo: {'dry-run' if summary.dry_run else 'live'}",
                    ]
                )
            )
            return 0

        parser.print_help()
        return 1
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


def _handle_profile_command(engine: SyncEngine, args: argparse.Namespace) -> int:
    """Centraliza o tratamento dos subcomandos de perfil."""
    if args.profile_command == "save":
        profile = engine.save_profile(
            name=args.name,
            source_dir=Path(args.source),
            destination_dir=Path(args.destination),
            filters=_filters_from_args(args),
        )
        print(f"Perfil salvo: {profile.name}")
        return 0

    if args.profile_command == "list":
        profiles = engine.list_profiles()
        if not profiles:
            print("Nenhum perfil salvo.")
            return 0
        for profile in profiles:
            print(f"- {profile.name}")
            if args.details:
                print(f"  origem: {profile.source_dir}")
                print(f"  destino: {profile.destination_dir}")
                print(f"  filtros: {_format_filters(profile.filters)}")
        return 0

    if args.profile_command == "show":
        profile = engine.get_profile(args.name)
        if profile is None:
            raise ValueError(f"Profile {args.name!r} was not found.")
        print(
            "\n".join(
                [
                    f"Nome: {profile.name}",
                    f"Origem: {profile.source_dir}",
                    f"Destino: {profile.destination_dir}",
                    f"Criado em: {profile.created_at}",
                    f"Filtros: {_format_filters(profile.filters)}",
                ]
            )
        )
        return 0

    if args.profile_command == "run":
        summary = engine.run_profile(
            args.name,
            dry_run=not args.apply,
            report_path=Path(args.report) if args.report else None,
            progress_callback=_build_progress_callback(args),
        )
        _print_run_summary(summary)
        if args.report:
            print(f"Relatorio salvo em: {Path(args.report).resolve()}")
        return 0

    raise ValueError(f"Unsupported profile command: {args.profile_command}")


def _filters_from_args(args: argparse.Namespace) -> SyncFilters:
    return SyncFilters(
        ignore_patterns=list(getattr(args, "ignore", []) or []),
        extensions=list(getattr(args, "ext", []) or []),
        min_size_bytes=getattr(args, "min_size_bytes", None),
        max_size_bytes=getattr(args, "max_size_bytes", None),
        modified_after=getattr(args, "modified_after", None),
        modified_before=getattr(args, "modified_before", None),
    )


def _build_progress_callback(args: argparse.Namespace):
    enabled = _progress_enabled(args)
    if not enabled:
        return None

    def render(update: ProgressUpdate) -> None:
        width = 24
        total = max(update.total, 1)
        filled = int(width * update.current / total)
        bar = "#" * filled + "-" * (width - filled)
        label = update.relative_path
        if len(label) > 48:
            label = f"...{label[-45:]}"
        sys.stderr.write(
            f"\r[{bar}] {update.current}/{total} {update.action:<7} {label:<48}"
        )
        if update.current >= update.total:
            sys.stderr.write("\n")
        sys.stderr.flush()

    return render


def _progress_enabled(args: argparse.Namespace) -> bool:
    if getattr(args, "progress", None) is True:
        return True
    if getattr(args, "progress", None) is False:
        return False
    return sys.stderr.isatty()


def _print_run_summary(summary) -> None:
    print(
        "\n".join(
            [
                f"Execucao: {summary.run_id}",
                f"Status: {summary.status}",
                f"Modo: {'dry-run' if summary.dry_run else 'live'}",
                f"Origem: {summary.source_dir}",
                f"Destino: {summary.destination_dir}",
                (
                    f"Perfil: {summary.profile_name}"
                    if summary.profile_name
                    else "Perfil: execucao avulsa"
                ),
                f"Lidos: {summary.files_scanned}",
                f"Copiados: {summary.files_copied}",
                f"Atualizados: {summary.files_updated}",
                f"Ignorados: {summary.files_skipped}",
                f"Bytes copiados: {summary.bytes_copied}",
            ]
        )
    )


def _format_filters(filters: SyncFilters) -> str:
    parts: list[str] = []
    if filters.ignore_patterns:
        parts.append(f"ignore={filters.ignore_patterns}")
    if filters.extensions:
        parts.append(f"extensoes={filters.extensions}")
    if filters.min_size_bytes is not None:
        parts.append(f"min_size={filters.min_size_bytes}")
    if filters.max_size_bytes is not None:
        parts.append(f"max_size={filters.max_size_bytes}")
    if filters.modified_after:
        parts.append(f"modified_after={filters.modified_after}")
    if filters.modified_before:
        parts.append(f"modified_before={filters.modified_before}")
    return ", ".join(parts) if parts else "sem filtros adicionais"


def _add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Ignora arquivos ou pastas por nome ou padrao glob. Pode repetir.",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=[],
        metavar="EXTENSAO",
        help="Sincroniza apenas arquivos com a extensao informada. Pode repetir.",
    )
    parser.add_argument(
        "--min-size-bytes",
        type=int,
        help="Sincroniza apenas arquivos com tamanho igual ou superior a esse valor.",
    )
    parser.add_argument(
        "--max-size-bytes",
        type=int,
        help="Sincroniza apenas arquivos com tamanho igual ou inferior a esse valor.",
    )
    parser.add_argument(
        "--modified-after",
        help="Sincroniza apenas arquivos modificados depois desta data ISO-8601.",
    )
    parser.add_argument(
        "--modified-before",
        help="Sincroniza apenas arquivos modificados antes desta data ISO-8601.",
    )


def _add_mode_arguments(parser: argparse.ArgumentParser) -> None:
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Grava as alteracoes no disco. Sem esta flag, a execucao e apenas de preview.",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Forca o modo de preview sem gravar arquivos nem blobs versionados.",
    )


def _add_progress_arguments(parser: argparse.ArgumentParser) -> None:
    progress_group = parser.add_mutually_exclusive_group()
    progress_group.add_argument(
        "--progress",
        dest="progress",
        action="store_true",
        default=None,
        help="Forca a exibicao da barra de progresso.",
    )
    progress_group.add_argument(
        "--no-progress",
        dest="progress",
        action="store_false",
        default=None,
        help="Desabilita a barra de progresso.",
    )


if __name__ == "__main__":
    raise SystemExit(main())
