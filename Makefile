.PHONY: help setup install install-dev validate test list sync unit clean activate dev-help dev-list dev-create dev-create-ticket dev-samples dev-rsvp dev-bulk-rsvp dev-search dev-clean-drafts dev-clean-test dev-clean-all

# Default target
help:
	@echo "Wix Events + Google Sheets Sync - Available Commands:"
	@echo ""
	@echo "Production Commands:"
	@echo "  make setup      - Complete setup (create venv, install deps, create .env)"
	@echo "  make install    - Install Python dependencies"
	@echo "  make install-dev- Install Python + dev/test dependencies"
	@echo "  make validate   - Validate credentials configuration"
	@echo "  make test       - Test Wix API connection"
	@echo "  make list       - List existing Wix events"
	@echo "  make sync       - Run the event sync"
	@echo "  make unit       - Run automated unit tests"
	@echo "  make clean      - Remove virtual environment and cache files"
	@echo "  make activate   - Show how to activate virtual environment"
	@echo ""
	@echo "Development Tools:"
	@echo "  make dev-help         - Show development tools help"
	@echo "  make dev-list         - List events using dev tools"
	@echo "  make dev-create       - Create test RSVP event"
	@echo "  make dev-create-ticket- Create test TICKETED event"
	@echo "  make dev-samples      - Create 5 sample events (mix of types)"
	@echo "  make dev-rsvp         - Create test RSVP (requires EVENT_ID=...)"
	@echo "  make dev-bulk-rsvp    - Create 10 test RSVPs (requires EVENT_ID=...)"
	@echo "  make dev-search       - Search events (requires QUERY=...)"
	@echo ""
	@echo "Cleanup Commands:"
	@echo "  make dev-clean-drafts - Delete all DRAFT events"
	@echo "  make dev-clean-test   - Delete all events with 'Test' in title"
	@echo "  make dev-clean-all    - Delete ALL events (use with caution!)"
	@echo ""
	@echo "See DEV_TOOLS.md for complete development tools documentation"
	@echo ""

# Setup environment
setup:
	@echo "Setting up environment..."
	@python3 -m venv venv
	@./venv/bin/pip install --upgrade pip
	@./venv/bin/pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		printf "# Wix Credentials\nWIX_API_KEY=\nWIX_ACCOUNT_ID=\nWIX_SITE_ID=\n\n# Google Sheets\nGOOGLE_SHEET_ID=\nGOOGLE_CREDENTIALS=\n" > .env; \
		echo "Created .env template - please add your credentials"; \
	fi
	@echo "Setup complete! Activate venv with: source venv/bin/activate"

# Install dependencies
install:
	@echo "Installing dependencies..."
	@pip install -r requirements.txt

install-dev:
	@echo "Installing dev/test dependencies..."
	@pip install -r requirements-dev.txt

# Validate credentials
validate:
	@python sync_events.py validate

# Test Wix connection
test:
	@python sync_events.py test

# List existing events
list:
	@python sync_events.py list

# Run sync
sync:
	@python sync_events.py sync

unit:
	@pytest

# Clean up
clean:
	@echo "Cleaning up..."
	@rm -rf venv
	@rm -rf __pycache__
	@rm -rf .pytest_cache
	@find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Cleanup complete"

# Show activation command
activate:
	@echo "To activate the virtual environment, run:"
	@echo "  source venv/bin/activate"

# Development Tools
dev-help:
	@echo "Development Tools Help"
	@echo "====================="
	@echo ""
	@echo "Event Operations:"
	@echo "  python dev_events.py list                      - List all events"
	@echo "  python dev_events.py get <event_id>            - Get event details"
	@echo "  python dev_events.py create <title>            - Create test event"
	@echo "  python dev_events.py create-samples [count]    - Create sample events"
	@echo "  python dev_events.py search <query>            - Search events"
	@echo "  python dev_events.py delete <event_id> --confirm - Delete event"
	@echo ""
	@echo "Ticket Operations:"
	@echo "  python dev_tickets.py rsvp <event_id>          - Create single RSVP"
	@echo "  python dev_tickets.py bulk-rsvp <event_id> [n] - Create N RSVPs"
	@echo "  python dev_tickets.py list-rsvps <event_id>    - List RSVPs"
	@echo "  python dev_tickets.py search-event <title>     - Search for event"
	@echo ""
	@echo "See DEV_TOOLS.md for complete documentation"

dev-list:
	@python dev_events.py list

dev-create:
	@python dev_events.py create "Test RSVP Event $$(date +%Y%m%d-%H%M%S)" 7 true RSVP
	@echo ""
	@echo "Created test RSVP event. Use 'make dev-list' to see all events."

dev-create-ticket:
	@python dev_events.py create "Test TICKETED Event $$(date +%Y%m%d-%H%M%S)" 7 true TICKETS
	@echo ""
	@echo "Created ticketed event with General Admission ticket ($25.00)."
	@echo "Use 'make dev-list' to see all events."

dev-samples:
	@python dev_events.py create-samples 5

dev-rsvp:
ifndef EVENT_ID
	@echo "Error: EVENT_ID required. Usage: make dev-rsvp EVENT_ID=abc123"
	@exit 1
endif
	@python dev_tickets.py rsvp $(EVENT_ID)

dev-bulk-rsvp:
ifndef EVENT_ID
	@echo "Error: EVENT_ID required. Usage: make dev-bulk-rsvp EVENT_ID=abc123"
	@exit 1
endif
	@python dev_tickets.py bulk-rsvp $(EVENT_ID) 10

dev-search:
ifndef QUERY
	@echo "Error: QUERY required. Usage: make dev-search QUERY='Workshop'"
	@exit 1
endif
	@python dev_events.py search "$(QUERY)"

# Cleanup commands
dev-clean-drafts:
	@echo "⚠️  This will delete ALL DRAFT events!"
	@echo "Press Ctrl+C to cancel, or wait 3 seconds to continue..."
	@sleep 3
	@python dev_events.py delete-drafts --confirm

dev-clean-test:
	@echo "⚠️  This will delete ALL events with 'Test' in the title!"
	@echo "Press Ctrl+C to cancel, or wait 3 seconds to continue..."
	@sleep 3
	@python dev_events.py delete-test --confirm

dev-clean-all:
	@echo "🚨 WARNING: This will delete ALL events in the system!"
	@echo "Press Ctrl+C to cancel, or wait 5 seconds to continue..."
	@sleep 5
	@python dev_events.py delete-pattern "" --confirm