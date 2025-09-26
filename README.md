# Wix Events + Google Sheets Auto-Sync

Automatically sync events from Google Sheets to your Wix Events website using GitHub Actions. Free, automated, and runs daily.

## What This Does

1. Reads events from a Google Sheet (your event planning spreadsheet)
2. Creates those events in Wix Events via API
3. Runs automatically every day at 9 AM EST
4. Costs $0/month (uses free tiers)

## Quick Start

1. **Setup (30 minutes)** - Follow [SETUP.md](SETUP.md) for detailed instructions
2. **Test locally** - Run `npm run test` to verify connections
3. **Deploy to GitHub** - Push to GitHub and configure secrets
4. **Automate** - Runs daily automatically, or trigger manually

## Commands

```bash
npm install          # Install dependencies
npm run test         # Test Wix API connection
npm run list         # List existing events in Wix
npm run sync         # Sync events from Google Sheets to Wix
```

## How It Works

- **Google Sheets API** reads your event spreadsheet using a service account
- **Wix Events API v2** creates events on your Wix site
- **GitHub Actions** runs the sync automatically (daily schedule + manual trigger)
- All credentials stored securely in GitHub Secrets

## Files

- `index.js` - Main sync script
- `.github/workflows/sync-events.yml` - GitHub Actions automation
- `SETUP.md` - Step-by-step setup guide
- `CHECKLIST.md` - Setup checklist

## Support

See [SETUP.md](SETUP.md) for troubleshooting and detailed configuration.

## Cost

**$0/month** - Uses:
- Google Sheets API (free tier: 100 requests/100 seconds)
- Wix Events API (free with Wix site)
- GitHub Actions (free tier: 2,000 minutes/month)

For ~20 events/day, you'll use < 1 minute/day of GitHub Actions.