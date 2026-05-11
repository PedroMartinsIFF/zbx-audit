#!/usr/bin/env python3
import sys
from pathlib import Path
import argparse
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple

# Adicionar diretório atual ao path para imports relativos
sys.path.insert(0, str(Path(__file__).parent))

from shared.config import OLLAMA_MODEL, OLLAMA_API_URL, DB_NAME, get_optional_env
from analyzer.analyzer import GroupZabbixAnalyzer, ENV_AUDIT_GROUP
from shared.models import EventData
from ai.runbook_indexer import RunbookIndexer
from shared.utils import convert_json_to_toon, extract_proxy_name, is_control_group_event, get_correlation_window
from shared.logger_config import LoggerSetup
from integrations.notification_hub import NotificationHubClient

logger = LoggerSetup.get_logger(__name__)

NOTIFICATION_HUB_DEFAULT_URL = get_optional_env(
    "NOTIFICATION_HUB_URL",
    "https://api.notificationhub.globoi.com/statistics",
)
NOTIFICATION_HUB_DEFAULT_TOKEN = get_optional_env("NOTIFICATION_HUB_TOKEN", "")


def _resolve_notification_hub_period(args) -> Tuple[str, str]:
    if args.notification_hub_start_date and args.notification_hub_end_date:
        return args.notification_hub_start_date, args.notification_hub_end_date

    now = datetime.now()
    start = (now - timedelta(hours=args.hours)).strftime('%Y-%m-%d')
    end = now.strftime('%Y-%m-%d')
    return start, end


def _attach_notification_hub_data(metrics: Dict[str, Any], group_name: str, args) -> None:
    team_name = args.notification_hub_team or ""
    start_date, end_date = _resolve_notification_hub_period(args)
    client = NotificationHubClient(
        base_url=args.notification_hub_url,
        bearer_token=args.notification_hub_token,
    )

    logger.info(
        "🌐 Coletando Notification Hub (time='%s', período=%s até %s)",
        team_name or "[todos]",
        start_date,
        end_date,
    )

    try:
        payload = client.fetch_statistics(
            start_date=start_date,
            end_date=end_date,
            team=team_name,
        )
        metrics.setdefault('external_sources', {})['notification_hub'] = {
            'requested': {
                'start_date': start_date,
                'end_date': end_date,
                'team': team_name,
                'url': args.notification_hub_url,
            },
            'response': payload,
        }
        logger.info("✅ Notification Hub integrado ao output")
    except Exception as exc:
        logger.warning("⚠️ Falha ao coletar Notification Hub: %s", exc)
        metrics.setdefault('external_sources', {})['notification_hub'] = {
            'requested': {
                'start_date': start_date,
                'end_date': end_date,
                'team': team_name,
                'url': args.notification_hub_url,
            },
            'error': str(exc),
        }


def _collect_group_events_with_fallback(analyzer: GroupZabbixAnalyzer, group_name: str, hours: int) -> int:
    """Fallback de coleta por grupo quando a coleta global falha."""
    logger.warning("🔄 Fallback: iniciando coleta por grupo '%s'", group_name)

    events = []
    stored_count = 0
    try:
        analyzer.collector.connect()
        events = analyzer.collector.get_events_batch(group_name, hours)
        if not events:
            logger.warning("⚠️ Fallback sem eventos para '%s'", group_name)
            return 0

        analyzer.collector.preload_host_cache(events)
        logger.info("💾 Fallback processando %s eventos...", len(events))

        for i, event in enumerate(events):
            try:
                hosts = event.get('hosts', [])
                if not hosts:
                    continue

                host_info = analyzer.collector.get_host_info(hosts[0]['hostid'])
                if not host_info:
                    continue

                hostgroups = [hg['name'] for hg in host_info.get('hostgroups', [])]
                event_timestamp = datetime.fromtimestamp(int(event['clock']))
                event_value = int(event.get('value', 1))
                event_duration = None
                r_eventid = event.get('r_eventid', '0')

                if event_value == 0 and r_eventid != '0':
                    problem_timestamp = analyzer.collector.get_problem_event_timestamp(r_eventid)
                    if problem_timestamp:
                        event_duration = int(event['clock']) - problem_timestamp

                event_data = EventData(
                    event_id=event['eventid'],
                    timestamp=event_timestamp,
                    host_name=host_info.get('host', 'Desconhecido'),
                    hostgroups=hostgroups,
                    proxy_name=extract_proxy_name(hostgroups),
                    severity=int(event['severity']),
                    problem_name=event.get('name', 'Problema sem nome'),
                    is_control_group=is_control_group_event(hostgroups),
                    correlation_window=get_correlation_window(event_timestamp),
                    event_value=event_value,
                    r_eventid=r_eventid if r_eventid != '0' else None,
                    event_duration=event_duration,
                )
                analyzer.store_event(event_data)
                stored_count += 1
            except Exception as e:
                logger.debug("Erro no fallback ao processar evento: %s", e)
                continue

            if i % 500 == 0 and i > 0:
                logger.info("   📊 Fallback progresso: %.1f%%", (i / len(events)) * 100)

        return stored_count
    finally:
        try:
            analyzer.collector.disconnect()
        except Exception:
            pass


