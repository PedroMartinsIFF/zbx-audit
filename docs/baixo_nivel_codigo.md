# Documentacao de Baixo Nivel do Codigo

## 1. Visao Geral

Este projeto implementa um pipeline completo para ingestao, persistencia, analise estatistica e analise assistida por IA de eventos do Zabbix. O fluxo principal tem duas entradas:

- [cli.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/cli.py): execucao batch e operacional.
- [app.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/app.py): interface Streamlit para consulta, execucao e debug.

As duas entradas convergem na mesma camada de orquestracao:

- [analyzer/analyzer.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/analyzer/analyzer.py)

Essa camada central se relaciona com:

- `zabbix/`: coleta dados da API do Zabbix.
- `db/`: persiste eventos, respostas da IA e executa consultas analiticas.
- `analyzer/`: calcula baseline, auditoria de ambiente e relatorios deterministico + IA.
- `ai/`: encapsula chamadas ao Ollama, RAG e montagem dos prompts.
- `shared/`: configuracao, logging, modelos e utilitarios.

O projeto nao usa a IA como fonte primaria da logica. A abordagem atual separa:

- partes deterministicas, construidas no codigo;
- partes interpretativas, geradas pela IA com prompts reduzidos;
- montagem final do relatorio no backend.

Essa divisao reduz alucinacao e faz com que o texto final dependa de dados estruturados ja validados.

## 2. Fluxo de Execucao End-to-End

### 2.1 Entrada via CLI

Arquivo principal:

- [cli.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/cli.py)

Funcoes principais:

- `_collect_group_events_with_fallback(...)`
- `process_single_group(...)`
- `main()`

Fluxo:

1. `main()` faz o parse dos argumentos.
2. `process_single_group()` instancia `GroupZabbixAnalyzer`.
3. Dependendo das flags, o processo entra em um dos modos:
   - indexacao de runbooks;
   - somente coleta;
   - somente analise;
   - coleta + analise;
   - consulta de historico da IA;
   - estatisticas do banco;
   - limpeza do banco.
4. Quando ha coleta:
   - a coleta global tenta trazer todos os eventos do Zabbix;
   - se houver falha no fluxo principal, `_collect_group_events_with_fallback()` faz coleta por grupo.
5. Os eventos coletados sao convertidos em `EventData` e enviados para persistencia.
6. Depois da coleta, o codigo chama a analise estruturada e, opcionalmente, a analise por IA.

### 2.2 Entrada via Streamlit

Arquivo principal:

- [app.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/app.py)

Ponto central:

- `get_analyzer()` usa `st.cache_resource` para compartilhar uma unica instancia de `GroupZabbixAnalyzer`.

Funcao da camada web:

- iniciar rotinas de analise;
- mostrar metricas estruturadas;
- mostrar historico da IA;
- renderizar graficos e tabelas;
- testar prompts manualmente no modo Stats & Debug.

O Streamlit nao replica a logica analitica. Ele consome o analyzer como servico e se concentra em:

- orquestracao de entrada do usuario;
- leitura do `session_state`;
- transformacao de saida em UI.

## 3. Pasta `shared/`

Arquivos:

- [shared/config.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/shared/config.py)
- [shared/logger_config.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/shared/logger_config.py)
- [shared/models.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/shared/models.py)
- [shared/types_contracts.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/shared/types_contracts.py)
- [shared/utils.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/shared/utils.py)

### 3.1 `config.py`

Responsabilidade:

- carregar `.env`;
- validar variaveis obrigatorias;
- expor configuracoes globais do projeto.

Pontos internos relevantes:

- `ConfigError`: excecao de configuracao.
- `get_required_env(key)`: aborta a inicializacao se a variavel nao existir.
- `get_optional_env(key, default)`: le variavel opcional com fallback.
- `reload_env_file()`: relê o `.env`.
- `get_runtime_ollama_settings()`: retorna `model`, `api_url` e `timeout` atualizados do Ollama.

Relacao com o resto do sistema:

- praticamente todos os modulos dependem deste arquivo;
- ele e importado cedo, entao erro de configuracao costuma falhar a inicializacao inteira.

### 3.2 `logger_config.py`

Responsabilidade:

- criar loggers padronizados;
- configurar console, arquivo rotativo e arquivo de erro.

Ponto central:

- `LoggerSetup.get_logger(name, log_level)`

Efeitos no sistema:

- cada modulo chama `LoggerSetup.get_logger(__name__)`;
- a configuracao atual grava logs em `logs/` na raiz do projeto.

