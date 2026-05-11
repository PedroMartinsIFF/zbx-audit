import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.logger_config import LoggerSetup

logger = LoggerSetup.get_logger(__name__)

try:
    from pyzabbix import ZabbixAPI
    HAS_PYZABBIX = True
except ImportError:
    HAS_PYZABBIX = False
    logger.warning("⚠️ pyzabbix não encontrado. Usando requests direto.")


class ZabbixCollector:
    def __init__(self, url: str, user: str, password: str):
        self.url = url
        self.user = user
        self.password = password
        self.zapi = None
        self.host_cache = {}

    def connect(self):
        """Conecta ao Zabbix com logging estruturado"""
        try:
            if HAS_PYZABBIX:
                logger.debug(f"Conectando ao Zabbix: {self.url}")
                self.zapi = ZabbixAPI(self.url)
                self.zapi.login(self.user, self.password)
                logger.info(f"🔌 Conectado ao Zabbix: {self.url}")
            else:
                logger.critical("pyzabbix necessário mas não instalado")
                raise ImportError("pyzabbix necessário")
        except Exception as e:
            logger.error(f"❌ Erro ao conectar ao Zabbix: {e}", exc_info=True)
            raise

    def disconnect(self):
        """Desconecta do Zabbix com logging"""
        if self.zapi and HAS_PYZABBIX:
            try:
                if hasattr(self.zapi, "user") and hasattr(self.zapi.user, "logout"):
                    self.zapi.user.logout()
                elif hasattr(self.zapi, "logout"):
                    self.zapi.logout()
                logger.debug("Desconectado do Zabbix")
            except Exception as e:
                logger.warning(f"Erro ao desconectar do Zabbix: {e}")

    def get_events_batch(self, group_name: str, hours_back: int = 24):
        """Coleta eventos de um grupo com logging detalhado"""
        try:
            if not self.zapi:
                logger.error("Não conectado ao Zabbix")
                raise Exception("Não conectado ao Zabbix")
            if not group_name:
                logger.error("group_name é obrigatório")
                raise ValueError("group_name é obrigatório")

            logger.debug(f"Buscando grupo Zabbix: '{group_name}'")
            hostgroups = self.zapi.hostgroup.get(
                filter={'name': group_name},
                output=['groupid', 'name']
            )
            
            if not hostgroups:
                logger.warning(f"❌ Grupo '{group_name}' não encontrado no Zabbix")
                return []
            
            group_id = hostgroups[0]['groupid']
            logger.info(f"🎯 Encontrado grupo: '{group_name}' (ID: {group_id})")

            from datetime import datetime, timedelta
            now = int(datetime.now().timestamp())
            time_from = int((datetime.now() - timedelta(hours=hours_back)).timestamp())

            params = {
                'time_from': time_from,
                'time_till': now,
                'groupids': [group_id],
                'selectHosts': ['host', 'hostid'],
                'output': ['eventid', 'clock', 'name', 'severity', 'value', 'r_eventid'],
                'sortfield': ['clock'],
                'sortorder': 'DESC'
            }

            logger.debug(f"Executando event.get() para grupo '{group_name}'")
            events = self.zapi.event.get(**params)
            logger.info(f"✅ Coletados {len(events)} eventos do grupo '{group_name}'")
            return events
        except Exception as e:
            logger.error(f"❌ Erro ao coletar eventos de {group_name}: {e}", exc_info=True)
            return []

    def get_host_info(self, hostid: str) -> Dict:
        """Obtém informações de host com cache e logging"""
        try:
            if hostid in self.host_cache:
                logger.debug(f"Host {hostid} encontrado em cache")
                return self.host_cache[hostid]
            
            logger.debug(f"Buscando informações do host {hostid}")
            host = self.zapi.host.get(
                hostids=hostid,
                selectHostGroups=['name'],
                output=['host', 'name']
            )
            result = host[0] if host else {}
            self.host_cache[hostid] = result
            logger.debug(f"✅ Host {hostid} em cache")
            return result
        except Exception as e:
            logger.warning(f"⚠️ Erro ao obter host {hostid}: {e}")
            return {}

    def preload_host_cache(self, events: List[Dict]):
        """Pré-carrega cache de hosts com logging"""
        try:
            unique_hostids = set()
            for event in events:
                hosts = event.get('hosts', [])
                if hosts:
                    unique_hostids.add(hosts[0]['hostid'])
            
            if not unique_hostids:
                logger.debug("Nenhum host único encontrado nos eventos")
                return
            
            logger.info(f"🔄 Pré-carregando cache de {len(unique_hostids)} hosts...")
            hosts_data = self.zapi.host.get(
                hostids=list(unique_hostids),
                selectHostGroups=['name'],
                output=['host', 'name', 'hostid']
            )
            for host in hosts_data:
                self.host_cache[host['hostid']] = host
            
            logger.info(f"✅ Cache carregado com {len(self.host_cache)} hosts")
        except Exception as e:
            logger.error(f"❌ Erro ao carregar cache: {e}", exc_info=True)

    def get_all_events(self, hours_back: int = 24):
        """Coleta TODOS os eventos de TODOS os grupos com logging detalhado"""
        try:
            if not self.zapi:
                logger.error("Não conectado ao Zabbix")
                raise Exception("Não conectado ao Zabbix")

            logger.info(f"🌍 Buscando TODOS os eventos do Zabbix (últimas {hours_back} horas)...")

            from datetime import datetime, timedelta
            now = int(datetime.now().timestamp())
            time_from = int((datetime.now() - timedelta(hours=hours_back)).timestamp())

            params = {
                'time_from': time_from,
                'time_till': now,
                'selectHosts': ['host', 'hostid'],
                'output': ['eventid', 'clock', 'name', 'severity', 'value', 'r_eventid'],
                'sortfield': ['clock'],
                'sortorder': 'DESC'
            }

            logger.debug(f"Executando event.get() para TODOS os eventos")
            events = self.zapi.event.get(**params)
            logger.info(f"✅ Coletados {len(events)} eventos de TODAS as fontes do Zabbix")
            return events
        except Exception as e:
            logger.error(f"❌ Erro ao coletar todos os eventos: {e}", exc_info=True)
            return []

    def get_problem_event_timestamp(self, problem_eventid: str) -> int:
        """Busca timestamp do evento de problema para calcular duração do recovery"""
        if not self.zapi or not problem_eventid:
            logger.debug(f"Parâmetros inválidos: zapi={bool(self.zapi)}, eventid={problem_eventid}")
            return None
        
        try:
            logger.debug(f"Buscando timestamp do evento problema: {problem_eventid}")
            problem_event = self.zapi.event.get(
                eventids=problem_eventid,
                output=['clock']
            )
            if problem_event:
                timestamp = int(problem_event[0]['clock'])
                logger.debug(f"✅ Timestamp encontrado para {problem_eventid}: {timestamp}")
                return timestamp
            
            logger.warning(f"Evento {problem_eventid} não encontrado no Zabbix")
            return None
        except Exception as e:
            logger.error(f"❌ Erro ao buscar evento {problem_eventid}: {e}", exc_info=True)
            return None