def process_single_group(group_name: str, args):
    """Processa coleta global e/ou análise por grupo"""
    try:
        analyzer = GroupZabbixAnalyzer()

        if args.index_runbooks or args.index_runbook_file:
            indexer = RunbookIndexer(OLLAMA_API_URL)
            docs_dir = args.docs_dir or str(Path(__file__).parent / "docs")

            if args.index_runbook_file:
                inserted, updated = indexer.index_markdown_file(
                    args.index_runbook_file,
                    group_name=args.runbook_group,
                )
                logger.info(
                    "✅ Indexação de runbook concluída (arquivo): inseridos=%s atualizados=%s",
                    inserted,
                    updated,
                )
            else:
                processed, inserted, updated = indexer.index_markdown_directory(
                    docs_dir,
                    group_name=args.runbook_group,
                )
                logger.info(
                    "✅ Indexação de runbooks concluída (diretório): arquivos=%s inseridos=%s atualizados=%s",
                    processed,
                    inserted,
                    updated,
                )

            if not (args.with_ai or args.with_ai_toon or args.only_collect or args.only_analyze):
                logger.info("ℹ️ Apenas indexação executada. Use --with-ai ou --only-analyze para analisar em seguida.")
                return

        # Mostrar período exato que será coletado
        if not args.show_stats and not args.clear_db:
            now = datetime.now()
            time_from = now - timedelta(hours=args.hours)
            logger.info(f"   - Período exato: {time_from.strftime('%Y-%m-%d %H:%M:%S')} até {now.strftime('%Y-%m-%d %H:%M:%S')}")

        # MODO: Mostrar estatísticas
        if args.show_stats:
            logger.info("📊 Estatísticas do Banco de Dados")
            logger.info("="*50)
            stats = analyzer.get_database_stats()
            
            if 'error' in stats:
                logger.error(f"❌ Erro: {stats['error']}")
                return
            
            logger.info(f"📈 Total de eventos: {stats['total_events']:,}")
            logger.info(f"📅 Período dos dados: {stats['date_range']}")
            logger.info(f"🖥️  Hosts únicos: {stats['hosts_count']:,}")
            logger.info(f"🔌 Proxies únicos: {stats['proxies_count']:,}")
            
            if stats['total_events'] > 0:
                logger.info(f"💡 Dicas:")
                events_per_day = stats['total_events'] / max(1, (args.baseline_days if hasattr(args, 'baseline_days') and args.baseline_days else 30))
                logger.info(f"   - Taxa média: ~{events_per_day:.0f} eventos/dia")
                if stats['total_events'] < 1000:
                    logger.info(f"   - Colete mais dados históricos para melhor baseline")
                elif stats['total_events'] > 100000:
                    logger.info(f"   - Banco bem populado - baseline será preciso!")
            
            return
        
        # MODO: Limpar banco de dados
        if args.clear_db:
            logger.info("🗑️  Limpeza do Banco de Dados")
            logger.info("="*50)
            
            if not args.force_clear:
                stats = analyzer.get_database_stats()
                if 'error' not in stats and stats['total_events'] > 0:
                    logger.warning(f"⚠️  ATENÇÃO: Você vai DELETAR {stats['total_events']:,} eventos!")
                    logger.info(f"📅 Período: {stats['date_range']}")
                
                confirm = input("❓ Tem certeza que quer continuar? Digite 'CONFIRMAR' para prosseguir: ")
                if confirm != 'CONFIRMAR':
                    logger.info("❌ Operação cancelada pelo usuário")
                    return
            
            result = analyzer.clear_database()
            if 'error' in result:
                logger.error(f"❌ Erro ao limpar: {result['error']}")
            else:
                logger.info(f"✅ Banco de dados limpo com sucesso! ({result['deleted_count']} eventos deletados)")
            return

        # MODO: Apenas coleta (ignora grupo fornecido)
        if args.only_collect:
            logger.info(f"📥 MODO: APENAS COLETA GLOBAL (parâmetro --only-collect ativo)")
            logger.info(f"   - Coleta: TODOS os eventos do Zabbix")
            logger.info(f"   - Período: {args.hours} horas ({args.hours / 24:.1f} dias)")
            logger.info(f"   - Banco: {DB_NAME}")
            logger.warning(f"⚠️  Parâmetro --group '{group_name}' será IGNORADO (coleta sempre global)")
            
            logger.info(f"\n📥 COLETA GLOBAL")
            total_stored = analyzer.collect_all_events(args.hours)
            logger.info(f"✅ Coleta concluída: {total_stored} eventos armazenados no banco")
            return

        if args.ai_history:
            logger.debug(f"📄 HISTÓRICO IA: Grupo '{group_name}'")
            history = analyzer.get_ollama_history(group_name, args.ai_history_limit)
            if not history:
                logger.warning(f"⚠️ Nenhuma resposta da IA encontrada para o grupo '{group_name}'")
                return
            for i, entry in enumerate(history, 1):
                logger.info(f"🕵️ **Análise {i}** - {entry['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{entry['response']}")
            return

        logger.info(f"Iniciando análise do grupo: '{group_name}'")

        logger.info(f"🌍 COLETA GLOBAL + ANÁLISE POR GRUPO")
        logger.info(f"   - Grupo para Análise: '{group_name}'")
        logger.info(f"   - Coleta: TODOS os eventos do Zabbix")
        logger.info(f"   - Período: {args.hours} horas ({args.hours / 24:.1f} dias)")
        logger.info(f"   - Modelo IA: {args.model or OLLAMA_MODEL}")
        logger.info(f"   - Banco: {DB_NAME}")
        logger.debug(f"   - Análise posterior: hostgroups::jsonb ? '{group_name}'")
        
        # Coleta (global por padrão), com fallback por grupo quando necessário
        if args.only_analyze:
            logger.info("⏭️ --only-analyze ativo: pulando fase de coleta")
        else:
            logger.info(f"\n📥 FASE 1: COLETA GLOBAL")
            try:
                total_stored = analyzer.collect_all_events(args.hours)
            except Exception as e:
                logger.warning("⚠️ Falha na coleta global: %s", e)
                total_stored = _collect_group_events_with_fallback(analyzer, group_name, args.hours)

            logger.info(f"✅ Fase 1 concluída: {total_stored} eventos armazenados\n")
        
        if args.format_toon:
            logger.debug(f"   - Formato: TOON (profundidade: {args.toon_depth})")
        if args.output:
            logger.debug(f"   - Arquivo: {args.output}")
        if args.with_ai:
            logger.debug(f"   - IA habilitada: JSON")
        elif args.with_ai_toon:
            logger.debug(f"   - IA habilitada: TOON")
        
        # FASE 2: Análise do grupo especificado (após coleta global)
        logger.info(f"\n📊 FASE 2: ANÁLISE DO GRUPO '{group_name}'")
        metrics = analyzer.get_structured_metrics(
            group_name=group_name,
            hours_back=args.hours,
            include_baseline=not args.no_baseline,
        )
        logger.info(f"📈 Resultados:")
        logger.info(f"   - Eventos: {metrics.get('total_events', 0):,}")
        logger.info(f"   - Críticos: {metrics.get('critical_events', 0):,}")

        if args.with_notification_hub:
            _attach_notification_hub_data(metrics, group_name, args)

        env_audit = None
        env_audit_group = ENV_AUDIT_GROUP if args.env_audit else group_name
        if args.env_audit:
            env_audit = analyzer.get_environment_audit(active_group_name=env_audit_group)

        ai_summary = None
        if args.with_ai or args.with_ai_toon:
            logger.debug("Inicializando análise de IA")
            analyzer.initialize_ai_analyzer(args.model or OLLAMA_MODEL, OLLAMA_API_URL)
            ai_summary = analyzer.run_ai_analysis(
                metrics,
                env_audit_group if args.env_audit else group_name,
                use_toon=args.with_ai_toon,
                toon_depth=args.toon_depth,
                prompt_variant="env-audit" if args.env_audit else "default",
                metrics_env=env_audit,
            )
            if ai_summary:
                ai_type = "TOON" if args.with_ai_toon else "JSON"
                logger.info(f"✅ IA {ai_type} - Grupo '{group_name}' análise concluída")

        if ai_summary:
            metrics['ai_summary'] = ai_summary

        logs_dir = Path(__file__).parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        if args.format_toon:
            toon_output = "\n".join(convert_json_to_toon(metrics, max_depth=args.toon_depth))
            logs_output_file = logs_dir / "last_output.toon"
            with open(logs_output_file, 'w', encoding='utf-8') as f:
                f.write(toon_output)
            logger.info(f"💾 Resultado TOON salvo em logs: {logs_output_file}")
        else:
            logs_output_file = logs_dir / "last_output.json"
            with open(logs_output_file, 'w', encoding='utf-8') as f:
                json.dump(metrics, f, indent=2, default=str, ensure_ascii=False)
            logger.info(f"💾 Resultado JSON salvo em logs: {logs_output_file}")

        if args.env_audit:
            if args.format_toon:
                env_toon_output = "\n".join(convert_json_to_toon(env_audit, max_depth=args.toon_depth))
                env_logs_file = logs_dir / "env_audit_last_output.toon"
                with open(env_logs_file, 'w', encoding='utf-8') as f:
                    f.write(env_toon_output)
                logger.info(f"💾 Auditoria do ambiente (TOON) salva em logs: {env_logs_file}")
            else:
                env_logs_file = logs_dir / "env_audit_last_output.json"
                with open(env_logs_file, 'w', encoding='utf-8') as f:
                    json.dump(env_audit, f, indent=2, default=str, ensure_ascii=False)
                logger.info(f"💾 Auditoria do ambiente (JSON) salva em logs: {env_logs_file}")

            if args.output:
                base_output = args.output.rsplit('.', 1)[0]
                env_output_file = base_output + ('_env_audit.toon' if args.format_toon else '_env_audit.json')
                if args.format_toon:
                    with open(env_output_file, 'w', encoding='utf-8') as f:
                        f.write(env_toon_output)
                else:
                    with open(env_output_file, 'w', encoding='utf-8') as f:
                        json.dump(env_audit, f, indent=2, default=str, ensure_ascii=False)
                logger.info(f"💾 Auditoria do ambiente salva: {env_output_file}")

        if args.output:
            if args.format_toon:
                toon_output = "\n".join(convert_json_to_toon(metrics, max_depth=args.toon_depth))
                output_file = args.output
                if not output_file.endswith('.toon'):
                    output_file = output_file.rsplit('.', 1)[0] + '.toon'
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(toon_output)
                logger.info(f"💾 Resultados TOON salvos: {output_file}")
            
            if not args.output.endswith('.toon'):
                with open(args.output, 'w', encoding='utf-8') as f:
                    json.dump(metrics, f, indent=2, default=str, ensure_ascii=False)
                logger.info(f"💾 Resultados JSON salvos: {args.output}")
        else:
            logger.info(f"📊 Resumo: {metrics.get('total_events', 0)} eventos coletados, {metrics.get('critical_events', 0)} críticos")
    
    except Exception as e:
        logger.critical(f"❌ Erro na análise do grupo '{group_name}': {e}", exc_info=True)
        raise