### 3.3 `models.py`

Responsabilidade:

- definir dataclasses de dominio.

Modelos principais:

- `EventData`: representa um evento Zabbix normalizado e enriquecido;
- `BaselineMetrics`: baseline historico do grupo;
- `AnomalyDetection`: resultado da comparacao entre janela atual e baseline.

Essas dataclasses circulam principalmente entre:

- `cli.py`
- `analyzer/analyzer.py`
- `db/event_repository.py`
- `analyzer/baseline.py`

### 3.4 `types_contracts.py`

Responsabilidade:

- definir `TypedDicts` de contratos estruturados.

Tipos importantes:

- `StructuredMetrics`
- `EnvironmentAudit`
- `ProxyAnalysis`
- `BaselineAnalysis`

Esses contratos tornam explicito o shape esperado de dicionarios trocados entre:

- analyzer;
- relatorios;
- camada web;
- persistencia de snapshots.

### 3.5 `utils.py`

Responsabilidade:

- utilitarios simples e transversais.

Funcoes principais:

- `convert_json_to_toon(...)`: transforma JSON em representacao textual simplificada.
- `extract_proxy_name(hostgroups)`: extrai proxy a partir dos hostgroups.
- `is_control_group_event(hostgroups)`: identifica eventos de grupo de controle.
- `get_correlation_window(timestamp)`: normaliza timestamp em janelas de correlacao.

Uso:

- chamado no CLI para enriquecer eventos antes de salvar;
- usado pela IA quando a saida precisa ser convertida em TOON.

## 4. Pasta `zabbix/`

Arquivo:

- [zabbix/zabbix_client.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/zabbix/zabbix_client.py)

Classe principal:

- `ZabbixCollector`

Responsabilidade:

- encapsular a comunicacao com a API do Zabbix.

Metodos relevantes:

- `connect()`: autentica na API.
- `disconnect()`: encerra a sessao.
- `get_events_batch(group_name, hours_back)`: coleta eventos de um grupo.
- `get_all_events(hours_back)`: coleta global.
- `get_host_info(hostid)`: busca metadados do host.
- `preload_host_cache(events)`: reduz round-trips para hosts repetidos.
- `get_problem_event_timestamp(problem_eventid)`: usado para calcular duracao em recoveries.

Relacao com o sistema:

- o analyzer usa essa classe como fonte de verdade para ingestao;
- o CLI usa o collector indiretamente via analyzer;
- o cache de hosts evita repetir chamadas de host para eventos ja correlacionados.

## 5. Pasta `db/`

Arquivos:

- [db/db.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/db/db.py)
- [db/event_repository.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/db/event_repository.py)
- [db/migrate_add_duration.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/db/migrate_add_duration.py)
- [db/query_recovery_durations.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/db/query_recovery_durations.py)

### 5.1 `db.py`

Responsabilidade:

- abrir e manter o pool de conexoes;
- criar schema e indices da aplicacao.

Funcoes principais:

- `init_connection_pool()`
- `get_db_connection()`
- `close_connection_pool()`
- `setup_database()`

`setup_database()` garante:

- tabela `events`
- tabela `ollama_response`
- tabela `runbooks` quando `pgvector` esta disponivel
- colunas adicionais e indices para performance

Relacao com o sistema:

- e o ponto de entrada de persistencia para praticamente todos os modulos;
- a inicializacao acontece cedo dentro de `GroupZabbixAnalyzer.__init__()`.

### 5.2 `event_repository.py`

Classe principal:

- `EventRepository`

Responsabilidade:

- encapsular leitura e escrita mais semanticas no banco.

Metodos relevantes:

- `upsert_event(...)`: grava evento normalizado em `events`.
- `insert_ollama_response(...)`: persiste resposta da IA.
- `fetch_latest_ollama_response(...)`: busca ultima resposta salva de um grupo.
- `fetch_group_metrics_bundle(...)`: consulta grande que consolida metricas do grupo.

Importancia:

- essa classe desacopla o analyzer do SQL bruto em operacoes mais frequentes.

### 5.3 Scripts auxiliares

`migrate_add_duration.py`:

- faz alteracoes para suportar `event_duration`.

`query_recovery_durations.py`:

- consulta recoveries e duracoes para diagnostico.

Eles nao participam do fluxo normal de runtime da app.

## 6. Pasta `analyzer/`

Arquivos:

