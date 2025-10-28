# Setup Guide - Wix Events + Google Sheets Sync (Python)

Complete step-by-step guide to set up automated event syncing from Google Sheets to Wix Events.

## Prerequisites

- Python 3.8+ installed on your computer
- Wix website with Events app installed
- Google account for Sheets and Cloud Console
- GitHub account (free) for automation

## Part 1: Google Cloud Setup (15 minutes)

### 1.1 Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Name it: `wix-events-sync`
4. Click "Create"

### 1.2 Enable Required APIs

1. In your project, go to "APIs & Services" → "Library"
2. Search for and enable each of these APIs:
   - **Google Sheets API** - to read event data
   - **Google Drive API** - to download event images
3. Click on each and press "Enable"

### 1.3 Create Service Account

1. Go to "APIs & Services" → "Credentials"
2. Click "+ Create Credentials" → "Service account"
3. Service account details:
   - Name: `wix-events-sync`
   - ID: (auto-generated)
   - Description: "Reads event data from Google Sheets"
4. Click "Create and Continue"
5. Skip optional steps (Grant access, Grant users)
6. Click "Done"

### 1.4 Download Credentials

1. Click on your service account email
2. Go to "Keys" tab
3. Click "Add Key" → "Create new key"
4. Choose "JSON" format
5. Click "Create" (file downloads automatically)
6. **Save this file safely** - you'll need it later

### 1.5 Prepare Google Sheet

1. Create a new Google Sheet or use existing one
2. Set up columns A-L with these headers:
   ```
   Event Name | Event Type | Start Date | Start Time | End Date | End Time | Location | Description | Ticket Price | Capacity | Registration Type | Image URL
   ```
3. Copy the spreadsheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/[SPREADSHEET_ID]/edit
   ```

### 1.6 Prepare Event Images (Optional)

1. Upload event images to Google Drive
2. For each image file:
   - Right-click → "Share"
   - Change to "Anyone with the link" can view
   - Click "Copy link"
3. Paste the Google Drive link in Column L of your spreadsheet
4. Supported formats:
   - Full URL: `https://drive.google.com/file/d/FILE_ID/view`
   - Short URL: `https://drive.google.com/open?id=FILE_ID`
   - Just the file ID: `FILE_ID`

**IMPORTANT:** The service account needs access to your Google Drive images:
- Share each image file (or parent folder) with the service account email
- OR make images publicly accessible with "Anyone with the link"

### 1.7 Share Sheet with Service Account

1. Open your Google Sheet
2. Click "Share" button
3. Paste the service account email (from credentials JSON)
4. Set permission to "Viewer"
5. Click "Send"

## Part 2: Wix API Setup (10 minutes)

### 2.1 Get Wix API Key

