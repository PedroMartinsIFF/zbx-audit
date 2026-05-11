#!/usr/bin/env python3
"""
Script de migração para adicionar colunas event_duration e r_eventid na tabela events
"""
import sys
from pathlib import Path

# Adicionar diretório atual ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.db import get_db_connection

def migrate():
    print("🔄 Iniciando migração do banco de dados...")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verificar se as colunas já existem
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='events' 
            AND column_name IN ('event_duration', 'r_eventid')
        """)
        existing_columns = [row[0] for row in cursor.fetchall()]
        
        # Adicionar event_duration se não existir
        if 'event_duration' not in existing_columns:
            print("➕ Adicionando coluna event_duration...")
            cursor.execute("""
                ALTER TABLE events 
                ADD COLUMN event_duration INTEGER DEFAULT NULL
            """)
            print("✅ Coluna event_duration adicionada")
        else:
            print("ℹ️ Coluna event_duration já existe")
        
        # Adicionar r_eventid se não existir
        if 'r_eventid' not in existing_columns:
            print("➕ Adicionando coluna r_eventid...")
            cursor.execute("""
                ALTER TABLE events 
                ADD COLUMN r_eventid TEXT DEFAULT NULL
            """)
            print("✅ Coluna r_eventid adicionada")
        else:
            print("ℹ️ Coluna r_eventid já existe")
        
        # Criar índices
        print("📊 Criando índices...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_status 
            ON events(event_status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_duration 
            ON events(event_duration) 
            WHERE event_duration IS NOT NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_r_eventid 
            ON events(r_eventid) 
            WHERE r_eventid IS NOT NULL
        """)
        print("✅ Índices criados")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("🎉 Migração concluída com sucesso!")
        print("\n💡 Agora você pode usar o sistema normalmente.")
        print("   Os novos eventos de recovery terão a duração calculada automaticamente.")
        
    except Exception as e:
        print(f"❌ Erro durante a migração: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
