# Deploy remoto do ZBX Audit (Streamlit + Nginx + systemd)

Este guia publica a interface de forma segura com HTTPS usando reverse proxy.

## 1) Pré-requisitos

- Servidor Linux com `systemd` (Ubuntu/Debian/RHEL)
- Domínio apontando para o IP do servidor (ex.: `audit.exemplo.com`)
- Portas 80 e 443 liberadas no firewall/security group
- PostgreSQL acessível a partir do servidor

## 2) Estrutura sugerida no servidor

- Código: `/opt/zbx-audit`
- Virtualenv: `/opt/zbx-audit/.venv`
- Serviço: `/etc/systemd/system/zbx-audit-streamlit.service`
- Variáveis: `/etc/default/zbx-audit`
- Nginx site: `/etc/nginx/sites-available/zbx-audit.conf`

## 3) Instalar app e dependências

```bash
sudo mkdir -p /opt/zbx-audit
sudo chown -R $USER:$USER /opt/zbx-audit

# copie o projeto para /opt/zbx-audit (git clone/rsync/scp)
cd /opt/zbx-audit
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4) Configurar variáveis de ambiente

Crie `/etc/default/zbx-audit`:

```bash
sudo tee /etc/default/zbx-audit > /dev/null <<'EOF'
ZABBIX_URL=https://seu-zabbix
ZABBIX_USER=seu_usuario
ZABBIX_PASSWORD=sua_senha

DB_NAME=zabbix_events
DB_USER=zbx_user
DB_PASSWORD=zbx_pass
DB_HOST=127.0.0.1
DB_PORT=5432

OLLAMA_MODEL=phi3:mini
OLLAMA_API_URL=http://127.0.0.1:11434/api/generate
LOG_LEVEL=INFO
DB_POOL_MIN=2
DB_POOL_MAX=10
EOF
```

## 5) Subir Streamlit como serviço

1. Ajuste os placeholders do arquivo `deploy/systemd/zbx-audit-streamlit.service`:
   - `User=zbxaudit`
   - `WorkingDirectory=/opt/zbx-audit`
   - `ExecStart=/opt/zbx-audit/.venv/bin/streamlit run /opt/zbx-audit/app.py ...`

2. Crie usuário de serviço (se necessário):

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin zbxaudit || true
sudo chown -R zbxaudit:www-data /opt/zbx-audit
```

3. Instale e habilite:

```bash
sudo cp deploy/systemd/zbx-audit-streamlit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zbx-audit-streamlit
sudo systemctl status zbx-audit-streamlit --no-pager
```

4. Logs:

```bash
journalctl -u zbx-audit-streamlit -f
```

## 6) Publicar com Nginx

1. Ajuste domínio em `deploy/nginx/zbx-audit.conf` (`server_name`).
2. Instale o site e valide:

```bash
sudo cp deploy/nginx/zbx-audit.conf /etc/nginx/sites-available/zbx-audit.conf
sudo ln -sf /etc/nginx/sites-available/zbx-audit.conf /etc/nginx/sites-enabled/zbx-audit.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 7) HTTPS com Let's Encrypt

```bash
sudo apt-get update && sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d audit.exemplo.com
```

## 8) Firewall

- Libere apenas `80/443` externamente.
- **Não exponha 8501** para internet.

Exemplo UFW:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8501/tcp
sudo ufw reload
```

## 9) Checklist de validação

- `systemctl status zbx-audit-streamlit` sem erro
- `curl -I http://127.0.0.1:8501` no servidor responde
- `nginx -t` OK
- `https://audit.exemplo.com` abre externamente
- Certificado válido (cadeado HTTPS)

## 10) Troubleshooting rápido

- Erro DB no app: revisar `/etc/default/zbx-audit` e conectividade para `DB_HOST:DB_PORT`
- 502 no Nginx: serviço Streamlit parado ou porta errada
- Timeout: aumentar `proxy_read_timeout` no Nginx
- Mudanças no app não refletem: `sudo systemctl restart zbx-audit-streamlit`