- [analyzer/analyzer.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/analyzer/analyzer.py)
- [analyzer/baseline.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/analyzer/baseline.py)
- [analyzer/env_audit_report.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/analyzer/env_audit_report.py)
- [analyzer/group_metrics_report.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/analyzer/group_metrics_report.py)

### 6.1 `analyzer.py`

Classe central:

- `GroupZabbixAnalyzer`

Esse e o nucleo do sistema. Ele conecta:

- coleta Zabbix;
- persistencia;
- baseline estatistico;
- IA;
- historico de execucao;
- montagem de saidas consumidas por CLI e UI.

#### Dependencias internas

No `__init__()`:

- inicializa banco com `setup_database()` e `init_connection_pool()`;
- cria `ZabbixCollector`;
- cria `GroupBaseline`;
- cria `EventRepository`;
- mantem referencias lazy para analisadores IA.

#### Responsabilidades praticas

1. Coleta e persistencia
   - `collect_all_events(...)`
   - `store_event(...)`

2. Metricas estruturadas do grupo
   - `get_structured_metrics(...)`

3. Auditoria de ambiente
   - `get_environment_audit()`

4. Historico e comparacao entre execucoes
   - `get_ollama_history(...)`
   - `_extract_ai_history_metadata(...)`
   - `_build_metrics_snapshot(...)`
   - `_build_execution_comparison(...)`

5. Integracao com IA
   - `_ensure_ai_analyzer_model(...)`
   - `run_group_prompt_inject(...)`
   - `run_env_prompt_inject(...)`

#### Como os dados circulam aqui

Fluxo de grupo:

1. analisa eventos do banco;
2. calcula baseline;
3. monta `StructuredMetrics`;
4. se IA estiver ativa, envia o pacote consolidado para `StandardOllamaAnalyzer`;
5. recebe o relatorio final;
6. salva a resposta e um snapshot das metricas em `ollama_response`.

Fluxo de ambiente:

1. consulta metricas agregadas de 24h e baseline 30d;
2. monta estrutura `EnvironmentAudit`;
3. se IA estiver ativa, envia para `OllamaAnalyzer`;
4. recebe relatorio final composto;
5. salva no historico.

### 6.2 `baseline.py`

Classe principal:

- `GroupBaseline`

Responsabilidade:

- transformar historico bruto do grupo em referencia estatistica.

Metodos principais:

- `calculate_baseline(group_name, days_back=30)`
- `detect_anomalies(current_metrics, baseline, group_name)`

Calculos internos:

- media de eventos por hora;
- desvio padrao por hora;
- taxa media de eventos criticos;
- distribuicao de carga por proxy;
- identificacao de novos problemas;
- identificacao de proxies fora da distribuicao esperada;
- score sintetico de anomalia.

Esse modulo nao fala com IA. Ele produz insumo estruturado para o analyzer.

### 6.3 `env_audit_report.py`

Classe principal:

- `EnvAuditReportBuilder`

Responsabilidade:

- gerar as partes deterministicas do relatorio de ambiente;
- selecionar candidatos para a parte interpretativa da IA;
- montar prompts reduzidos do `env-audit`.

Partes deterministicas:

- secao 1: estado geral do ambiente;
- secao 2.2: proxies ativos na ultima hora;
- secao 3: eventos criticos ativos;
- montagem final do texto.

Partes orientadas a IA:

- prompt da secao `2.1`
- prompt da secao `4 + resumo`

Pontos importantes:

- `build_proxy_candidates(...)` nao usa IA; escolhe proxies relevantes antes do prompt.
- `extract_actions_summary(...)` torna a extracao da resposta da IA mais tolerante a pequenas variacoes de cabecalho.

### 6.4 `group_metrics_report.py`

Classe principal:

- `GroupMetricsReportBuilder`

Responsabilidade:

- fazer para `metricas por grupo` o mesmo papel que `EnvAuditReportBuilder` faz para `env-audit`.

Partes deterministicas:

- secao 1 do grupo;
- secao 2.2 de evidencias objetivas;
- selecao de candidatos.

Partes por IA:

- secao `2.1 Interpretação dos Desvios`
- secao `3 Ações Recomendadas`
- resumo executivo

Estado atual do desenho:

- a analise por grupo esta mais host-centric;
- hosts viram a entidade principal do texto;
- problemas entram como contexto associado ao host.

## 7. Pasta `ai/`

Arquivos:

