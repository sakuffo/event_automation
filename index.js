// Wix Events + Google Sheets Integration PoC
// Prerequisites: npm install axios googleapis dotenv

const axios = require('axios');
const { google } = require('googleapis');
require('dotenv').config();

// Configuration (store these in .env file)
const config = {
    wix: {
        apiKey: process.env.WIX_API_KEY,
        accountId: process.env.WIX_ACCOUNT_ID,
        siteId: process.env.WIX_SITE_ID,
    },
    google: {
        spreadsheetId: process.env.GOOGLE_SHEET_ID,
        range: 'Sheet1!A2:K100',
        credentials: (() => {
            try {
                return JSON.parse(process.env.GOOGLE_CREDENTIALS || '{}');
            } catch (e) {
                console.error('❌ GOOGLE_CREDENTIALS is not valid JSON');
                console.error('Error:', e.message);
                return {};
            }
        })()
    }
};

// ============= GOOGLE SHEETS FUNCTIONS =============

async function authenticateGoogleSheets() {
    const auth = new google.auth.GoogleAuth({
        credentials: config.google.credentials,
        scopes: ['https://www.googleapis.com/auth/spreadsheets.readonly'],
    });
    
    const sheets = google.sheets({ version: 'v4', auth });
    return sheets;
}

async function fetchEventsFromSheet() {
    try {
        const sheets = await authenticateGoogleSheets();
        
        const response = await sheets.spreadsheets.values.get({
            spreadsheetId: config.google.spreadsheetId,
            range: config.google.range,
        });
        
        const rows = response.data.values;
        if (!rows || rows.length === 0) {
            console.log('No data found in spreadsheet.');
            return [];
        }
        
        // Map spreadsheet rows to event objects
        const events = rows.map(row => {
            let regType = row[10] || 'RSVP';
            if (regType === 'TICKETS') {
                console.log(`⚠️  Note: "${row[0]}" uses TICKETS - converting to RSVP (ticket pricing must be set up in Wix Dashboard)`);
                regType = 'RSVP';
            }
            return {
                name: row[0],
                eventType: row[1],
                startDate: row[2],
                startTime: row[3],
                endDate: row[4],
                endTime: row[5],
                location: row[6],
                description: row[7],
                ticketPrice: parseFloat(row[8]) || 0,
                capacity: parseInt(row[9]) || 100,
                registrationType: regType
            };
        });
        
        return events;
    } catch (error) {
        console.error('Error fetching from Google Sheets:', error);
        throw error;
    }
}

// ============= WIX EVENTS API FUNCTIONS =============

async function createWixEvent(eventData) {
    const url = `https://www.wixapis.com/events/v3/events`;

    const wixEventPayload = {
        event: {
            title: eventData.name,
            dateAndTimeSettings: {
                dateAndTimeTbd: false,
                startDate: `${eventData.startDate}T${eventData.startTime}:00Z`,
                endDate: `${eventData.endDate}T${eventData.endTime}:00Z`,
                timeZoneId: 'America/Toronto'
            },
            location: {
                type: 'VENUE',
                address: {
                    formattedAddress: eventData.location
                }
            },
            registration: {
                initialType: eventData.registrationType
            },
            draft: false
        }
    };

    try {
        const response = await axios.post(url, wixEventPayload, {
            headers: {
                'Authorization': config.wix.apiKey,
                'wix-site-id': config.wix.siteId,
                'Content-Type': 'application/json',
            }
        });

        console.log(`✅ Created event: ${eventData.name}`);
        return response.data;
    } catch (error) {
        console.error(`❌ Failed to create event ${eventData.name}:`, error.response?.data || error.message);
        console.error(`[DEBUG] Request URL: ${url}`);
        console.error(`[DEBUG] Headers:`, JSON.stringify({
            'Authorization': config.wix.apiKey?.substring(0, 20) + '...',
            'wix-site-id': config.wix.siteId
        }));
        console.error(`[DEBUG] Payload sent:`, JSON.stringify(wixEventPayload));
        throw error;
    }
}

