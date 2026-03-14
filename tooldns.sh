#!/usr/bin/env bash
# ============================================================
# tooldns.sh — Developer Helper Script for ToolDNS
# ============================================================
#
# This script provides guided commands for installing, running,
# testing, and debugging ToolDNS. Designed for junior devs
# and anyone new to the project.
#
# Usage:
#   chmod +x tooldns.sh
#   ./tooldns.sh            # Show interactive menu
#   ./tooldns.sh install    # Run a specific command
#
# ============================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLDNS_HOME="${TOOLDNS_HOME:-$HOME/.tooldns}"
PYTHON="${PYTHON:-python3}"

# Read API key from .env file
_get_api_key() {
    local env_file="$TOOLDNS_HOME/.env"
    if [ -f "$env_file" ]; then
        grep "^TOOLDNS_API_KEY=" "$env_file" | cut -d= -f2-
    else
        echo ""
    fi
}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_header() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║            ⚡ ToolDNS ⚡               ║${NC}"
    echo -e "${CYAN}║     Developer Helper Script            ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
    echo ""
}

print_step() {
    echo -e "${GREEN}▶ $1${NC}"
}

print_info() {
    echo -e "${BLUE}  ℹ $1${NC}"
}

print_warn() {
    echo -e "${YELLOW}  ⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}  ✗ $1${NC}"
}

# ============================================================
# Commands
# ============================================================

do_install() {
    print_step "Installing ToolDNS..."
    echo ""
    print_info "This will:"
    print_info "  1. Create ~/.tooldns home directory"
    print_info "  2. Install Python dependencies"
    print_info "  3. Run interactive setup (API key, auto-detect configs)"
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli install
}

do_start() {
    print_step "Starting ToolDNS server..."
    print_info "Repo: $REPO_DIR"
    print_info "Home: $TOOLDNS_HOME"
    print_info "Swagger docs will be at: http://localhost:8787/docs"
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli serve
}

do_status() {
    print_step "Checking ToolDNS status..."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli status
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
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli ingest
}

do_update() {
    print_step "Updating ToolDNS from git..."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli update
}

do_logs() {
    local log_file="$TOOLDNS_HOME/tooldns.log"
    if [ ! -f "$log_file" ]; then
        print_warn "No log file found at $log_file"
        print_info "Start the server first: ./tooldns.sh start"
        return
    fi
    print_step "Last 30 lines of $log_file:"
    echo ""
    tail -n 30 "$log_file"
    echo ""
    print_info "Full log: $log_file"
    print_info "Watch live: tail -f $log_file"
}

do_test_api() {
    local port="${TOOLDNS_PORT:-8787}"
    local api_key
    api_key=$(_get_api_key)
    print_step "Testing ToolDNS API on port $port..."
    echo ""

    if [ -z "$api_key" ]; then
        print_warn "No API key found in $TOOLDNS_HOME/.env — authenticated endpoints may fail."
        print_info "Run './tooldns.sh install' to set one up."
        echo ""
    fi

    # Health check (no auth needed)
    echo -e "${BLUE}  1. Health check:${NC}"
    if curl -s "http://localhost:$port/health" 2>/dev/null | python3 -m json.tool 2>/dev/null; then
        echo -e "${GREEN}     ✅ Server is running${NC}"
    else
        print_error "Server is not running. Start it first: ./tooldns.sh start"
        return
    fi
    echo ""

    # Root endpoint
    echo -e "${BLUE}  2. Root endpoint:${NC}"
    curl -s "http://localhost:$port/" | python3 -m json.tool
    echo ""

    # Search test (needs auth)
    echo -e "${BLUE}  3. Search test (query: 'send email'):${NC}"
    curl -s -X POST "http://localhost:$port/v1/search" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $api_key" \
        -d '{"query": "send email", "top_k": 3}' | python3 -m json.tool
    echo ""

    # List sources (needs auth)
    echo -e "${BLUE}  4. Registered sources:${NC}"
    curl -s "http://localhost:$port/v1/sources" \
        -H "Authorization: Bearer $api_key" | python3 -m json.tool | head -30
    echo ""

    print_step "API tests complete!"
}

do_integrate() {
    print_step "Wiring ToolDNS into your AI agent (nanobot/openclaw)..."
    echo ""
    print_info "This will:"
    print_info "  1. Add ToolDNS to your agent's MCP server list"
    print_info "  2. Move heavy tool servers to ToolDNS config (saves tokens)"
    print_info "  3. Update AGENTS.md with correct ToolDNS instructions"
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli integrate
}

do_install_mcp() {
    print_step "Install a new MCP server..."
    echo ""
    print_info "Installs the package, saves credentials, and indexes its tools."
    print_info "Works with npm/npx, pip/Python, or any custom command."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli install-mcp
}

do_new_skill() {
    print_step "Create a new skill file..."
    echo ""
    print_info "Skills are markdown files that teach your agent how to use an API"
    print_info "or perform a multi-step task. Saved to ~/.tooldns/skills/ or any"
    print_info "skill folder configured in ~/.tooldns/config.json."
    echo ""
    cd "$REPO_DIR"
    $PYTHON -m tooldns.cli new-skill
}