- [ai/ai.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/ai/ai.py)
- [ai/ai_standard.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/ai/ai_standard.py)
- [ai/ollama_client.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/ai/ollama_client.py)
- [ai/rag_support.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/ai/rag_support.py)
- [ai/runbook_indexer.py](/Users/pedromartins/Documents/Trabalho/zbx-audit/ai/runbook_indexer.py)

### 7.1 `ollama_client.py`

Classe principal:

- `OllamaClient`

Responsabilidade:

- encapsular as chamadas HTTP para o Ollama;
- lidar com timeout, payload e retorno bruto.

Esse e o nivel mais baixo da integracao com IA.

### 7.2 `rag_support.py`

Classe principal:

- `RagSupport`

Responsabilidade:

- recuperar contexto de runbook;
- selecionar match deterministico entre problema observado e runbook indexado.

Papeis praticos:

- montar consulta de similaridade;
- chamar embeddings;
- consultar a tabela `runbooks`;
- escolher o melhor trecho aplicavel;
- extrair `problem/pattern/procedure` quando o KB segue um formato reconhecido.

O RAG nao monta o relatorio. Ele so injeta contexto para a camada de prompt.

### 7.3 `runbook_indexer.py`

Classe principal:

- `RunbookIndexer`

Responsabilidade:

- quebrar markdown em chunks;
- gerar embeddings;
- gravar chunks na tabela `runbooks`.

Metodos principais:

- `_split_markdown_into_chunks(...)`
- `_embed_text(...)`
- `index_markdown_file(...)`
- `index_markdown_directory(...)`

Esse modulo e usado principalmente pelo CLI quando se deseja indexar KBs.

### 7.4 `ai.py`

Classe principal:

- `OllamaAnalyzer`

Responsabilidade:

- executar a analise IA do `env-audit`.

Pontos internos importantes:

- `self.ollama_client`: cliente bruto do Ollama.
- `self.rag_support`: apoio para RAG.
- `self.env_audit_report`: builder do relatorio do ambiente.

Responsabilidades concretas:

- montar query para runbook;
- buscar contexto relevante;
- gerar prompts reduzidos para `2.1` e `4 + resumo`;
- concatenar prompts para historico;
- salvar `last_ai_prompt.txt`;
- receber os blocos gerados pela IA e montar o relatorio final.

Historicamente esse arquivo teve logica monolitica. Hoje ele atua mais como orquestrador do fluxo fracionado.

### 7.5 `ai_standard.py`

Classe principal:

- `StandardOllamaAnalyzer(OllamaAnalyzer)`

Responsabilidade:

- especializar a analise de metricas por grupo.

Diferenca para `OllamaAnalyzer`:

- usa `GroupMetricsReportBuilder`;
- trata o contexto do grupo como entidade principal;
- mantem a mesma infraestrutura base de Ollama e RAG.

No fluxo de grupo:

1. opcionalmente busca runbook;
2. tenta resolver match deterministico;
3. monta prompts reduzidos de grupo;
4. registra o prompt final;
5. retorna o relatorio final combinado.

## 8. `app.py`: funcionamento interno da interface

`app.py` e um arquivo grande, mas a responsabilidade dele e bem clara: UI.

### 8.1 Estrutura principal

O arquivo:

- configura a pagina do Streamlit;
- cria tabs;
- dispara chamadas ao analyzer;
- renderiza tabelas, cards, graficos e blocos de texto.

### 8.2 Blocos importantes

- `get_analyzer()`: singleton do analyzer na sessao.
- `_fetch_dashboard_data(...)`: consulta dados do banco para graficos 24h.
- `_render_dashboard_sections(...)`: renderizacao consolidada do dashboard.
- `_render_env_overview_panels(...)`: cards deterministas do ambiente.
- `_render_env_last_hour_chart(...)`: grafico da secao 2.2 do ambiente.
- `_render_env_events_table(...)`: tabela estruturada dos eventos do ambiente.
- `_render_env_actions_and_summary(...)`: reapresenta o texto do relatorio IA salvo.
- `_render_group_ai_report(...)`: mostra o relatorio de grupo estruturado por secoes.
- `_render_group_overview_panels(...)`: cards do grupo.
- `_render_group_proxy_table(...)`: proxies do grupo.
- `_render_group_critical_events_table(...)`: tabela de eventos criticos do grupo.

### 8.3 Papel arquitetural

O `app.py` nao deveria conter regra pesada de negocio. No estado atual:

- a maior parte da regra esta fora dele;
- ele monta visualizacao a partir de dados estruturados do analyzer;
- ele ainda concentra bastante codigo de renderizacao, mas nao e a fonte de verdade da analise.

