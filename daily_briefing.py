"""
Daily Briefing Agent - extracted from agent.ipynb for GUI integration.
Provides the AIAssistant and GoogleClient classes, plus a run_briefing() function
that can be called from the chatbot GUI.

All classes accept an optional log_fn(category, message, level) callback that the
GUI uses to pipe behind-the-scenes activity into the debug console.
"""

import os
import datetime
from datetime import timedelta, timezone
import dateutil.parser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google import genai
from google.genai import types

try:
    import secrets_config
except ImportError:
    secrets_config = None

# --- CONFIGURATION ---
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.readonly'
]
GOOGLE_ACCOUNTS = ['bar', 'manager']


def _noop_log(category, message, level="info"):
    """Default no-op logger used when no GUI callback is provided."""
    pass


class AIAssistant:
    def __init__(self, api_key, log_fn=None, memory_manager=None):
        self.log = log_fn or _noop_log
        self.memory_manager = memory_manager
        self.log("GEMINI", "Initialising genai.Client...", "info")
        self.client = genai.Client(api_key=api_key)
        self.log("GEMINI", "genai.Client ready.", "ok")
        if self.memory_manager:
            self.log("GEMINI", "Shared MemoryManager attached.", "ok")

    def generate_briefing(self, calendar_events, emails, user_message=None):
        """Generate the daily battle plan. If user_message is provided, it is
        appended to the data feed so the LLM can address it directly."""

        self.log("GEMINI", "Building data feed for Gemini prompt...", "info")

        current_time = datetime.datetime.now().strftime("%A, %B %d, %I:%M %p")
        data_feed = f"CURRENT TIME: {current_time}\n\n"

        # Inject shared memory context (owner preferences, decisions, etc.)
        if self.memory_manager:
            memory_ctx = self.memory_manager.get_memory_context()
            if memory_ctx:
                data_feed += memory_ctx + "\n\n"
                self.log("GEMINI", f"Memory context injected — {len(memory_ctx)} chars", "info")

        cal_count = 0
        data_feed += "--- CALENDAR (NEXT 48 HOURS) ---\n"
        for event in calendar_events:
            if event['sort_key'] < datetime.datetime.now() + timedelta(days=2):
                data_feed += f"- {event['detail']} : {event['title']} [{event['source']}]\n"
                cal_count += 1
        self.log("GEMINI", f"Calendar events included in prompt: {cal_count}", "info")

        data_feed += "\n--- RECENT EMAILS ---\n"
        for email in emails:
            data_feed += f"- From: {email['detail']} | Subject: {email['title']} | Snippet: {email['snippet']}\n"
        self.log("GEMINI", f"Emails included in prompt: {len(emails)}", "info")

        if user_message:
            data_feed += f"\n--- USER REQUEST ---\n{user_message}\n"
            self.log("GEMINI", f"User request appended ({len(user_message)} chars)", "info")

        self.log("GEMINI", f"Total prompt size: {len(data_feed)} chars", "info")

        system_instruction = """
        You are the owner and creative visionary of **Patterson Park Patio Bar** in Houston, Texas.
        You are creative and appeal to an upper-middle-class demographic (ages 23-39) in the Heights/Rice Military area.
        You make all decisions regarding operations, including event planning, scheduling, and high-level strategy.

        **Your Goal:**
        Review the raw data (calendar/emails) to provide specific instructions for managers and employees for the immediate future.
        Balance operational rigor with creative flair (party ideas, decor, menu specials).

        If the user has asked a specific question or made a request (see USER REQUEST section),
        focus your response on answering that question using the available data.
        Otherwise, produce the full Daily Battle Plan.

        ## THE FORECAST & STRATEGY
        (Synthesize weather + calendar. Predict crowd size: Low/Med/High. Define the "Vibe" for the day/night. include music types, volume levels, lighting for both day, happy hour, and night.)

        ## MANAGER ORDERS (Logistics & Ops)
        (Specific tasks: Inventory needs, repair orders, staffing adjustments, VIP table management.)

        ## CREATIVE DIRECTIVE (Events & Promo)
        - **Today/Tomorrow:** Daily specials, music selection, lighting cues.
        - **This Week:** Upcoming weekend themes, social media hooks.
        - **Future:** Ideas for parties, decorations, or menu changes based on what you see in the calendar.

        ## ACTION ITEMS (FROM EMAILS)
        (Scan email snippets for tasks like "please send," "confirm," "sign," or deadlines.)
        - [ ] Task 1
        - [ ] Task 2

        ## PRE-SHIFT RALLY (Staff Instructions)
        (Talking points for the staff meeting. Upselling focuses, service standards, and energy maintenance.)
        """

        self.log("GEMINI", "Sending request to model='gemini-pro-latest'...", "info")
        try:
            response = self.client.models.generate_content(
                model='gemini-pro-latest',
                contents=data_feed,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction
                )
            )
            self.log("GEMINI", f"Response received — {len(response.text)} chars", "ok")
            return response.text
        except Exception as e:
            self.log("GEMINI", f"API error: {e}", "err")
            return f"Error generating Gemini summary: {e}"

    def chat(self, calendar_events, emails, user_message):
        """Convenience wrapper: always includes the user message."""
        self.log("GEMINI", "chat() called — delegating to generate_briefing()", "info")
        return self.generate_briefing(calendar_events, emails, user_message=user_message)


