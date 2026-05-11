# KB – Ação em Caso de Alarme (Threshold de Serviço)

## Sumário
1. Contexto Geral
2. Fila do Zabbix Server (Queue)
3. Zabbix Server & Proxy – Alertas Comuns
4. Zabbix Server – Alertas Exclusivos
5. Zabbix Proxy – Alertas Exclusivos
6. Zabbix Externo
7. Cloudmon
8. Servidores da Solução

## 1. Contexto Geral
Este documento orienta a análise e atuação sobre alarmes do Zabbix.

## 2.Alertas relacionados a fila do Zabbix Server (Queue)
 - **Formato do problema**: Queue --> 10 min --> 10k+ items missing data --> XXXX
**Procedimento de resolução:**
Verificar proxy ativo com `status-all`.
Reiniciar com:
```
stop-all
start-all
```
Se fila >100k itens, aguardar ~10 min.

## 3. Zabbix Server & Proxy – Alertas Comuns
### Cache
- **Formato do Problema**:  Cache --> Configuration cache free --> XXXX
**Procedimento de resolução:**
Abrir Incidente de acionar o plantonista

- **Formato do Problema**: Cache --> History cache free --> XXXX
Apenas abrir um evento, sem acionamento do plantonista

### Processos
- **Formato do Problema**:Processes --> Configuration syncer --> XXXXX
**Procedimento de resolução:**
Abrir Incidente acionar o plantonista e acionar o time de BD Produção
- **Formato do Problema**: Processes --> History syncer --> XXXX

**Procedimento de resolução:**
Abrir Incidente acionar o plantonista e acionar o time de BD Produção

- **Formato do Problema**: Processes --> Housekeeper --> XXXX
**Procedimento de resolução:**
Apenas abrir um evento, sem acionamento do plantonista

- **Formato do Problema**: Processes --> Self-monitoring --> XXXX
**Procedimento de resolução:**
Apenas abrir um evento, sem acionamento do plantonista

- **Formato do Problema**: Processes --> XXXX poller --> XXXX
**Procedimento de resolução:**
Verificar o impacto pelos logs e acionar um Incidente

- **Formato do Problema**: Processes --> Snmp trapper --> XXXX
**Procedimento de resolução:**
Abrir um evento, sem acionamento do plantonista

- **Formato do Problema**: Processes --> Trapper --> XXXX
**Procedimento de resolução:**
Em caso de impactor, abrir incidente, caso seja apenas em um proxy, abrir evento

- **Formato do Problema**: Processes --> Unreachable poller --> XXXX
**Procedimento de resolução:**
Em caso de timeout temporario, abrir incidente, dependendo do impacto abra apenas um evento

### Outros
- **Formato do Problema**: Queue --> 10min --> 180k+ items missing data --> XXXX
**Procedimento de resolução:**
Em caso de impacto nas coletas, abrir incidente

## 4. Zabbix Server – Alertas Exclusivos

- **Formato do Problema**:Cache --> Trends cache free --> XXXX
**Procedimento de resolução:**
Abrir evento, sem acionamento do plantonista

- **Formato do Problema**: Cache --> Value cache free --> XXXX
**Procedimento de resolução:**
Abrir evento, sem acionamento do plantonista

- **Formato do Problema**:Processes --> Alerter processes --> XXXX
**Procedimento de resolução:**
Abrir evento, sem acionamento do plantonista

- **Formato do Problema**: Processes --> DB watchdog --> XXXX
**Procedimento de resolução:**
Em caso de impacto nas coletas, abrir incidente

- **Formato do Problema**: Processes --> Escalator --> XXXX
**Procedimento de resolução:**
Abrir evento, sem acionamento do plantonista

- **Formato do Problema**: Processes --> Proxy poller --> XXXX
**Procedimento de resolução:**
Em caso de impacto nas coletas, abrir incidente

- **Formato do Problema**: Processes --> Timer --> XXXX
**Procedimento de resolução:**
Abrir evento, sem acionamento do plantonista

## 5. Zabbix Proxy – Alertas Exclusivos
- **Formato do Problema**: Data sender process no data for 10 minutes	
**Procedimento de resolução:**
Apenas abrir INC baixo (sem acionamento) para Backoffice Monitoração. Se observar impacto (atraso na monitoração de hosts, alerta persistir em 100% por 20min+), abrir INC e reiniciar o serviço limpando o cache local (stop-zabbix-proxy ; du -hs /opt/zabbix/tmp/zabbix_proxy.db ; force-start-zabbix-proxy ; tail-zabbix-proxy) e comunicar Backoffice enviando o resultado dos comandos.

- **Formato do Problema**: Proxy UNREACHABLE 10m --> Last seen date --> XXXX
**Procedimento de resolução:**
Caso dispare, o proxy pode ter caído. Existe processo de HA no cron dos proxies que faz a virada entre VM's. Verificar se outro proxy subiu e aguardar normalização. Caso o processo não inicie em até 2 minutos, subir o serviço manualmente (KB0015514). Abrir INC e Acionar Backoffice caso não consiga restaurar o serviço.