## 9. Relacao entre as Pastas

### 9.1 Dependencias principais

Ordem tipica de dependencia:

1. `shared`
2. `db` e `zabbix`
3. `analyzer`
4. `ai`
5. `app.py` e `cli.py`

Em termos praticos:

- `shared` e consumido por quase todos os outros modulos;
- `db` e `zabbix` sao camadas de integracao;
- `analyzer` usa ambas e organiza o dominio;
- `ai` depende de `analyzer` para os builders e de `db` para RAG/historico;
- `app.py` e `cli.py` usam `analyzer` como ponto central.

### 9.2 Fluxo de dados

**Zabbix -> DB**

- `zabbix.zabbix_client.ZabbixCollector`
- `cli.py`
- `analyzer.GroupZabbixAnalyzer.store_event()`
- `db.event_repository.EventRepository.upsert_event()`

**DB -> Metricas**

- `db.event_repository.fetch_group_metrics_bundle()`
- consultas diretas em `analyzer/analyzer.py`
- baseline em `analyzer/baseline.py`

**Metricas -> Relatorio**

- grupo:
  - `GroupZabbixAnalyzer`
  - `GroupMetricsReportBuilder`
  - `StandardOllamaAnalyzer`
- ambiente:
  - `GroupZabbixAnalyzer`
  - `EnvAuditReportBuilder`
  - `OllamaAnalyzer`

**Relatorio -> Persistencia**

- `EventRepository.insert_ollama_response()`

**Persistencia -> UI**

- `app.py` consome historico e metricas para reconstruir:
  - cards;
  - graficos;
  - tabelas;
  - blocos narrativos.

## 10. Como o Sistema se Divide entre Deterministico e IA

### 10.1 Grupo

Deterministico:

- secao 1;
- secao 2.2;
- selecao de hosts candidatos;
- calculo de baseline e anomalia;
- persistencia;
- comparacao entre execucoes.

IA:

- secao 2.1;
- secao 3;
- resumo executivo.

### 10.2 Ambiente

Deterministico:

- secao 1;
- secao 2.2;
- secao 3;
- selecao de proxies candidatos;
- deteccao de eventos ativos correlacionados;
- preparacao do contexto RAG.

IA:

- secao 2.1;
- secao 4;
- resumo executivo.

Essa separacao e central para entender o projeto. O objetivo do codigo nao e pedir para a IA "resolver tudo", e sim:

- reduzir o espaco de decisao da IA;
- cercar a resposta com dados ja estruturados;
- usar o modelo como camada de interpretacao controlada.

## 11. Riscos Tecnicos e Pontos de Atencao

### 11.1 `sys.path.insert(...)`

Alguns modulos ainda inserem a raiz manualmente no `sys.path`. Isso funciona, mas e um sinal de acoplamento estrutural. Se a organizacao de pacotes continuar evoluindo, esse ponto tende a ser uma das proximas limpezas.

### 11.2 `app.py` grande

Mesmo com a logica analitica fora dele, o arquivo ainda concentra muita renderizacao. O risco aqui e manutencao de UI, nao regra de negocio.

### 11.3 Dependencia de formato no historico da IA

Parte da comparacao entre execucoes depende de extrair elementos textuais da resposta da IA. O codigo atual ja esta mais tolerante, mas ainda existe dependencia parcial de formato.

### 11.4 Persistencia de logs e paths absolutos

Configuracao de logs e deploy sao sensiveis a permissao de escrita no servidor. Erros recentes de permissao mostram que esse aspecto operacional ainda precisa de consistencia entre:

- caminho do projeto;
- usuario do service;
- diretorio de logs.

## 12. Resumo Final

Em baixo nivel, o codigo funciona como uma pipeline modular:

1. coleta eventos do Zabbix;
2. normaliza e persiste no PostgreSQL;
3. consolida metricas e baseline;
4. separa partes deterministicas e partes interpretativas;
5. usa IA apenas nas secoes onde linguagem e julgamento agregam valor;
6. persiste historico de saida e snapshots;
7. reapresenta tudo por CLI e pela interface Streamlit.

As pastas refletem bem essa divisao:

- `shared`: fundacao tecnica;
- `zabbix`: origem externa dos dados;
- `db`: persistencia e consultas;
- `analyzer`: regra de negocio e montagem estruturada;
- `ai`: interpretacao assistida por LLM e RAG;
- `app.py` e `cli.py`: superficies de uso.