async function createTicketDefinition(eventId, eventData) {
    if (eventData.registrationType !== 'TICKETS') return;

    const url = `https://www.wixapis.com/events/v2/ticket-definitions`;

    const ticketPayload = {
        ticketDefinition: {
            eventId: eventId,
            name: 'General Admission',
            price: {
                value: eventData.ticketPrice.toString(),
                currency: 'CAD'
            },
            limitPerOrder: 10,
            capacity: eventData.capacity,
            salePeriod: {
                startDate: new Date().toISOString(),
                endDate: `${eventData.startDate}T${eventData.startTime}:00.000Z`
            }
        }
    };
    
    try {
        const response = await axios.post(url, ticketPayload, {
            headers: {
                'Authorization': config.wix.apiKey,
                'wix-site-id': config.wix.siteId,
                'Content-Type': 'application/json',
            }
        });
        
        console.log(`  📎 Added ticket: $${eventData.ticketPrice} CAD`);
        return response.data;
    } catch (error) {
        console.error(`  ❌ Failed to create ticket for ${eventData.name}:`, error.response?.data || error.message);
    }
}

// ============= MAIN EXECUTION =============

async function getExistingEvents() {
    const url = `https://www.wixapis.com/events/v3/events/query`;

    try {
        const response = await axios.post(url,
            { query: { paging: { limit: 100 } } },
            {
                headers: {
                    'Authorization': config.wix.apiKey,
                    'wix-site-id': config.wix.siteId,
                    'Content-Type': 'application/json',
                }
            }
        );
        return response.data.events || [];
    } catch (error) {
        console.error('Warning: Could not fetch existing events:', error.message);
        return [];
    }
}

async function syncEventsFromSheetToWix() {
    console.log('🚀 Starting Google Sheets → Wix Events sync...\n');

    try {
        console.log('📊 Fetching events from Google Sheets...');
        const events = await fetchEventsFromSheet();
        console.log(`Found ${events.length} events in spreadsheet\n`);

        console.log('🔍 Checking for existing events in Wix...');
        const existingEvents = await getExistingEvents();
        const existingKeys = new Set(existingEvents.map(e => {
            const startDate = e.dateAndTimeSettings?.startDate || '';
            return `${e.title}|${startDate.split('T')[0]}`;
        }));
        console.log(`Found ${existingEvents.length} existing events\n`);

        console.log('📅 Creating new events in Wix...\n');
        const results = {
            success: [],
            failed: [],
            skipped: []
        };

        for (const event of events) {
            const eventKey = `${event.name}|${event.startDate}`;
            if (existingKeys.has(eventKey)) {
                console.log(`⏭️  Skipped: ${event.name} on ${event.startDate} (already exists)`);
                results.skipped.push(event.name);
                continue;
            }

            try {
                const createdEvent = await createWixEvent(event);

                if (event.registrationType === 'TICKETS') {
                    await createTicketDefinition(createdEvent.event.id, event);
                }

                results.success.push(event.name);

                await new Promise(resolve => setTimeout(resolve, 1000));

            } catch (error) {
                results.failed.push({ name: event.name, error: error.message });
            }
        }
        
        console.log('\n📈 Sync Complete!\n');
        console.log(`✅ Successfully created: ${results.success.length} events`);
        if (results.success.length > 0) {
            results.success.forEach(name => console.log(`  • ${name}`));
        }

        if (results.skipped.length > 0) {
            console.log(`\n⏭️  Skipped (already exist): ${results.skipped.length} events`);
            results.skipped.forEach(name => console.log(`  • ${name}`));
        }

        if (results.failed.length > 0) {
            console.log(`\n❌ Failed: ${results.failed.length} events`);
            results.failed.forEach(item => console.log(`  • ${item.name}: ${item.error}`));
        }
        
    } catch (error) {
        console.error('Fatal error during sync:', error);
    }
}

// ============= UTILITY FUNCTIONS =============

