# Setup Checklist

Use this checklist to track your setup progress. Estimated time: **30 minutes**.

---

## ☐ Part 1: Wix Credentials (5 min)

- [ ] Get Wix API Key from https://www.wix.com/my-account/api-keys
  - [ ] Name: "Events Sync"
  - [ ] Permission: Wix Events (Read & Write)
  - [ ] Copy and save API key
- [ ] Get Wix Site ID from dashboard URL
- [ ] Get Wix Account ID from account settings
- [ ] Save all three values

---

## ☐ Part 2: Google Cloud Setup (10 min)

- [ ] Create Google Cloud project at https://console.cloud.google.com/
  - [ ] Name: "Wix Events Sync"
- [ ] Enable Google Sheets API
  - [ ] Search for "Google Sheets API"
  - [ ] Click "Enable"
- [ ] Create service account
  - [ ] Name: "wix-events-sync"
  - [ ] Role: Editor (or skip)
- [ ] Create service account key (JSON)
  - [ ] Download JSON file
  - [ ] Save file securely
- [ ] Copy service account email from JSON file

---

## ☐ Part 3: Google Sheet (5 min)

- [ ] Create/open Google Sheet
- [ ] Add column headers in row 1:
  ```
  Event Name | Event Type | Start Date | Start Time | End Date | End Time | Location | Description | Ticket Price | Capacity | Registration Type
  ```
- [ ] Add sample event in row 2
- [ ] Share sheet with service account email
  - [ ] Permission: Viewer
  - [ ] Uncheck "Notify people"
- [ ] Copy Spreadsheet ID from URL

---

## ☐ Part 4: Local Testing (5 min)

- [ ] Clone/download this repository
- [ ] Run `npm install`
- [ ] Create `.env` file
  - [ ] Copy from `.env.example`
  - [ ] Add WIX_API_KEY
  - [ ] Add WIX_ACCOUNT_ID
  - [ ] Add WIX_SITE_ID
  - [ ] Add GOOGLE_SHEET_ID
  - [ ] Add GOOGLE_CREDENTIALS (minified JSON)
- [ ] Test Wix connection: `npm run test`
- [ ] List events: `npm run list`
- [ ] Run sync: `npm run sync`
- [ ] Verify event appears in Wix Dashboard

---

## ☐ Part 5: GitHub Deployment (5 min)

- [ ] Create new GitHub repository (Private recommended)
- [ ] Push code to GitHub:
  ```bash
  git init
  git add .
  git commit -m "Initial commit"
  git branch -M main
  git remote add origin https://github.com/YOUR_USERNAME/REPO_NAME.git
  git push -u origin main
  ```
- [ ] Add GitHub Secrets (Settings → Secrets → Actions):
  - [ ] WIX_API_KEY
  - [ ] WIX_ACCOUNT_ID
  - [ ] WIX_SITE_ID
  - [ ] GOOGLE_SHEET_ID
  - [ ] GOOGLE_CREDENTIALS (entire JSON, minified)
- [ ] Test workflow:
  - [ ] Go to "Actions" tab
  - [ ] Click "Sync Events from Google Sheets to Wix"
  - [ ] Click "Run workflow"
  - [ ] Wait for completion
  - [ ] Check logs for success message

---

## ✅ Done!

Your automation is now:
- ✅ Running automatically every day at 9 AM EST
- ✅ Available for manual trigger anytime
- ✅ Costing $0/month

### Daily Workflow:

1. Update Google Sheet with new events
2. Wait for 9 AM EST sync (or trigger manually)
3. Events appear in Wix automatically

---

## Quick Reference

**Test locally:**
```bash
npm run test   # Test connection
npm run list   # List events  
npm run sync   # Sync events
```

**Trigger manually:**
1. GitHub repo → Actions tab
2. Click workflow → Run workflow

**View logs:**
1. GitHub repo → Actions tab
2. Click on latest run
3. Expand steps to see details