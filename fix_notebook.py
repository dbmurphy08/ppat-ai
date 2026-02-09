import json
import os

file_path = 'Bar_Owner_Agent/agent.ipynb'

# The complete source code for the notebook cell
source_code = r"""import os
import datetime
import caldav
from datetime import timedelta, timezone
import dateutil.parser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google import genai
from google.genai import types

# Import your secrets
try:
    import secrets_config
except ImportError:
    print("ERROR: secrets_config.py not found. Please create it.")
    secrets_config = None

# --- CONFIGURATION ---
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.readonly' 
]
GOOGLE_ACCOUNTS = ['bar', 'manager'] 
# --- CLASS: AI ASSISTANT ---
class AIAssistant:
    def __init__(self, api_key):
        self.client = genai.Client(api_key=api_key)

    def generate_briefing(self, calendar_events, emails):
        print("Sending data to Gemini...")
        
        # 1. Prepare Data Payload
        current_time = datetime.datetime.now().strftime("%A, %B %d, %I:%M %p")
        data_feed = f"CURRENT TIME: {current_time}\n\n"
        
        data_feed += "--- CALENDAR (NEXT 48 HOURS) ---\n"
        for event in calendar_events:
            # AI only needs to see immediate future for "Battle Plan" context
            if event['sort_key'] < datetime.datetime.now() + timedelta(days=2):
                data_feed += f"- {event['detail']} : {event['title']} [{event['source']}]\n"
        
        data_feed += "\n--- RECENT EMAILS ---\n"
        for email in emails:
            data_feed += f"- From: {email['detail']} | Subject: {email['title']} | Snippet: {email['snippet']}\n"

        # 2. System Prompt (Prioritized)
        system_instruction = """
        You are the owner and creative visionary of **Patterson Park Patio Bar** in Houston, Texas. 
        You are creative and appeal to an upper-middle-class demographic (ages 23-39) in the Heights/Rice Military area. 
        You make all decisions regarding operations, including event planning, scheduling, and high-level strategy.
        
        **Your Goal:** 
        Review the raw data (calendar/emails) to provide specific instructions for managers and employees for the immediate future. 
        Balance operational rigor with creative flair (party ideas, decor, menu specials).
        
        
        ## 🔮 THE FORECAST & STRATEGY
        (Synthesize weather + calendar. Predict crowd size: Low/Med/High. Define the "Vibe" for the day/night. include music types, volume levels, lighting for both day, happy hour, and night.)
        
        ## 📋 MANAGER ORDERS (Logistics & Ops)
        (Specific tasks: Inventory needs, repair orders, staffing adjustments, VIP table management.)
        
        ## 🎨 CREATIVE DIRECTIVE (Events & Promo)
        - **Today/Tomorrow:** Daily specials, music selection, lighting cues.
        - **This Week:** Upcoming weekend themes, social media hooks.
        - **Future:** Ideas for parties, decorations, or menu changes based on what you see in the calendar. 
        
        ## ⚡ ACTION ITEMS (FROM EMAILS)
        (Scan email snippets for tasks like "please send," "confirm," "sign," or deadlines.)
        - [ ] Task 1
        - [ ] Task 2
        
        ## 📣 PRE-SHIFT RALLY (Staff Instructions)
        (Talking points for the staff meeting. Upselling focuses, service standards, and energy maintenance.)
        """

        # 3. Call the API
        try:
            response = self.client.models.generate_content(
                model='gemini-pro-latest',
                contents=data_feed,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction
                )
            )
            return response.text
        except Exception as e:
            return f"Error generating Gemini summary: {e}"


# --- CLASS: GOOGLE CLIENT ---
class GoogleClient:
    def __init__(self, account_name):
        self.account_name = account_name
        self.creds = None
        self.gmail_service = None
        self.calendar_service = None
        self.token_file = f'token_{account_name}.json'

    def authenticate(self):
        if os.path.exists(self.token_file):
            self.creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                except Exception:
                    if os.path.exists(self.token_file): os.remove(self.token_file)
                    self.authenticate()
                    return
            else:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            with open(self.token_file, 'w') as token:
                token.write(self.creds.to_json())

        self.gmail_service = build('gmail', 'v1', credentials=self.creds)
        self.calendar_service = build('calendar', 'v3', credentials=self.creds)

    def get_recent_emails(self, days=1):
            if not self.gmail_service: return []
            
            # Calculate the query date (e.g., "newer_than:1d")
            query = f"newer_than:{days}d"
            
            try:
                # We increase maxResults to 50 to ensure we catch all emails in that window
                # but rely on the 'q' parameter to filter by time.
                results = self.gmail_service.users().messages().list(
                    userId='me', 
                    labelIds=['INBOX'], 
                    q=query,
                    maxResults=50 
                ).execute()
                
                messages = results.get('messages', [])
                email_data = []

                if not messages:
                    return []

                for msg in messages:
                    txt = self.gmail_service.users().messages().get(userId='me', id=msg['id']).execute()
                    headers = txt['payload'].get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "No Subject")
                    sender = next((h['value'] for h in headers if h['name'] == 'From'), "Unknown")
                    snippet = txt.get('snippet', '')
                    
                    email_data.append({
                        "type": "Email",
                        "source": f"Gmail ({self.account_name})",
                        "title": subject,
                        "detail": sender,
                        "snippet": snippet
                    })
                return email_data
            except Exception:
                return []

    def get_calendar_events(self, days=7):
        if not self.calendar_service: return []
        try:
            calendar_list = self.calendar_service.calendarList().list().execute()
            calendars = calendar_list.get('items', [])
        except Exception:
            return []

        events_data = []
        now = datetime.datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        end_time = (datetime.datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace('+00:00', 'Z')

        for cal in calendars:
            if 'holiday' in cal.get('summary', '').lower() or 'contacts' in cal.get('summary', '').lower():
                continue
            
            try:
                events_result = self.calendar_service.events().list(
                    calendarId=cal['id'], timeMin=now, timeMax=end_time,
                    maxResults=10, singleEvents=True, orderBy='startTime').execute()
                events = events_result.get('items', [])

                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    summary = event.get('summary', '(No Title)')
                    try:
                        dt_object = dateutil.parser.parse(start)
                    except:
                        dt_object = datetime.datetime.now()

                    if 'T' in start:
                        display_time = dt_object.strftime("%H:%M")
                    else:
                        display_time = "All Day"

                    events_data.append({
                        "type": "Calendar",
                        "source": f"Google ({self.account_name})",
                        "title": summary,
                        "detail": display_time,
                        "sort_key": dt_object.replace(tzinfo=None)
                    })
            except Exception:
                continue
        return events_data

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    all_data = []
    print("--- SYNCING ACCOUNTS ---")

    for account in GOOGLE_ACCOUNTS:
        print(f"* Syncing Google: {account}...")
        g_client = GoogleClient(account)
        g_client.authenticate()
        all_data.extend(g_client.get_calendar_events(days=7))
        all_data.extend(g_client.get_recent_emails(days=5))

    # Sort Data
    calendar_events = [x for x in all_data if x['type'] == 'Calendar']
    emails = [x for x in all_data if x['type'] == 'Email']
    calendar_events.sort(key=lambda x: x['sort_key'])

    # --- PART 1: PRINT RAW SCHEDULE ---
    print("\n" + "="*50)
    print(f"WEEKLY SCHEDULE (NEXT 7 DAYS)")
    print("="*50)
    
    current_day = None
    for event in calendar_events:
        event_day = event['sort_key'].strftime("%A, %b %d")
        if event_day != current_day:
            print(f"\n[{event_day}]")
            current_day = event_day
        print(f"   {event['detail']} - {event['title']} ({event['source']})")
        
    print("\n" + "="*50)

    # --- PART 2: AI BATTLE PLAN ---
    if secrets_config and hasattr(secrets_config, 'GEMINI_API_KEY'):
        ai = AIAssistant(secrets_config.GEMINI_API_KEY)
        briefing = ai.generate_briefing(calendar_events, emails)
        print("\n" + briefing)
        

    else:
        print("X Gemini API Key missing.")
    print("="*50)
"

# Convert to list of strings (lines) including newlines
source_lines = source_code.splitlines(keepends=True)


notebook = {
 "cells": [
  {
   "cell_type": "code",
   "execution_count": None,
   "id": "6115ce52",
   "metadata": {},
   "outputs": [],
   "source": source_lines
  },
  {
   "cell_type": "code",
   "execution_count": None,
   "id": "a905bd09",
   "metadata": {},
   "outputs": [],
   "source": [
    "from google import genai\n",
    "import secrets_config\n",
    "\n",
    "# Initialize the client\n",
    "client = genai.Client(api_key=secrets_config.GEMINI_API_KEY)\n",
    "\n",
    "print(f"{'MODEL ID':<30} | {'DISPLAY NAME'}")\n",
    "print(\"-" * 60)\"\n",
    "\n",
    "try:\n",
    "    # Fetch all models\n",
    "    # The new library returns a Pager object, we iterate through it\n",
    "    for model in client.models.list():\n",
    "        \n",
    "        # In the new library, we check 'supported_actions' instead of 'supported_generation_methods'\n",
    "        # Some older models might not have this attribute set, so we safely check.\n",
    "        actions = getattr(model, 'supported_actions', [])\n",
    "        \n",
    "        if \"generateContent\" in actions:\n",
    "            # Clean up the ID (remove 'models/' prefix)\n",
    "            model_id = model.name.replace(\"models/\", \"\")\n",
    "            \n",
    "            # Handle cases where display_name might be None\n",
    "            display_name = model.display_name if model.display_name else \"(No Name)\"\n",
    "            \n",
    "            print(f"{{model_id:<30}} | {{display_name}}")\n",
    "\n",
    "except Exception as e:\n",
    "    print(f"Error listing models: {{e}}")\n",
    "    # Debugging: Print one model to see what it looks like if it fails\n",
    "    try:\n",
    "        print(\"\\n--- Debug: First Model Object Attributes ---")\n",
    "        first_item = next(client.models.list())\n",
    "        print(dir(first_item))\n",
    "    except:\n",
    "        pass"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "dailybriefing-env",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "codemirror_mode": {
    "name": "ipython",
    "version": 3
   },
   "file_extension": ".py",
   "mimetype": "text/x-python",
   "name": "python",
   "nbconvert_exporter": "python",
   "pygments_lexer": "ipython3",
   "version": "3.14.2"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 5
}

with open(file_path, 'w', encoding='utf-8') as f:
    json.dump(notebook, f, indent=1)

print("Successfully reconstructed agent.ipynb")