async function validateCredentials() {
    console.log('🔍 Validating credentials and configuration...\n');

    let allValid = true;

    if (!config.wix.apiKey) {
        console.error('❌ WIX_API_KEY is missing');
        allValid = false;
    } else {
        console.log('✅ WIX_API_KEY is set');
    }

    if (!config.wix.siteId) {
        console.error('❌ WIX_SITE_ID is missing');
        allValid = false;
    } else {
        console.log('✅ WIX_SITE_ID is set');
    }

    if (!config.wix.accountId) {
        console.error('❌ WIX_ACCOUNT_ID is missing');
        allValid = false;
    } else {
        console.log('✅ WIX_ACCOUNT_ID is set');
    }

    if (!config.google.spreadsheetId) {
        console.error('❌ GOOGLE_SHEET_ID is missing');
        allValid = false;
    } else {
        console.log('✅ GOOGLE_SHEET_ID is set');
    }

    if (!config.google.credentials.client_email) {
        console.error('❌ GOOGLE_CREDENTIALS is missing or invalid');
        console.error('   Make sure it contains valid JSON with client_email field');
        allValid = false;
    } else {
        console.log('✅ GOOGLE_CREDENTIALS is valid JSON');
        console.log(`   Service account: ${config.google.credentials.client_email}`);
    }

    console.log('');

    if (allValid) {
        console.log('✅ All credentials are configured correctly!\n');
        console.log('Next steps:');
        console.log('  1. Run: npm run test');
        console.log('  2. Run: npm run sync');
    } else {
        console.log('❌ Some credentials are missing or invalid. Check .env file.\n');
    }

    return allValid;
}

async function testWixConnection() {
    const url = `https://www.wixapis.com/events/v3/events/query`;
    
    try {
        const response = await axios.post(url, 
            { query: { limit: 1 } },
            {
                headers: {
                    'Authorization': config.wix.apiKey,
                    'wix-site-id': config.wix.siteId,
                    'Content-Type': 'application/json',
                }
            }
        );
        console.log('✅ Wix API connection successful!');
        return true;
    } catch (error) {
        console.error('❌ Wix API connection failed:', error.response?.data || error.message);
        return false;
    }
}

// List existing events (useful for checking what's already created)
async function listExistingEvents() {
    const url = `https://www.wixapis.com/events/v3/events/query`;

    try {
        const response = await axios.post(url,
            {
                query: {
                    paging: { limit: 50 }
                }
            },
            {
                headers: {
                    'Authorization': config.wix.apiKey,
                    'wix-site-id': config.wix.siteId,
                    'Content-Type': 'application/json',
                }
            }
        );
        
        console.log('\n📅 Existing Events in Wix:\n');
        response.data.events?.forEach(event => {
            console.log(`  • ${event.title} - ${event.dateAndTimeSettings?.startDate || 'No date'}`);
        });
        
        return response.data.events;
    } catch (error) {
        console.error('Failed to list events:', error.response?.data || error.message);
    }
}

// ============= RUN THE SYNC =============

// Command line interface
const command = process.argv[2];

switch(command) {
    case 'test':
        testWixConnection();
        break;
    case 'list':
        listExistingEvents();
        break;
    case 'sync':
        syncEventsFromSheetToWix();
        break;
    case 'validate':
        validateCredentials();
        break;
    default:
        console.log(`
Wix Events + Google Sheets Integration

Usage:
  node index.js test      - Test Wix API connection
  node index.js list      - List existing events in Wix
  node index.js sync      - Sync events from Google Sheets to Wix
  node index.js validate  - Validate all credentials and configuration

Setup:
1. Create a .env file with:
   WIX_API_KEY=your_api_key
   WIX_ACCOUNT_ID=your_account_id
   WIX_SITE_ID=your_site_id
   GOOGLE_SHEET_ID=your_spreadsheet_id
   GOOGLE_CREDENTIALS={"type":"service_account",...}

2. Install dependencies:
   npm install

3. Run the sync:
   node index.js sync
        `);
}