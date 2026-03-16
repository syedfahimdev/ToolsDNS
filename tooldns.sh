#!/usr/bin/env bash
# ============================================================
# tooldns.sh — Developer Helper Script for ToolsDNS
# ============================================================
#
# Provides guided commands for managing ToolsDNS running
# directly on the host (systemd + Caddy). No Docker.
#
# Usage:
#   chmod +x tooldns.sh
#   ./tooldns.sh            # Interactive menu
#   ./tooldns.sh install    # Run a specific command
#
# ============================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLDNS_HOME="${TOOLDNS_HOME:-$HOME/.tooldns}"
PYTHON="${PYTHON:-python3}"
PORT="${TOOLDNS_PORT:-8787}"
MCP_PORT="${TOOLDNS_MCP_PORT:-8788}"

# Load MCP port from .env if present
_load_env() {
    local env_file="$TOOLDNS_HOME/.env"
    if [ -f "$env_file" ]; then
        local p
        p=$(grep "^TOOLDNS_MCP_PORT=" "$env_file" | cut -d= -f2 | tr -d ' ')
        [ -n "$p" ] && MCP_PORT="$p"
        p=$(grep "^TOOLDNS_PORT=" "$env_file" | cut -d= -f2 | tr -d ' ')
        [ -n "$p" ] && PORT="$p"
    fi
}
_load_env

_get_api_key() {
    local env_file="$TOOLDNS_HOME/.env"
    [ -f "$env_file" ] && grep "^TOOLDNS_API_KEY=" "$env_file" | cut -d= -f2- || echo ""
}

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║            ⚡ ToolsDNS ⚡               ║${NC}"
    echo -e "${CYAN}║     Developer Helper Script            ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
    echo ""
}

print_step()  { echo -e "${GREEN}▶ $1${NC}"; }
print_info()  { echo -e "${BLUE}  ℹ $1${NC}"; }
print_warn()  { echo -e "${YELLOW}  ⚠ $1${NC}"; }
print_error() { echo -e "${RED}  ✗ $1${NC}"; }

# ============================================================
# Commands
# ============================================================

do_install() {
    print_step "Installing ToolsDNS..."
    echo ""
    print_info "This will:"
    print_info "  1. Create ~/.tooldns home directory"
    print_info "  2. Install Python dependencies (including ONNX for fast search)"
    print_info "  3. Run interactive setup (API key, auto-detect configs)"
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli install
}

do_start() {
    print_step "Starting ToolsDNS server..."
    print_info "Repo: $REPO_DIR"
    print_info "Home: $TOOLDNS_HOME"
    print_info "API docs: http://localhost:$PORT/docs"
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli serve
}

