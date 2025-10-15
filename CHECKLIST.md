# Setup Checklist - Python Version

Track your progress setting up the Wix Events + Google Sheets sync.

## Prerequisites

- [ ] Python 3.8+ installed
- [ ] Wix website with Events app
- [ ] Google account
- [ ] GitHub account

## Part 1: Google Setup

- [ ] Created Google Cloud Project
- [ ] Enabled Google Sheets API
- [ ] Created Service Account
- [ ] Downloaded credentials JSON file
- [ ] Created/prepared Google Sheet with correct columns
- [ ] Shared sheet with service account email
- [ ] Copied spreadsheet ID

## Part 2: Wix Setup

- [ ] Created app in Wix Developers
- [ ] Generated API key
- [ ] Copied Site ID
- [ ] Copied Account ID
- [ ] Added Events permissions

## Part 3: Local Setup

- [ ] Downloaded/cloned project files
- [ ] Ran setup script (setup.sh or setup.bat)
- [ ] Created .env file from .env.example
- [ ] Added Wix credentials to .env
- [ ] Added Google Sheet ID to .env
- [ ] Added Google credentials JSON to .env (one line)
- [ ] Activated virtual environment
- [ ] Installed Python dependencies

## Part 4: Testing

- [ ] Run: `python sync_events.py validate`
- [ ] All credentials validated successfully
- [ ] Run: `python sync_events.py test`
- [ ] Wix API connection successful
- [ ] Run: `python sync_events.py list`
- [ ] Can see existing Wix events (if any)
- [ ] Run: `python sync_events.py sync`
- [ ] First sync completed successfully

## Part 5: GitHub Actions

- [ ] Created GitHub repository
- [ ] Pushed code to GitHub
- [ ] Added WIX_API_KEY secret
- [ ] Added WIX_ACCOUNT_ID secret
- [ ] Added WIX_SITE_ID secret
- [ ] Added GOOGLE_SHEET_ID secret
- [ ] Added GOOGLE_CREDENTIALS secret (one line)
- [ ] Enabled GitHub Actions
- [ ] Manually triggered workflow
- [ ] Workflow ran successfully

## Verification

- [ ] Events from sheet appear in Wix
- [ ] Duplicate events are skipped
- [ ] Daily schedule is active (9 AM EST)
- [ ] Manual trigger works

## Notes

**Your Credentials:**
```
WIX_API_KEY: ________________________
WIX_SITE_ID: ________________________
WIX_ACCOUNT_ID: ______________________
GOOGLE_SHEET_ID: ____________________
Service Account Email: _______________
```

**Troubleshooting Log:**
```
Date: ________
Issue: _______
Solution: ____
```

## Success Metrics

- [ ] Syncs run daily without intervention
- [ ] New events sync automatically
- [ ] No duplicate events created
- [ ] Error notifications working (if configured)

---

**Setup completed on:** _______________

**Notes for future reference:**
_______________________________________
_______________________________________
_______________________________________