class GoogleClient:
    def __init__(self, account_name, log_fn=None):
        self.account_name = account_name
        self.log = log_fn or _noop_log
        self.creds = None
        self.gmail_service = None
        self.calendar_service = None
        self.token_file = f'token_{account_name}.json'

    def authenticate(self):
        self.log("AUTH", f"[{self.account_name}] Checking for cached token: {self.token_file}", "info")

        if os.path.exists(self.token_file):
            self.creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            self.log("AUTH", f"[{self.account_name}] Token loaded from file.", "ok")
        else:
            self.log("AUTH", f"[{self.account_name}] No cached token found.", "warn")

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.log("AUTH", f"[{self.account_name}] Token expired — refreshing...", "warn")
                try:
                    self.creds.refresh(Request())
                    self.log("AUTH", f"[{self.account_name}] Token refreshed.", "ok")
                except Exception as e:
                    self.log("AUTH", f"[{self.account_name}] Refresh failed: {e} — re-authenticating", "err")
                    if os.path.exists(self.token_file):
                        os.remove(self.token_file)
                    self.authenticate()
                    return
            else:
                self.log("AUTH", f"[{self.account_name}] Launching OAuth2 browser flow...", "warn")
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                self.creds = flow.run_local_server(port=0)
                self.log("AUTH", f"[{self.account_name}] OAuth2 complete.", "ok")

            with open(self.token_file, 'w') as token:
                token.write(self.creds.to_json())
            self.log("AUTH", f"[{self.account_name}] Token saved to {self.token_file}", "ok")

        self.log("GOOGLE", f"[{self.account_name}] Building Gmail service...", "info")
        self.gmail_service = build('gmail', 'v1', credentials=self.creds)
        self.log("GOOGLE", f"[{self.account_name}] Building Calendar service...", "info")
        self.calendar_service = build('calendar', 'v3', credentials=self.creds)
        self.log("GOOGLE", f"[{self.account_name}] Services ready.", "ok")

    def get_recent_emails(self, days=1):
        if not self.gmail_service:
            self.log("GMAIL", f"[{self.account_name}] No gmail_service — skipping emails.", "warn")
            return []
        query = f"newer_than:{days}d"
        self.log("GMAIL", f"[{self.account_name}] Querying INBOX: '{query}'", "info")
        try:
            results = self.gmail_service.users().messages().list(
                userId='me', labelIds=['INBOX'], q=query, maxResults=50
            ).execute()
            messages = results.get('messages', [])
            if not messages:
                self.log("GMAIL", f"[{self.account_name}] No messages found.", "info")
                return []

            self.log("GMAIL", f"[{self.account_name}] Found {len(messages)} message IDs — fetching details...", "info")
            email_data = []
            for i, msg in enumerate(messages):
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
                if (i + 1) % 10 == 0:
                    self.log("GMAIL", f"[{self.account_name}] Fetched {i + 1}/{len(messages)} emails...", "info")

            self.log("GMAIL", f"[{self.account_name}] {len(email_data)} emails fetched.", "ok")
            return email_data
        except Exception as e:
            self.log("GMAIL", f"[{self.account_name}] Error fetching emails: {e}", "err")
            return []

    def get_calendar_events(self, days=7):
        if not self.calendar_service:
            self.log("CALENDAR", f"[{self.account_name}] No calendar_service — skipping.", "warn")
            return []
        self.log("CALENDAR", f"[{self.account_name}] Fetching calendar list...", "info")
        try:
            calendar_list = self.calendar_service.calendarList().list().execute()
            calendars = calendar_list.get('items', [])
            self.log("CALENDAR", f"[{self.account_name}] Found {len(calendars)} calendars.", "info")
        except Exception as e:
            self.log("CALENDAR", f"[{self.account_name}] Error listing calendars: {e}", "err")
            return []

        events_data = []
        now = datetime.datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        end_time = (datetime.datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace('+00:00', 'Z')

        for cal in calendars:
            cal_name = cal.get('summary', '(unnamed)')
            if 'holiday' in cal_name.lower() or 'contacts' in cal_name.lower():
                self.log("CALENDAR", f"[{self.account_name}] Skipping calendar: {cal_name}", "info")
                continue
            self.log("CALENDAR", f"[{self.account_name}] Querying: {cal_name}...", "info")
            try:
                events_result = self.calendar_service.events().list(
                    calendarId=cal['id'], timeMin=now, timeMax=end_time,
                    maxResults=10, singleEvents=True, orderBy='startTime'
                ).execute()
                events = events_result.get('items', [])
                self.log("CALENDAR", f"[{self.account_name}]   -> {len(events)} events", "info")
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    summary = event.get('summary', '(No Title)')
                    try:
                        dt_object = dateutil.parser.parse(start)
                    except Exception:
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
            except Exception as e:
                self.log("CALENDAR", f"[{self.account_name}] Error for calendar '{cal_name}': {e}", "err")
                continue

        self.log("CALENDAR", f"[{self.account_name}] Total events collected: {len(events_data)}", "ok")
        return events_data


def sync_google_data(log_fn=None):
    """Fetch calendar events and emails from all configured Google accounts.
    Returns (calendar_events, emails)."""
    log = log_fn or _noop_log
    all_data = []
    for account in GOOGLE_ACCOUNTS:
        log("SYNC", f"--- Syncing account: {account} ---", "info")
        g_client = GoogleClient(account, log_fn=log)
        g_client.authenticate()
        all_data.extend(g_client.get_calendar_events(days=7))
        all_data.extend(g_client.get_recent_emails(days=5))
        log("SYNC", f"--- Account '{account}' done ---", "ok")

    calendar_events = sorted(
        [x for x in all_data if x['type'] == 'Calendar'],
        key=lambda x: x['sort_key']
    )
    emails = [x for x in all_data if x['type'] == 'Email']
    log("SYNC", f"All accounts synced. Calendar: {len(calendar_events)}, Emails: {len(emails)}", "ok")
    return calendar_events, emails


def run_briefing(user_message=None, log_fn=None):
    """Run the daily briefing agent. If user_message is provided, it will
    be included in the prompt so the LLM addresses that specific query."""
    log = log_fn or _noop_log
    if not secrets_config or not hasattr(secrets_config, 'GEMINI_API_KEY'):
        log("BRIEFING", "GEMINI_API_KEY missing.", "err")
        return "Gemini API Key missing in secrets_config.py."

    calendar_events, emails = sync_google_data(log_fn=log)
    ai = AIAssistant(secrets_config.GEMINI_API_KEY, log_fn=log)

    if user_message:
        return ai.chat(calendar_events, emails, user_message)
    else:
        return ai.generate_briefing(calendar_events, emails)