do_reset() {
    print_warn "This will delete the ToolDNS database and re-ingest everything."
    echo -n "  Continue? [y/N]: "
    read -r confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        rm -f "$TOOLDNS_HOME/tooldns.db"
        print_info "Database deleted."
        print_info "Run './tooldns.sh start' to restart, or './tooldns.sh add' to re-add sources."
    else
        print_info "Cancelled."
    fi
}

do_info() {
    print_step "ToolDNS Configuration"
    echo ""
    print_info "Home directory:   $TOOLDNS_HOME"
    print_info "Repo directory:   $REPO_DIR"
    print_info "Config file:      $TOOLDNS_HOME/.env"
    print_info "Database:         $TOOLDNS_HOME/tooldns.db"
    print_info "Log file:         $TOOLDNS_HOME/tooldns.log"
    echo ""

    if [ -f "$TOOLDNS_HOME/.env" ]; then
        print_step "Current .env settings:"
        while IFS= read -r line; do
            # Mask API keys
            if [[ "$line" == *"API_KEY"* ]]; then
                key=$(echo "$line" | cut -d= -f1)
                echo "    $key=td_****"
            elif [ -n "$line" ] && [[ "$line" != \#* ]]; then
                echo "    $line"
            fi
        done < "$TOOLDNS_HOME/.env"
    else
        print_warn "No config found. Run: ./tooldns.sh install"
    fi
    echo ""

    if [ -f "$TOOLDNS_HOME/tooldns.db" ]; then
        local db_size
        db_size=$(du -h "$TOOLDNS_HOME/tooldns.db" | cut -f1)
        print_info "Database size: $db_size"
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
    echo "    1)  install      First-time setup (creates ~/.tooldns, installs deps)"
    echo "    2)  integrate    Wire ToolDNS into nanobot/openclaw (updates AGENTS.md)"
    echo "    3)  start        Start the API server"
    echo "    4)  status       Show system status & health"
    echo ""
    echo -e "  ${CYAN}Add Tools & Skills:${NC}"
    echo "    5)  install-mcp  Install a new MCP server + set env vars + index tools"
    echo "    6)  new-skill    Create a new skill file template"
    echo "    7)  add          Add an existing source (config file, URL, folder)"
    echo "    8)  ingest       Re-scan all sources to pick up new tools"
    echo ""
    echo -e "  ${CYAN}Search & Debug:${NC}"
    echo "    9)  search       Search for a tool by description"
    echo "   10)  test-api     Test all API endpoints with live requests"
    echo "   11)  logs         View recent server log entries"
    echo "   12)  info         Show configuration details (API key, paths, DB size)"
    echo ""
    echo -e "  ${CYAN}Maintenance:${NC}"
    echo "   13)  update       Pull latest code from git and reinstall"
    echo "   14)  reset        Wipe database and start fresh"
    echo ""
    echo "    0)  exit"
    echo ""
    echo -n "  Choice: "
}

# ============================================================
# Main
# ============================================================

if [ $# -gt 0 ]; then
    # Direct command mode
    case "$1" in
        install)      do_install ;;
        integrate)    do_integrate ;;
        start)        do_start ;;
        status)       do_status ;;
        install-mcp)  do_install_mcp ;;
        new-skill)    do_new_skill ;;
        search)       shift; do_search "$*" ;;
        add)          do_add ;;
        ingest)       do_ingest ;;
        update)       do_update ;;
        test-api)     do_test_api ;;
        logs)         do_logs ;;
        info)         do_info ;;
        reset)        do_reset ;;
        help|--help|-h)
            print_header
            echo "  Usage: ./tooldns.sh [command]"
            echo "         ./tooldns.sh           (interactive menu)"
            echo ""
            echo "  Getting Started:"
            echo "    install      First-time setup (creates ~/.tooldns, installs deps)"
            echo "    integrate    Wire ToolDNS into nanobot/openclaw (updates AGENTS.md)"
            echo "    start        Start the API server"
            echo "    status       Show system status and health"
            echo ""
            echo "  Add Tools & Skills:"
            echo "    install-mcp  Install a new MCP server, set env vars, and index tools"
            echo "    new-skill    Create a new skill file template"
            echo "    add          Add an existing source (config file, URL, folder)"
            echo "    ingest       Re-scan all sources to pick up new tools"
            echo ""
            echo "  Search & Debug:"
            echo "    search       Search for a tool  e.g: ./tooldns.sh search 'send email'"
            echo "    test-api     Send live requests to all API endpoints"
            echo "    logs         View recent server log entries"
            echo "    info         Show config details (API key, paths, DB size)"
            echo ""
            echo "  Maintenance:"
            echo "    update       Pull latest code from git and reinstall"
            echo "    reset        Wipe database and start fresh"
            ;;
        *)
            print_error "Unknown command: $1"
            echo ""
            echo "  Run './tooldns.sh help' to see all available commands."
            echo "  Run './tooldns.sh' (no args) to open the interactive menu."
            exit 1
            ;;
    esac
else
    # Interactive menu mode
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
            9)  do_search ;;
            10) do_test_api ;;
            11) do_logs ;;
            12) do_info ;;
            13) do_update ;;
            14) do_reset ;;
            0)  echo "  Goodbye!"; exit 0 ;;
            *)  print_error "Invalid choice. Enter a number from the menu above." ;;
        esac
        echo ""
        echo -n "  Press Enter to continue..."
        read -r
    done
fi
