#!/usr/bin/env python3
"""
Script de exemplo para consultar eventos com duração (recovery events)
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.db import get_db_connection

def format_duration(seconds):
    """Formata duração em formato legível"""
    if seconds is None:
        return "N/A"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    
    return " ".join(parts)

def query_recovery_events(hours_back=24, limit=20):
    """Consulta eventos de recovery com suas durações"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    since_timestamp = int((datetime.now() - timedelta(hours=hours_back)).timestamp())
    
    print(f"\n📊 Eventos de RECOVERY com duração (últimas {hours_back}h)\n")
    print("=" * 100)
    
    cursor.execute("""
        SELECT 
            event_id,
            to_timestamp(timestamp) as recovery_time,
            host_name,
            problem_name,
            event_duration,
            severity,
            proxy_name
        FROM events 
        WHERE timestamp > %s 
        AND event_status = 'Recovery'
        AND event_duration IS NOT NULL
        ORDER BY event_duration DESC
        LIMIT %s
    """, (since_timestamp, limit))
    
    results = cursor.fetchall()
    
    if not results:
        print("⚠️ Nenhum evento de recovery encontrado no período")
        return
    
    print(f"{'Event ID':<15} {'Recovery Time':<20} {'Duration':<15} {'Host':<30} {'Problem':<40}")
    print("-" * 100)
    
    for row in results:
        event_id, recovery_time, host_name, problem_name, duration, severity, proxy = row
        duration_str = format_duration(duration)
        
        print(f"{event_id:<15} {recovery_time.strftime('%Y-%m-%d %H:%M'):<20} {duration_str:<15} {host_name[:28]:<30} {problem_name[:38]:<40}")
    
    print("\n" + "=" * 100)
    
    # Estatísticas
    cursor.execute("""
        SELECT 
            COUNT(*) as total_recoveries,
            AVG(event_duration) as avg_duration,
            MIN(event_duration) as min_duration,
            MAX(event_duration) as max_duration
        FROM events 
        WHERE timestamp > %s 
        AND event_status = 'Recovery'
        AND event_duration IS NOT NULL
    """, (since_timestamp,))
    
    stats = cursor.fetchone()
    if stats and stats[0] > 0:
        print(f"\n📈 Estatísticas:")
        print(f"   Total de recoveries: {stats[0]}")
        print(f"   Duração média: {format_duration(int(stats[1]))}")
        print(f"   Duração mínima: {format_duration(stats[2])}")
        print(f"   Duração máxima: {format_duration(stats[3])}")
    
    # Top hosts com mais recoveries
    print(f"\n🏆 Top 5 hosts com mais recoveries:")
    cursor.execute("""
        SELECT 
            host_name,
            COUNT(*) as recovery_count,
            AVG(event_duration) as avg_duration
        FROM events 
        WHERE timestamp > %s 
        AND event_status = 'Recovery'
        AND event_duration IS NOT NULL
        GROUP BY host_name
        ORDER BY recovery_count DESC
        LIMIT 5
    """, (since_timestamp,))
    
    top_hosts = cursor.fetchall()
    for host, count, avg_dur in top_hosts:
        print(f"   {host}: {count} recoveries (média: {format_duration(int(avg_dur))})")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Consultar eventos de recovery com duração")
    parser.add_argument('--hours', type=int, default=24, help='Horas para buscar (padrão: 24)')
    parser.add_argument('--limit', type=int, default=20, help='Limite de resultados (padrão: 20)')
    
    args = parser.parse_args()
    
    try:
        query_recovery_events(args.hours, args.limit)
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
