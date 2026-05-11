# Fluxo de dados do `zbx-audit`

Este diagrama mostra **de onde os dados saem**, **como são processados** e **onde ficam armazenados**.

```mermaid
---
config:
  layout: dagre
---
flowchart TB
 subgraph CLI["CLI / Configuração"]
        U["Usuário / cli.py"]
        A["Parser de argumentos\n--group-name --hours --with-ai --env-audit"]
        E[".env / config.py Credenciais Zabbix + PostgreSQL + Ollama"]
  end
 subgraph COLETA["Coleta e Normalização"]
        C["GroupZabbixAnalyzer.collect_all_events analyzer.py"]
        Z["ZabbixCollector zabbix_client.py"]
        ZAPI[("API do Zabbix")]
        N["Normalização EventData + utils.py proxy | severidade | janela | duração"]
        S["store_event analyzer.py"]
  end
 subgraph DB_LAYER["Persistência"]
        DB[("PostgreSQL")]
        T1["events"]
        T2["ollama_response"]
        T3["runbooks (requer pgvector)"]
  end
 subgraph ANALISE["Análise Estatística"]
        M["get_structured_metrics analyzer.py"]
        B["GroupBaseline baseline.py média | desvio | z-score"]
        MA["Métricas estruturadas top problems | proxies | críticos"]
  end
 subgraph AUDITORIA["Auditoria de Ambiente"]
        EA["get_environment_audit analyzer.py"]
  end
 subgraph IA_LAYER["Camada de IA"]
        AI["OllamaAnalyzer ai.py"]
        OLL_GEN[("Ollama generate API")]
        SR["store_ollama_response.analyzer.py"]
  end
 subgraph OUTPUT["Saídas"]
        OUT1["Saída estruturada logs/last_output.json | --output"]
        OUT2["Saída env audit logs/env_audit_last_output.json"]
        OUT3["Resposta IA logs + tabela ollama_response"]
  end
 subgraph VIEW["Visualização"]
        G[("Grafana")]
        D["Datasource PostgreSQL"]
  end
    U --> E
    E --> A
    A -- "Modo de Coleta 
--only-collect" --> C
    A -- "Somente Análise
--only-analize" --> M
    A -- "Indexação runbooks 
--index-runbooks --index-runbook--file" --> RI["RI"]
    A -- "Auditoria do Ambiente Zabbix 
--env-audit" --> EA
    A -- "Ativar analise de IA 
--with-ai / --with-ai-toon" --> AI
    C --> Z
    Z --> ZAPI & N
    ZAPI --> Z
    N --> S
    DB -.-> T1 & T2 & T3
    S --> DB
    RI -- chunk + embedding --> OLL_EMB[("Ollama embeddings API")]
    OLL_EMB --> RI
    RI --> DB
    M --> DB
    DB --> B & MA & D
    B --> MA
    EA --> DB & ZAPI & AI & OUT2
    MA --> AI & OUT1
    DB -- runbook_context / RAG --> AI
    AI --> OLL_GEN & SR & OUT3
    OLL_GEN --> AI
    SR --> DB
    D --> G
```

## Resumo rápido

- **Origem dos dados principais:** API do Zabbix (`event.get`, `host.get`, `problem.get`).
- **Configuração de acesso:** arquivo `.env` lido por `config.py`.
- **Processamento:** normalização dos eventos em `EventData`, cálculo de métricas e baseline (`analyzer.py` + `baseline.py`).
- **Armazenamento principal:** PostgreSQL nas tabelas `events`, `ollama_response` e `runbooks`.
- **IA (opcional):** `ai.py` envia métricas para Ollama e grava o retorno em `ollama_response`.
- **Saídas finais:** arquivos em `logs/` e/ou arquivo passado em `--output` (JSON ou TOON).