do_status() {
    print_step "Checking ToolsDNS status..."
    echo ""
    # Main API service
    if systemctl is-active --quiet tooldns 2>/dev/null; then
        echo -e "  ${GREEN}✅ tooldns.service: running${NC}"
        systemctl status tooldns --no-pager -l 2>/dev/null | grep -E "(Active|Main PID|Memory|CPU)" | sed 's/^/     /'
    else
        echo -e "  ${RED}❌ tooldns.service: not running${NC}"
        print_info "Start: systemctl start tooldns"
        print_info "Logs:  journalctl -u tooldns -n 30"
    fi
    echo ""
    # MCP HTTP service
    if systemctl is-active --quiet tooldns-mcp 2>/dev/null; then
        echo -e "  ${GREEN}✅ tooldns-mcp.service: running (http://127.0.0.1:${MCP_PORT}/mcp)${NC}"
        systemctl status tooldns-mcp --no-pager -l 2>/dev/null | grep -E "(Active|Main PID|Memory|CPU)" | sed 's/^/     /'
    else
        echo -e "  ${YELLOW}⚠ tooldns-mcp.service: not running${NC}"
        print_info "Start: systemctl start tooldns-mcp"
        print_info "Logs:  journalctl -u tooldns-mcp -n 30"
    fi
    echo ""
    # Caddy status
    if systemctl is-active --quiet caddy 2>/dev/null; then
        echo -e "  ${GREEN}✅ caddy: running${NC}"
    else
        echo -e "  ${YELLOW}⚠ caddy: not running${NC}"
    fi
    echo ""
    # API health check
    HEALTH=$(curl -s --max-time 3 "http://localhost:$PORT/health" 2>/dev/null || true)
    if echo "$HEALTH" | grep -q "healthy"; then
        TOOLS=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tools_indexed',0))" 2>/dev/null || echo "?")
        SOURCES=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('sources',0))" 2>/dev/null || echo "?")
        echo -e "  ${GREEN}✅ API healthy — $TOOLS tools from $SOURCES sources${NC}"
    else
        echo -e "  ${RED}❌ API not responding on port $PORT${NC}"
    fi
    # MCP health check
    MCP_RESP=$(curl -s --max-time 3 "http://127.0.0.1:${MCP_PORT}/mcp" \
        -H "Accept: text/event-stream" 2>/dev/null || true)
    if [[ -n "$MCP_RESP" ]]; then
        echo -e "  ${GREEN}✅ MCP server responding on port ${MCP_PORT}${NC}"
    else
        echo -e "  ${YELLOW}⚠ MCP server not responding on port ${MCP_PORT}${NC}"
    fi
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli status 2>/dev/null || true
}

do_mcp_status() {
    print_step "MCP Server Status"
    echo ""
    if systemctl is-active --quiet tooldns-mcp 2>/dev/null; then
        echo -e "  ${GREEN}✅ tooldns-mcp.service: running${NC}"
        echo ""
        systemctl status tooldns-mcp --no-pager -l 2>/dev/null | grep -E "(Active|Main PID|Memory|CPU|Restart)" | sed 's/^/     /'
        echo ""
        print_info "Endpoint : http://127.0.0.1:${MCP_PORT}/mcp"
        print_info "Transport: HTTP (persistent — no cold-start overhead)"
        print_info "Logs     : journalctl -u tooldns-mcp -f"
    else
        echo -e "  ${RED}❌ tooldns-mcp.service: not running${NC}"
        echo ""
        print_info "Start : systemctl start tooldns-mcp"
        print_info "Enable: systemctl enable tooldns-mcp"
        print_info "Logs  : journalctl -u tooldns-mcp -n 30"
        echo ""
        print_info "Or run deploy.sh to install it automatically."
    fi
    echo ""
}

do_search() {
    local query="$1"
    if [ -z "$query" ]; then
        echo -n "  Enter search query: "
        read -r query
    fi
    print_step "Searching for: '$query'"
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli search "$query"
}

do_add() {
    print_step "Adding a new tool source..."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli add
}