def main():
    parser = argparse.ArgumentParser(
        description='Analisador Zabbix por Grupo (modular)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--group-name', type=str, required=True, help='Nome do hostgroup ou lista separada por vírgula (ignorado com --only-collect)')
    parser.add_argument('--hours', type=int, default=24, help='Horas para análise (padrão: 24)')
    parser.add_argument('--model', type=str, help='Modelo Ollama para análise')
    parser.add_argument('--output', type=str, help='Arquivo de saída (opcional)')
    parser.add_argument('--only-collect', action='store_true', help='APENAS COLETAR TODOS os eventos (ignora --group-name, não aceita análise)')
    parser.add_argument('--only-analyze', action='store_true', help='Apenas analisar dados existentes do grupo')
    parser.add_argument('--baseline-days', type=int, default=30, help='Dias para baseline (padrão: 30)')
    parser.add_argument('--no-baseline', action='store_true', help='Desabilitar baseline')
    parser.add_argument('--with-ai', action='store_true', help='Habilitar análise com IA (JSON)')
    parser.add_argument('--with-ai-toon', action='store_true', help='Habilitar análise com IA (TOON)')
    parser.add_argument('--env-audit', action='store_true', help='Gerar auditoria do ambiente em output separado')
    parser.add_argument('--format-toon', action='store_true', help='Converter output para TOON')
    parser.add_argument('--toon-depth', type=int, default=3, help='Profundidade TOON (padrão: 3)')
    parser.add_argument('--ai-history', action='store_true', help='Mostrar histórico de respostas da IA para o grupo')
    parser.add_argument('--ai-history-limit', type=int, default=5, help='Limite do histórico')
    parser.add_argument('--show-stats', action='store_true', help='Mostrar estatísticas do banco de dados')
    parser.add_argument('--clear-db', action='store_true', help='Limpar todos os eventos do banco de dados')
    parser.add_argument('--force-clear', action='store_true', help='Forçar limpeza sem confirmação')
    parser.add_argument('--index-runbooks', action='store_true', help='Indexar todos os arquivos .md para o RAG')
    parser.add_argument('--index-runbook-file', type=str, help='Indexar um único arquivo .md no RAG')
    parser.add_argument('--runbook-group', type=str, help='Escopo de grupo para os runbooks indexados (ex: Zabbix/Servico)')
    parser.add_argument('--docs-dir', type=str, default=str(Path(__file__).parent / "docs"), help='Diretório de documentos .md para indexação')
    parser.add_argument('--with-notification-hub', action='store_true', help='Anexar estatísticas da API Notification Hub no output final')
    parser.add_argument('--notification-hub-team', type=str, help='Time para a API Notification Hub (opcional, padrão: todos os times)')
    parser.add_argument('--notification-hub-start-date', type=str, help='Data inicial para Notification Hub (YYYY-mm-dd)')
    parser.add_argument('--notification-hub-end-date', type=str, help='Data final para Notification Hub (YYYY-mm-dd)')
    parser.add_argument('--notification-hub-url', type=str, default=NOTIFICATION_HUB_DEFAULT_URL, help='Endpoint base da API Notification Hub')
    parser.add_argument('--notification-hub-token', type=str, default=NOTIFICATION_HUB_DEFAULT_TOKEN, help='Bearer token para autenticação no Notification Hub')

    args = parser.parse_args()

    if bool(args.notification_hub_start_date) ^ bool(args.notification_hub_end_date):
        logger.critical("❌ ERRO: informe ambas as datas (--notification-hub-start-date e --notification-hub-end-date) ou nenhuma")
        sys.exit(1)

    if args.with_notification_hub and not args.notification_hub_token:
        logger.critical("❌ ERRO: configure --notification-hub-token ou defina NOTIFICATION_HUB_TOKEN no .env")
        sys.exit(1)
    
    # Validação: --only-collect não funciona com múltiplos grupos
    group_names = [g.strip() for g in args.group_name.split(',')]
    if args.only_collect and len(group_names) > 1:
        logger.critical("❌ ERRO: --only-collect não aceita múltiplos grupos")
        logger.info("   --only-collect coleta SEMPRE TODOS os eventos globalmente")
        logger.info("   Forneça apenas um grupo (será ignorado) ou remova múltiplos grupos")
        sys.exit(1)

    if len(group_names) > 1:
        logger.info("Processando múltiplos grupos")
        total_success = 0
        total_errors = 0
        for idx, group_name in enumerate(group_names, 1):
            logger.info(f"{'='*70}")
            logger.info(f"📊 PROCESSANDO GRUPO {idx}/{len(group_names)}: '{group_name}'")
            logger.info(f"{'='*70}\n")
            try:
                process_single_group(group_name, args)
                total_success += 1
                logger.info(f"✅ Grupo '{group_name}' processado com sucesso!")
            except Exception as e:
                total_errors += 1
                logger.error(f"❌ ERRO ao processar grupo '{group_name}': {e}", exc_info=True)
            if idx < len(group_names):
                time.sleep(2)
        
        logger.info(f"{'='*70}")
        logger.info(f"🏁 RESUMO MULTI-GRUPO")
        logger.info(f"{'='*70}")
        logger.info(f"  Total de grupos: {len(group_names)}")
        logger.info(f"  ✅ Sucessos: {total_success}")
        logger.info(f"  ❌ Erros: {total_errors}")
        logger.info(f"  📊 Taxa de sucesso: {total_success * 100 // len(group_names)}%")
        logger.info(f"{'='*70}\n")
        return

    process_single_group(group_names[0], args)


if __name__ == "__main__":
    main()