1. Go to [Wix Developers](https://dev.wix.com/)
2. Sign in with your Wix account
3. Click "My Apps" → "Create New App"
4. Choose "Website / Business App"
5. Name it: `Event Sync`
6. Go to "Permissions" tab
7. Add these permissions:
   - Wix Events: Read and Manage
8. Go to "API Keys" tab
9. Click "Generate API Key"
10. **Copy and save the API key** (shown only once)

### 2.2 Get Site ID

1. In Wix Dashboard, go to "Settings" → "Website Settings"
2. Look for "Site ID" or find it in the URL when editing your site
3. Copy the Site ID

### 2.3 Get Account ID

1. In Wix Dashboard, go to "Settings"
2. Find your Account ID (usually in billing or account section)
3. Copy the Account ID

## Part 3: Local Setup (10 minutes)

### 3.1 Clone or Download Project

```bash
# Clone from GitHub (if available)
git clone https://github.com/yourusername/wix-events-sync.git
cd wix-events-sync

# Or create new directory
mkdir wix-events-sync
cd wix-events-sync
```

### 3.2 Run Setup Script

**Unix/Linux/Mac:**
```bash
chmod +x setup.sh
./setup.sh
```

**Windows:**
```cmd
setup.bat
```

**Or using Make:**
```bash
make setup
```

### 3.3 Configure Environment Variables

1. Open `.env` file (created from `.env.example`)
2. Add your credentials:

```bash
# Wix Credentials
WIX_API_KEY=your_api_key_from_step_2.1
WIX_ACCOUNT_ID=your_account_id_from_step_2.3
WIX_SITE_ID=your_site_id_from_step_2.2

# Google Sheets
GOOGLE_SHEET_ID=your_spreadsheet_id_from_step_1.5
```

3. For `GOOGLE_CREDENTIALS`:
   - Open the JSON file downloaded in step 1.4
   - Copy the entire contents
   - Paste as a single line after `GOOGLE_CREDENTIALS=`
   - Make sure it's all on ONE line (remove line breaks)

Example:
```bash
GOOGLE_CREDENTIALS={"type":"service_account","project_id":"...","private_key":"..."}
```

### 3.4 Test the Setup

```bash
# Activate virtual environment (if not already active)
source venv/bin/activate  # Unix/Mac
# OR
venv\Scripts\activate.bat  # Windows

# Validate credentials
python sync_events.py validate

# Test Wix connection
python sync_events.py test

# List existing events
python sync_events.py list

# Run your first sync
python sync_events.py sync
```

## Part 4: GitHub Actions Setup (10 minutes)

### 4.1 Create GitHub Repository

1. Go to [GitHub](https://github.com/)
2. Click "+" → "New repository"
3. Name: `wix-events-sync`
4. Set to Private (recommended)
5. Create repository

### 4.2 Push Code to GitHub

```bash
git init
git add .
git commit -m "Initial setup"
git branch -M main
git remote add origin https://github.com/yourusername/wix-events-sync.git
git push -u origin main
```

### 4.3 Add GitHub Secrets

1. Go to your repository on GitHub
2. Click "Settings" → "Secrets and variables" → "Actions"
3. Add these secrets (click "New repository secret" for each):

   - **WIX_API_KEY**: Your Wix API key
   - **WIX_ACCOUNT_ID**: Your Wix account ID
   - **WIX_SITE_ID**: Your Wix site ID
   - **GOOGLE_SHEET_ID**: Your spreadsheet ID
   - **GOOGLE_CREDENTIALS**: Entire JSON content (one line)

### 4.4 Enable GitHub Actions

1. Go to "Actions" tab in your repository
2. If prompted, enable Actions for this repository
3. You should see "Sync Events from Google Sheets to Wix"
4. Test it: Click "Run workflow" → "Run workflow"

## Part 5: Verify Everything Works

### 5.1 Check Automated Schedule

- The sync runs automatically at 9 AM EST daily
- Check `.github/workflows/sync-events.yml` to modify schedule
- Cron expression: `0 14 * * *` (14:00 UTC = 9 AM EST)

### 5.2 Manual Trigger

1. Go to GitHub repository → "Actions"
2. Click "Sync Events from Google Sheets to Wix"
3. Click "Run workflow"
4. Check the logs for success

### 5.3 Monitor Syncs

- GitHub Actions tab shows all sync history
- Green checkmark = successful sync
- Red X = failed sync (check logs)

## Troubleshooting

### Common Issues

**"Google Sheets API error"**
- Verify service account has access to sheet
- Check GOOGLE_CREDENTIALS is valid JSON
- Ensure Sheets API is enabled

**"Wix API connection failed"**
- Verify API key is correct
- Check Site ID matches your Wix site
- Ensure Events app is installed on Wix

**"No data found in spreadsheet"**
- Check sheet has data starting from row 2
- Verify GOOGLE_SHEET_ID is correct
- Ensure date format is YYYY-MM-DD

**GitHub Actions fails**
- Check all secrets are set correctly
- Verify GOOGLE_CREDENTIALS is one line
- Look at workflow logs for specific error

### Getting Help

1. Check error messages in console
2. Review GitHub Actions logs
3. Verify all credentials are correct
4. Ensure spreadsheet format matches requirements

## Data Format Reference

### Spreadsheet Columns

| Column | Field | Format | Example |
|--------|-------|--------|----------|
| A | Event Name | Text | "Workshop 2024" |
| B | Event Type | Text | "Workshop" |
| C | Start Date | YYYY-MM-DD | "2024-03-15" |
| D | Start Time | HH:MM | "14:00" |
| E | End Date | YYYY-MM-DD | "2024-03-15" |
| F | End Time | HH:MM | "16:00" |
| G | Location | Text | "Room 101, Main Building" |
| H | Description | Text | "Learn the basics..." |
| I | Ticket Price | Number | "0" or "25.00" |
| J | Capacity | Number | "30" |
| K | Registration Type | Text | "RSVP" |
| L | Image URL | Text | "https://drive.google.com/file/d/ABC123..." |

### Registration Types

- `RSVP` - Free RSVP registration
- `TICKETS` - Creates ticketed event (shows "Tickets are not on sale")
  - Event is created with TICKETING registration type
  - Add tickets manually via Wix Dashboard after creation
  - Registration type **cannot** be changed after event creation
- `EXTERNAL` - External registration platform
- `NO_REGISTRATION` - Display-only events

### Event Images

- **Column L** contains a Google Drive link to the event image
- Accepted formats:
  - Full URL: `https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9/view`
  - Short URL: `https://drive.google.com/open?id=1A2B3C4D5E6F7G8H9`
  - Just file ID: `1A2B3C4D5E6F7G8H9`
- Supported image formats: JPG, JPEG, PNG, GIF, WebP
- Leave blank if no image needed
- **Must share with service account or set to "Anyone with the link"**

## Maintenance

### Update Dependencies

```bash
pip install --upgrade -r requirements.txt
```

### Modify Schedule

Edit `.github/workflows/sync-events.yml`:
```yaml
schedule:
  - cron: '0 14 * * *'  # Daily at 2 PM UTC
```

### View Logs

```bash
# Local logs
python sync_events.py sync > sync.log 2>&1

# GitHub Actions logs
# Go to Actions tab → Click on workflow run
```

## Security Notes

- Never commit `.env` file to Git
- Keep API keys secret
- Use GitHub Secrets for production
- Regularly rotate API keys
- Set repository to private if possible

## Next Steps

1. Add more events to your Google Sheet
2. Customize event types and fields
3. Set up error notifications (optional)
4. Create backup of credentials
5. Document your specific setup

Congratulations! Your automated event sync is now running.