do_ingest() {
    print_step "Re-ingesting all sources..."
    echo ""

    local api_key
    api_key=$(_get_api_key)

    # Show current state before ingest
    local before
    before=$(curl -s --max-time 5 "http://localhost:$PORT/health" \
        -H "Authorization: Bearer $api_key" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tools_indexed',0))" 2>/dev/null || echo "?")
    print_info "Tools before: $before"
    echo ""

    # Run ingest and capture output
    local tmp_log
    tmp_log=$(mktemp)
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli ingest 2>&1 | tee "$tmp_log" | while IFS= read -r line; do
        if echo "$line" | grep -q "Successfully ingested"; then
            echo -e "  ${GREEN}✓${NC} $line"
        elif echo "$line" | grep -q "ERROR\|Failed\|failed\|✗"; then
            echo -e "  ${RED}✗${NC} $line"
        elif echo "$line" | grep -q "WARNING\|Skipping\|excluded\|Unauthorized"; then
            echo -e "  ${YELLOW}!${NC} $line"
        elif echo "$line" | grep -q "Ingesting source\|Connecting\|Fetching\|Discovered"; then
            echo -e "  ${BLUE}→${NC} $line"
        fi
    done

    echo ""

    # Show summary after ingest
    sleep 1
    local after sources errors
    local health_resp
    health_resp=$(curl -s --max-time 5 "http://localhost:$PORT/health" \
        -H "Authorization: Bearer $api_key" 2>/dev/null)
    after=$(echo "$health_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tools_indexed',0))" 2>/dev/null || echo "?")
    sources=$(echo "$health_resp" | python3 -c "import sys,json; d=json.load(sys.stdin); srcs=d.get('sources',[]); print(len(srcs) if isinstance(srcs,list) else srcs)" 2>/dev/null || echo "?")
    errors=$(grep -c "ERROR\|Failed\|✗" "$tmp_log" 2>/dev/null || echo 0)

    rm -f "$tmp_log"

    echo -e "  ${BOLD}──── Ingest Summary ────${NC}"
    echo -e "  Tools before : $before"
    echo -e "  Tools after  : $after"
    echo -e "  Sources      : $sources"
    [[ "$errors" -gt 0 ]] && \
        echo -e "  ${RED}Errors       : $errors (check logs above)${NC}" || \
        echo -e "  ${GREEN}Errors       : 0${NC}"
    echo ""
}

do_update() {
    print_step "Updating ToolsDNS from git..."
    echo ""
    cd "$REPO_DIR"
    git pull --ff-only
    # Reinstall in case dependencies changed
    if [ -f "$REPO_DIR/.venv/bin/pip" ]; then
        "$REPO_DIR/.venv/bin/pip" install -q -e "$REPO_DIR"
    else
        $PYTHON -m pip install -q -e "$REPO_DIR"
    fi
    systemctl restart tooldns 2>/dev/null && print_info "tooldns restarted" || true
    systemctl restart tooldns-mcp 2>/dev/null && print_info "tooldns-mcp restarted" || true
    print_info "Update complete. Logs: journalctl -u tooldns -n 20"
}

do_caddy_setup() {
    print_step "Configuring Caddy reverse proxy for ToolsDNS..."
    echo ""
    if ! command -v caddy &>/dev/null; then
        print_error "Caddy not found. Install it first:"
        print_info "  https://caddyserver.com/docs/install"
        return 1
    fi

    echo -n "  Domain (e.g. api.toolsdns.com): "
    read -r DOMAIN
    if [ -z "$DOMAIN" ]; then
        print_warn "No domain entered. Skipping."
        return
    fi

    mkdir -p /etc/caddy/conf.d
    SNIPPET="/etc/caddy/conf.d/tooldns.caddy"
    cat > "$SNIPPET" << EOF
# ToolsDNS API — managed by tooldns.sh
$DOMAIN {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        -Server
    }
    reverse_proxy localhost:$PORT {
        header_up X-Forwarded-For {remote_host}
        header_up X-Real-IP {remote_host}
    }
}
EOF

    CADDYFILE="/etc/caddy/Caddyfile"
    if ! grep -q "conf.d" "$CADDYFILE" 2>/dev/null; then
        echo "" >> "$CADDYFILE"
        echo "import /etc/caddy/conf.d/*.caddy" >> "$CADDYFILE"
    fi

    if caddy validate --config "$CADDYFILE" 2>/dev/null; then
        systemctl reload caddy 2>/dev/null || systemctl restart caddy
        print_info "Caddy reloaded"
        echo ""
        echo -e "  ${GREEN}✅ ToolsDNS will be served at https://$DOMAIN${NC}"
        print_info "Make sure DNS for $DOMAIN points to this server's IP."
    else
        print_error "Caddy config validation failed — check $SNIPPET"
    fi
}

