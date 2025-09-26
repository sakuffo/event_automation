# Setup Guide

Complete setup for Wix Events + Google Sheets sync in 30 minutes.

## Prerequisites

- A Wix website with Events app installed
- A Google account (for Google Sheets and Cloud Console)
- A GitHub account (for automation)
- Node.js installed locally (for testing)

---

## Part 1: Get Wix API Credentials (5 minutes)

### 1.1 Get Your API Key

1. Go to [Wix API Keys Dashboard](https://www.wix.com/my-account/api-keys)
2. Click **"Generate API Key"**
3. Name it: `Events Sync`
4. Select permissions:
   - ✅ **Wix Events** → Read and Write
5. Click **"Generate Key"**
6. **Copy and save the API key** (you won't see it again)

### 1.2 Get Your Site ID

1. Go to [Wix Dashboard](https://www.wix.com/my-account/sites)
2. Click on your site
3. Look at the URL - it will look like: `https://manage.wix.com/dashboard/abc123-def456.../`
4. The Site ID is the part between `/dashboard/` and the next `/`
5. Example: If URL is `https://manage.wix.com/dashboard/abc123-def456-ghi789/home`, your Site ID is `abc123-def456-ghi789`

### 1.3 Get Your Account ID

1. While logged into Wix, go to: https://www.wix.com/my-account
2. Click your profile icon (top right)
3. Click **"Account Settings"**
4. Your Account ID is shown at the top
   - OR look at the URL: `https://www.wix.com/my-account/account-id/YOUR_ACCOUNT_ID`

**Save these three values:**
- ✅ WIX_API_KEY
- ✅ WIX_SITE_ID  
- ✅ WIX_ACCOUNT_ID

---

## Part 2: Create Google Service Account (10 minutes)

### 2.1 Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **"Select a project"** → **"New Project"**
3. Name: `Wix Events Sync`
4. Click **"Create"**
5. Wait for the project to be created (notification will appear)
6. Select your new project from the dropdown

### 2.2 Enable Google Sheets API

1. In the search bar at top, type: `Google Sheets API`
2. Click **"Google Sheets API"** in results
3. Click **"Enable"**
4. Wait for it to enable (10-15 seconds)

### 2.3 Create Service Account

1. In left menu, click **"Credentials"**
2. Click **"+ Create Credentials"** → **"Service Account"**
3. Service account name: `wix-events-sync`
4. Service account ID: (auto-filled)
5. Click **"Create and Continue"**
6. Role: Select **"Editor"** (or skip this step)
7. Click **"Continue"** → **"Done"**

### 2.4 Create Service Account Key

1. Click on the service account you just created (in the list)
2. Go to **"Keys"** tab
3. Click **"Add Key"** → **"Create new key"**
4. Choose **"JSON"**
5. Click **"Create"**
6. A JSON file will download to your computer
7. **Save this file securely** - you'll need it next

### 2.5 Get Service Account Email

1. Open the downloaded JSON file
2. Find the `"client_email"` field
3. Copy the email (looks like: `wix-events-sync@your-project.iam.gserviceaccount.com`)

**Save:**
- ✅ Service account JSON file contents (entire file)
- ✅ Service account email

---

## Part 3: Prepare Google Sheet (5 minutes)

### 3.1 Create Event Spreadsheet

1. Go to [Google Sheets](https://sheets.google.com)
2. Create a new spreadsheet or use existing one
3. Name the first sheet: `Sheet1` (or update `config.google.range` in index.js)

### 3.2 Set Up Columns (Row 1)

Add these exact column headers in row 1:

| A | B | C | D | E | F | G | H | I | J | K |
|---|---|---|---|---|---|---|---|---|---|---|
| Event Name | Event Type | Start Date | Start Time | End Date | End Time | Location | Description | Ticket Price | Capacity | Registration Type |

### 3.3 Add Sample Event (Row 2)

```
Event Name: Movie Night
Event Type: TICKETS
Start Date: 2025-11-01
Start Time: 19:00
End Date: 2025-11-01
End Time: 22:00
Location: 123 Main St, Toronto
Description: Weekly community movie screening
Ticket Price: 15.00
Capacity: 50
Registration Type: TICKETS
```

### 3.4 Share Sheet with Service Account

1. Click **"Share"** button (top right)
2. Paste the **service account email** (from Part 2.5)
3. Permission: **"Viewer"** (read-only is fine)
4. Uncheck **"Notify people"**
5. Click **"Share"**

### 3.5 Get Spreadsheet ID

1. Look at the URL of your Google Sheet
2. Format: `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`
3. Copy the `SPREADSHEET_ID` part (between `/d/` and `/edit`)

**Save:**
- ✅ GOOGLE_SHEET_ID

---

## Part 4: Local Testing (5 minutes)

### 4.1 Install Dependencies

```bash
cd event_automation
npm install
```

### 4.2 Create .env File

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your values:
   ```
   WIX_API_KEY=your_actual_api_key
   WIX_ACCOUNT_ID=your_actual_account_id
   WIX_SITE_ID=your_actual_site_id
   GOOGLE_SHEET_ID=your_actual_spreadsheet_id
   GOOGLE_CREDENTIALS={"type":"service_account",...}
   ```

3. For `GOOGLE_CREDENTIALS`:
   - Open the service account JSON file you downloaded
   - Copy the **entire contents** (all the JSON)
   - **Minify it to one line** (remove line breaks)
   - Paste into .env file as value for GOOGLE_CREDENTIALS

### 4.3 Test Connection

```bash
npm run test
```

You should see: `✅ Wix API connection successful!`

If you get an error:
- Check your WIX_API_KEY is correct
- Check your WIX_SITE_ID is correct
- Make sure Wix Events app is installed on your site

### 4.4 List Existing Events

```bash
npm run list
```

This shows events currently in Wix (if any).

### 4.5 Run First Sync

```bash
npm run sync
```

You should see:
```
🚀 Starting Google Sheets → Wix Events sync...
📊 Fetching events from Google Sheets...
Found 1 events in spreadsheet

📅 Creating events in Wix...
✅ Created event: Movie Night

📈 Sync Complete!
✅ Successfully created: 1 events
  • Movie Night
```

### 4.6 Verify in Wix

1. Go to your Wix Dashboard
2. Click **"Events"** in left menu
3. You should see your new event!

---

## Part 5: Deploy to GitHub (5 minutes)

### 5.1 Create GitHub Repository

1. Go to [GitHub](https://github.com/new)
2. Name: `wix-events-sync` (or anything you want)
3. Visibility: **Private** (recommended to keep credentials secure)
4. Don't initialize with README (you already have files)
5. Click **"Create repository"**

### 5.2 Push Your Code

```bash
cd event_automation
git init
git add .
git commit -m "Initial commit: Wix Events sync"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/wix-events-sync.git
git push -u origin main
```

### 5.3 Add GitHub Secrets

1. Go to your GitHub repository
2. Click **"Settings"** tab
3. In left menu, click **"Secrets and variables"** → **"Actions"**
4. Click **"New repository secret"** for each:

   **Secret 1:**
   - Name: `WIX_API_KEY`
   - Value: (paste your Wix API key)
   - Click **"Add secret"**

   **Secret 2:**
   - Name: `WIX_ACCOUNT_ID`
   - Value: (paste your Wix account ID)
   - Click **"Add secret"**

   **Secret 3:**
   - Name: `WIX_SITE_ID`
   - Value: (paste your Wix site ID)
   - Click **"Add secret"**

   **Secret 4:**
   - Name: `GOOGLE_SHEET_ID`
   - Value: (paste your Google Sheet ID)
   - Click **"Add secret"**

   **Secret 5:**
   - Name: `GOOGLE_CREDENTIALS`
   - Value: (paste the **entire minified JSON** from your service account file)
   - Click **"Add secret"**

### 5.4 Test GitHub Action

1. Go to **"Actions"** tab in your repository
2. Click **"Sync Events from Google Sheets to Wix"** workflow
3. Click **"Run workflow"** dropdown (right side)
4. Click green **"Run workflow"** button
5. Wait 30-60 seconds
6. Click on the running workflow to see logs
7. Should show: `✅ Event sync completed successfully`

---

## Daily Automation

The workflow runs automatically **every day at 9 AM EST** (2 PM UTC).

To change the schedule:
1. Edit `.github/workflows/sync-events.yml`
2. Modify the cron expression: `cron: '0 14 * * *'`
3. Use [crontab.guru](https://crontab.guru/) to create custom schedules

---

## Updating Events

1. Edit your Google Sheet (add/remove rows)
2. Wait for daily sync at 9 AM EST
   - OR manually trigger in GitHub Actions (see 5.4 above)
3. Check Wix Dashboard to see new events

**Note:** This creates new events each time. It does NOT update existing events. To avoid duplicates, delete old events from Wix before syncing new ones.

---

## Troubleshooting

### "Wix API connection failed"
- Verify API key is correct (no extra spaces)
- Check Wix Events app is installed on your site
- Ensure API key has "Wix Events" permissions

### "No data found in spreadsheet"
- Check Sheet name is `Sheet1` (or update range in index.js)
- Verify service account has access to sheet (check Share settings)
- Make sure data starts in row 2 (row 1 is headers)

### "Failed to create event"
- Check date format: `YYYY-MM-DD` (e.g., `2025-11-01`)
- Check time format: `HH:MM` in 24-hour format (e.g., `19:00`)
- Ensure Registration Type is one of: `TICKETS`, `RSVP`, `EXTERNAL`, `NO_REGISTRATION`
- Dates must be in the future

### GitHub Action fails
- Check all 5 secrets are added correctly
- Verify GOOGLE_CREDENTIALS is valid JSON (use a JSON validator)
- Check GitHub Actions logs for specific error messages

### How to re-minify JSON credentials

```bash
# On Linux/Mac/WSL:
cat credentials.json | jq -c

# Or use online tool:
# https://jsonformatter.org/json-minify
```

---

## Advanced Configuration

### Change Timezone

Edit `index.js` line 85:
```javascript
timeZone: 'America/Toronto',  // Change to your timezone
```

Common timezones:
- `America/New_York` (EST/EDT)
- `America/Chicago` (CST/CDT)
- `America/Los_Angeles` (PST/PDT)
- `America/Toronto` (EST/EDT)
- [Full list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)

### Change Venue Name

Edit `index.js` line 90:
```javascript
name: 'Birdhaus',  // Change to your venue name
```

### Read Different Sheet Range

Edit `index.js` line 17:
```javascript
range: 'Sheet1!A2:K100',  // Change sheet name or range
```

Examples:
- `'Events!A2:K200'` - Read from "Events" sheet, up to 200 rows
- `'Sheet1!A2:K50'` - Read only 50 rows

---

## Security Best Practices

1. ✅ **Never commit .env file** (it's in .gitignore)
2. ✅ **Use Private GitHub repository** for extra security
3. ✅ **Rotate API keys** every 6-12 months
4. ✅ **Give service account minimum permissions** (Viewer on Sheet)
5. ✅ **Review GitHub Actions logs** occasionally for errors

---

## Cost Breakdown

| Service | Free Tier Limit | Your Usage | Cost |
|---------|----------------|------------|------|
| Google Sheets API | 100 requests/100 sec | 1 request/day | $0 |
| Wix Events API | Unlimited with site | 20 events/day | $0 |
| GitHub Actions | 2,000 min/month | ~30 min/month | $0 |

**Total: $0/month**

---

## Next Steps

Once setup is complete:

1. Update your Google Sheet with real events
2. Manually trigger sync to test
3. Let it run automatically every day at 9 AM EST
4. Check Wix Dashboard to verify events appear

For a quick checklist, see [CHECKLIST.md](CHECKLIST.md).