do_setup_service() {
    print_step "Setting up ToolsDNS as a systemd service..."
    echo ""

    VENV="$REPO_DIR/.venv"
    [ -d "$VENV" ] || { print_error "No .venv found at $REPO_DIR/.venv — run ./tooldns.sh install first"; return 1; }

    cat > /etc/systemd/system/tooldns.service << EOF
[Unit]
Description=ToolsDNS — AI Tool Discovery Service
Documentation=https://github.com/syedfahimdev/ToolsDNS
After=network.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_DIR
EnvironmentFile=$TOOLDNS_HOME/.env
ExecStart=$VENV/bin/python3 -m tooldns.cli serve
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tooldns
ReadWritePaths=$TOOLDNS_HOME $REPO_DIR
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable tooldns
    systemctl restart tooldns
    sleep 3

    if systemctl is-active --quiet tooldns; then
        print_info "✅ tooldns.service is running and enabled"
    else
        print_error "Service failed — check: journalctl -u tooldns -n 30"
    fi

    echo ""
    print_info "Useful commands:"
    print_info "  journalctl -u tooldns -f      # Follow logs"
    print_info "  systemctl restart tooldns     # Restart"
    print_info "  systemctl stop tooldns        # Stop"
}

do_logs() {
    print_step "ToolsDNS live logs (Ctrl+C to exit)..."
    echo ""
    if systemctl is-active --quiet tooldns 2>/dev/null; then
        journalctl -u tooldns -f --no-pager
    else
        local log_file="$TOOLDNS_HOME/tooldns.log"
        if [ -f "$log_file" ]; then
            tail -n 40 "$log_file"
        else
            print_warn "Service not running and no log file found."
            print_info "Start: systemctl start tooldns"
        fi
    fi
}

do_test_api() {
    local api_key
    api_key=$(_get_api_key)
    print_step "Testing ToolsDNS API on port $PORT..."
    echo ""

    echo -e "${BLUE}  1. Health check:${NC}"
    if curl -s "http://localhost:$PORT/health" 2>/dev/null | python3 -m json.tool 2>/dev/null; then
        echo -e "${GREEN}     ✅ Server is running${NC}"
    else
        print_error "Server is not responding. Start: systemctl start tooldns"
        return
    fi
    echo ""

    echo -e "${BLUE}  2. Search (query: 'send email'):${NC}"
    curl -s -X POST "http://localhost:$PORT/v1/search" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $api_key" \
        -d '{"query": "send email", "top_k": 3}' | python3 -m json.tool | head -50
    echo ""

    echo -e "${BLUE}  3. Sources:${NC}"
    curl -s "http://localhost:$PORT/v1/sources" \
        -H "Authorization: Bearer $api_key" | python3 -m json.tool | head -30
    echo ""

    print_step "API tests complete!"
}

do_integrate() {
    print_step "Wiring ToolsDNS into your AI agent..."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli integrate
}

do_install_mcp() {
    print_step "Install a new MCP server..."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli install-mcp
}

do_new_skill() {
    print_step "Create a new skill file..."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli new-skill
}

do_key_list() {
    local api_key
    api_key=$(_get_api_key)
    print_step "API Keys"
    echo ""
    local resp
    resp=$(curl -s -H "Authorization: Bearer $api_key" "http://localhost:$PORT/v1/api-keys" 2>/dev/null)
    if ! echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
keys = d.get('keys', [])
if not keys:
    print('  (no sub-keys yet)')
else:
    fmt = '  {:<20} {:<12} {:<8} {:>8} {:>12} {:>14} {}'
    print(fmt.format('Name', 'Plan', 'Status', 'Searches', 'Tokens Used', 'Tokens Saved', 'Key'))
    print('  ' + '-'*100)
    for k in keys:
        status = 'active' if k.get('is_active') else 'revoked'
        print(fmt.format(
            k.get('name','')[:20],
            k.get('plan','')[:12],
            status,
            str(k.get('total_searches', 0)),
            str(k.get('total_tokens_used', 0)),
            str(k.get('total_tokens_saved', 0)),
            k.get('key',''),
        ))
" 2>/dev/null; then
        print_error "Could not reach API on port $PORT"
    fi
    echo ""
}

do_key_create() {
    local api_key
    api_key=$(_get_api_key)
    print_step "Create a new API key"
    echo ""
    echo -n "  Name (e.g. my-agent, acme-corp): "
    read -r name
    [ -z "$name" ] && { print_warn "Name required."; return; }

    echo -n "  Label (shown to key holder, optional): "
    read -r label

    echo -n "  Plan [free/pro/enterprise] (default: free): "
    read -r plan
    plan="${plan:-free}"

    echo -n "  Monthly search limit (0 = unlimited, default: 0): "
    read -r limit
    limit="${limit:-0}"

    local body
    body=$(python3 -c "import json; print(json.dumps({'name': '$name', 'label': '$label', 'plan': '$plan', 'monthly_limit': $limit}))")

    local resp
    resp=$(curl -s -X POST "http://localhost:$PORT/v1/api-keys" \
        -H "Authorization: Bearer $api_key" \
        -H "Content-Type: application/json" \
        -d "$body" 2>/dev/null)

    local new_key
    new_key=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('key','ERROR'))" 2>/dev/null)

    if [[ "$new_key" == td_* ]]; then
        echo ""
        echo -e "  ${GREEN}✅ Key created successfully!${NC}"
        echo ""
        echo -e "  ${BOLD}Name :${NC} $name"
        echo -e "  ${BOLD}Key  :${NC} $new_key"
        echo ""
        echo -e "  ${CYAN}Use in agent config:${NC}"
        echo "    Authorization: Bearer $new_key"
        echo ""
        echo -e "  ${CYAN}MCP connection (n8n / cursor / claude desktop):${NC}"
        echo "    URL: https://your-domain.com/mcp"
        echo "    Authorization: Bearer $new_key"
    else
        print_error "Failed to create key: $resp"
    fi
    echo ""
}

do_key_revoke() {
    local api_key
    api_key=$(_get_api_key)
    print_step "Revoke an API key"
    echo ""
    do_key_list
    echo -n "  Enter key to revoke (td_...): "
    read -r target
    [ -z "$target" ] && { print_warn "Cancelled."; return; }
    echo -n "  Revoke $target? [y/N]: "
    read -r confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        local resp
        resp=$(curl -s -X POST "http://localhost:$PORT/v1/api-keys/$target/revoke" \
            -H "Authorization: Bearer $api_key" 2>/dev/null)
        echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print('  ✅ Revoked' if d.get('ok') else f'  Error: {d}')" 2>/dev/null
    else
        print_info "Cancelled."
    fi
    echo ""
}

do_key_delete() {
    local api_key
    api_key=$(_get_api_key)
    print_step "Delete an API key (permanent)"
    echo ""
    do_key_list
    echo -n "  Enter key to delete (td_...): "
    read -r target
    [ -z "$target" ] && { print_warn "Cancelled."; return; }
    echo -n "  Permanently delete $target? [y/N]: "
    read -r confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        local resp
        resp=$(curl -s -X DELETE "http://localhost:$PORT/v1/api-keys/$target" \
            -H "Authorization: Bearer $api_key" 2>/dev/null)
        echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print('  ✅ Deleted' if d.get('ok') else f'  Error: {d}')" 2>/dev/null
    else
        print_info "Cancelled."
    fi
    echo ""
}

do_stats() {
    local api_key
    api_key=$(_get_api_key)
    print_step "Token Savings Stats"
    echo ""
    curl -s -H "Authorization: Bearer $api_key" "http://localhost:$PORT/v1/stats" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('  Could not reach API')
    sys.exit(0)
print(f\"  Total searches      : {d.get('total_searches', 0):,}\")
print(f\"  Tokens saved        : {d.get('total_tokens_saved', 0):,}\")
print(f\"  Tokens actually used: {d.get('total_tokens_actually_used', 0):,}\")
print(f\"  Cost saved (USD)    : \${d.get('total_cost_saved_usd', 0):.4f}\")
print(f\"  Avg tokens saved    : {d.get('avg_tokens_saved', 0):,} per search\")
print(f\"  Avg search time     : {d.get('avg_search_time_ms', 0):.1f}ms\")
recent = d.get('recent_searches', [])
if recent:
    print()
    print('  Recent searches:')
    for r in recent[:5]:
        q = str(r.get('query',''))[:40]
        ts = int(r.get('tokens_saved') or 0)
        ms = float(r.get('search_time_ms') or 0)
        print(f\"    '{q}' -> saved {ts:,} tokens ({ms:.0f}ms)\")
"
    echo ""
}

do_reset() {
    print_warn "This will delete the ToolsDNS database and re-ingest everything."
    echo -n "  Continue? [y/N]: "
    read -r confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        rm -f "$TOOLDNS_HOME/tooldns.db"
        print_info "Database deleted."
        print_info "Restarting service..."
        systemctl restart tooldns 2>/dev/null || print_info "Start manually: ./tooldns.sh start"
    else
        print_info "Cancelled."
    fi
}

do_info() {
    print_step "ToolsDNS Configuration"
    echo ""
    print_info "Home directory : $TOOLDNS_HOME"
    print_info "Repo directory : $REPO_DIR"
    print_info "Config file    : $TOOLDNS_HOME/.env"
    print_info "Database       : $TOOLDNS_HOME/tooldns.db"
    echo ""

    if [ -f "$TOOLDNS_HOME/.env" ]; then
        print_step "Current .env settings:"
        while IFS= read -r line; do
            if [[ "$line" == *"KEY"* || "$line" == *"SECRET"* || "$line" == *"PASSWORD"* ]]; then
                key=$(echo "$line" | cut -d= -f1)
                echo "    $key=****"
            elif [ -n "$line" ] && [[ "$line" != \#* ]]; then
                echo "    $line"
            fi
        done < "$TOOLDNS_HOME/.env"
    else
        print_warn "No config found. Run: ./tooldns.sh install"
    fi
    echo ""

    [ -f "$TOOLDNS_HOME/tooldns.db" ] && \
        print_info "Database size: $(du -h "$TOOLDNS_HOME/tooldns.db" | cut -f1)"

    # Show Caddy config if present
    if [ -f "/etc/caddy/conf.d/tooldns.caddy" ]; then
        echo ""
        print_step "Caddy config (/etc/caddy/conf.d/tooldns.caddy):"
        cat /etc/caddy/conf.d/tooldns.caddy | grep -v "^#" | sed 's/^/    /'
    fi
}

# ============================================================
# Interactive Menu
# ============================================================

show_menu() {
    print_header
    echo "  What do you want to do?"
    echo ""
    echo -e "  ${CYAN}Getting Started:${NC}"
    echo "    1)  install        First-time setup"
    echo "    2)  integrate      Wire ToolsDNS into supported agent frameworks"
    echo "    3)  start          Start the API server (foreground)"
    echo "    4)  status         Show service status & health"
    echo ""
    echo -e "  ${CYAN}Add Tools & Skills:${NC}"
    echo "    5)  install-mcp    Install a new MCP server + index tools"
    echo "    6)  new-skill      Create a new skill file"
    echo "    7)  add            Add an existing source (config, URL, folder)"
    echo "    8)  ingest         Re-scan all sources for new tools"
    echo ""
    echo -e "  ${CYAN}API Key Management:${NC}"
    echo "    9)  key-list       List all sub-keys with usage + token stats"
    echo "   10)  key-create     Create a new sub-key for an agent or customer"
    echo "   11)  key-revoke     Disable a key (reversible)"
    echo "   12)  key-delete     Permanently delete a key"
    echo "   13)  stats          Show global token savings stats"
    echo ""
    echo -e "  ${CYAN}Search & Debug:${NC}"
    echo "   14)  search         Search for a tool by description"
    echo "   15)  test-api       Test all API endpoints with live requests"
    echo "   16)  logs           Follow live service logs"
    echo "   17)  info           Show config details (key, paths, DB size)"
    echo ""
    echo -e "  ${CYAN}Deployment (host + Caddy):${NC}"
    echo "   18)  setup-service  Install/update the systemd service"
    echo "   19)  caddy-setup    Configure Caddy reverse proxy + HTTPS"
    echo "   20)  update         Pull latest code and restart"
    echo "   21)  reset          Wipe database and restart fresh"
    echo "   22)  mcp-status     MCP HTTP server status + endpoint info"
    echo ""
    echo "    0)  exit"
    echo ""
    echo -n "  Choice: "
}

# ============================================================
# Main
# ============================================================

if [ $# -gt 0 ]; then
    case "$1" in
        install)        do_install ;;
        integrate)      do_integrate ;;
        start)          do_start ;;
        status)         do_status ;;
        install-mcp)    do_install_mcp ;;
        new-skill)      do_new_skill ;;
        key-list)       do_key_list ;;
        key-create)     do_key_create ;;
        key-revoke)     do_key_revoke ;;
        key-delete)     do_key_delete ;;
        stats)          do_stats ;;
        search)         shift; do_search "$*" ;;
        add)            do_add ;;
        ingest)         do_ingest ;;
        update)         do_update ;;
        test-api)       do_test_api ;;
        logs)           do_logs ;;
        info)           do_info ;;
        reset)          do_reset ;;
        setup-service)  do_setup_service ;;
        caddy-setup)    do_caddy_setup ;;
        mcp-status)     do_mcp_status ;;
        help|--help|-h)
            print_header
            echo "  Usage: ./tooldns.sh [command]"
            echo "         ./tooldns.sh            (interactive menu)"
            echo ""
            echo "  Getting Started:"
            echo "    install        First-time setup"
            echo "    integrate      Wire ToolsDNS into supported agent frameworks"
            echo "    start          Start the API server (foreground)"
            echo "    status         Show service status and health"
            echo ""
            echo "  Add Tools & Skills:"
            echo "    install-mcp    Install a new MCP server and index its tools"
            echo "    new-skill      Create a new skill file template"
            echo "    add            Add an existing source (config, URL, folder)"
            echo "    ingest         Re-scan all sources for new tools"
            echo ""
            echo "  API Key Management:"
            echo "    key-list       List all sub-keys with usage + token stats"
            echo "    key-create     Create a new sub-key"
            echo "    key-revoke     Disable a key"
            echo "    key-delete     Permanently delete a key"
            echo "    stats          Show global token savings stats"
            echo ""
            echo "  Search & Debug:"
            echo "    search         Search for a tool: ./tooldns.sh search 'send email'"
            echo "    test-api       Send live requests to all API endpoints"
            echo "    logs           Follow live service logs"
            echo "    info           Show config details (key, paths, DB size)"
            echo ""
            echo "  Deployment (host + Caddy):"
            echo "    setup-service  Install/update the systemd service"
            echo "    caddy-setup    Configure Caddy reverse proxy + HTTPS"
            echo "    update         Pull latest code and restart"
            echo "    reset          Wipe database and restart fresh"
            echo "    mcp-status     MCP HTTP server status + endpoint info"
            ;;
        *)
            print_error "Unknown command: $1"
            echo "  Run './tooldns.sh help' to see all commands."
            exit 1
            ;;
    esac
else
    while true; do
        show_menu
        read -r choice
        echo ""
        case "$choice" in
            1)  do_install ;;
            2)  do_integrate ;;
            3)  do_start ;;
            4)  do_status ;;
            5)  do_install_mcp ;;
            6)  do_new_skill ;;
            7)  do_add ;;
            8)  do_ingest ;;
            9)  do_key_list ;;
            10) do_key_create ;;
            11) do_key_revoke ;;
            12) do_key_delete ;;
            13) do_stats ;;
            14) do_search ;;
            15) do_test_api ;;
            16) do_logs ;;
            17) do_info ;;
            18) do_setup_service ;;
            19) do_caddy_setup ;;
            20) do_update ;;
            21) do_reset ;;
            22) do_mcp_status ;;
            0)  echo "  Goodbye!"; exit 0 ;;
            *)  print_error "Invalid choice." ;;
        esac
        echo ""
        echo -n "  Press Enter to continue..."
        read -r
    done
fi
