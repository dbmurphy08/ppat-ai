"""
Party Planner Agent
===================
Interactive agent that generates 3-month seasonal event plans for the bar,
refines them based on user feedback, and maintains persistent conversation history.

Accepts an optional log_fn(category, message, level) callback for the GUI debug console.
"""

import os
import datetime
import json
from pathlib import Path
from google import genai
from google.genai import types
import openpyxl

# Import your secrets
try:
    import secrets_config
except ImportError:
    print("ERROR: secrets_config.py not found. Please create it.")
    secrets_config = None

HISTORY_FILE = Path(__file__).parent / "party_history.json"
INVENTORY_FILE = Path(__file__).parent / "context_data" / "Patterson_Inventory.xlsx"


def _noop_log(category, message, level="info"):
    """Default no-op logger used when no GUI callback is provided."""
    pass


class PartyPlanningAgent:
    def __init__(self, api_key, log_fn=None, memory_manager=None, calendar_events=None):
        self.log = log_fn or _noop_log
        self.memory_manager = memory_manager
        self.calendar_events = calendar_events or []
        self.log("PARTY", "Initialising genai.Client...", "info")
        self.client = genai.Client(api_key=api_key)
        self.log("PARTY", "genai.Client ready.", "ok")
        self.log("PARTY", f"History file: {HISTORY_FILE}", "info")
        if self.memory_manager:
            self.log("PARTY", "Shared MemoryManager attached.", "ok")
        if self.calendar_events:
            self.log("PARTY", f"Google Calendar loaded: {len(self.calendar_events)} events.", "ok")
        else:
            self.log("PARTY", "No Google Calendar data provided.", "warn")
        self.liquor_inventory = self._load_liquor_inventory()
        self.history = self.load_history()

    def _load_liquor_inventory(self):
        """Load liquor names and per-ounce costs from Patterson_Inventory.xlsx."""
        if not INVENTORY_FILE.exists():
            self.log("INVENTORY", f"Inventory file not found: {INVENTORY_FILE}", "warn")
            return {}

        self.log("INVENTORY", f"Loading liquor inventory from {INVENTORY_FILE}...", "info")
        try:
            wb = openpyxl.load_workbook(INVENTORY_FILE, data_only=True)
            ws = wb["Liquor Inv"]

            # Data starts at row 5 (row 1-3 are metadata/headers, row 4 is column names)
            inventory = {}
            for row in ws.iter_rows(min_row=5, values_only=True):
                name = row[0]
                unit_price = row[15]  # Column index 15 = "Unit Price" (cost per ounce)
                if name and unit_price is not None:
                    try:
                        inventory[str(name).strip()] = round(float(unit_price), 2)
                    except (ValueError, TypeError):
                        continue

            wb.close()
            self.log("INVENTORY", f"Loaded {len(inventory)} liquor items with pricing.", "ok")
            return inventory
        except Exception as e:
            self.log("INVENTORY", f"Error loading inventory: {e}", "warn")
            return {}

    def _get_cocktail_pricing_context(self):
        """Build a prompt-ready block with liquor cost-per-ounce data and cocktail pricing rules."""
        if not self.liquor_inventory:
            return ""

        # Group inventory into categories for the LLM
        well = []
        mid_tier = []
        premium = []
        liqueurs = []

        liqueur_names = {
            "aperol", "campari", "st-germain", "kahlua l",
            "grand marnier", "licor 43", "luxardo", "absente",
            "amaro nonino", "fernet branca", "chila orchata",
            "tuaca", "jagermeister", "goldschlager", "fireball",
            "rumpleminze", "skrewball", "emmet's irish cream",
            "lillet", "dolin sweet vermouth", "vermouth dry noily prat",
            "william price coffee liqueur", "william price limoncello",
        }

        for name, cost_oz in sorted(self.liquor_inventory.items()):
            entry = f"  {name}: ${cost_oz:.2f}/oz"
            lower = name.lower()
            if lower.startswith("well ") or lower.startswith("(well)"):
                well.append(entry)
            elif "liqueur" in lower or lower in liqueur_names:
                liqueurs.append(entry)
            elif cost_oz >= 1.50:
                premium.append(entry)
            else:
                mid_tier.append(entry)

        lines = [
            "\n--- LIQUOR INVENTORY & COST PER OUNCE (from Patterson Inventory) ---",
            "\nWELL LIQUORS (avoid for specialty cocktails):"
        ]
        lines.extend(well)
        lines.append("\nMID-TIER LIQUORS (preferred for specialty cocktails — a step up from well):")
        lines.extend(mid_tier)
        lines.append("\nPREMIUM LIQUORS (use sparingly, for feature cocktails only):")
        lines.extend(premium)
        lines.append("\nLIQUEURS & SPECIALTY SPIRITS:")
        lines.extend(liqueurs)
        lines.append("\n--- END INVENTORY ---")

        lines.append("""
--- COCKTAIL PRICING RULES (MANDATORY) ---
When creating specialty cocktails for events, you MUST follow these rules:

1. USE MID-TIER LIQUORS as the base — a clear step up from well, but not ultra-premium.
   Good choices: Espolon (tequila), Bacardi/Planteray 3 Star (rum), Titos/Ketel One (vodka),
   Bombay Sapphire/Tanqueray/Roku/Hendricks (gin), Makers Mark/Buffalo Trace/Bulleit (bourbon),
   Jameson (whiskey), Aperol/Campari/St-Germain/Licor 43 (liqueurs).

2. COST ASSUMPTIONS for non-liquor ingredients:
   - Fresh juices (lime, lemon, orange, grapefruit, pineapple, cranberry): $0.20/oz
   - Simple syrup, honey syrup, grenadine, agave: $0.10/oz
   - Soda water, tonic water, ginger beer: $0.15/oz
   - Fresh herbs (mint, basil, rosemary sprig): $0.25 per garnish
   - Citrus wheel/wedge garnish: $0.10 per garnish
   - Specialty garnish (edible flower, dehydrated fruit, cocktail cherry): $0.35 per garnish
   - Salt/sugar rim: $0.05
   - Egg white: $0.30

3. RECIPE FORMAT for each cocktail:
   **Cocktail Name** — one-line description
   - 2 oz [Spirit Name] (cost: $X.XX)
   - 1 oz [Mixer/Juice] (cost: $X.XX)
   - 0.75 oz [Syrup/Liqueur] (cost: $X.XX)
   - Garnish: [description] (cost: $X.XX)
   - **Total COGS: $X.XX**
   - **Menu Price: $XX.00** (target 15% COGS — price between $10-$14)

4. TARGET 15% cost of goods. Calculate: Menu Price = Total COGS / 0.15, then round to nearest dollar within $10-$14 range.
   If COGS is very low, price at $10. If COGS pushes above $14, adjust ingredients down.

5. Generate an APPROPRIATE number of specialty cocktails per event:
   - Small weekly promotions: 1-2 cocktails
   - Monthly theme events: 2-3 cocktails
   - Major events (Super Bowl, Mardi Gras, holidays): 3-4 cocktails

6. Each cocktail MUST have specific measurements in ounces, specific ingredient names,
   itemized costs per ingredient, a total COGS, and a final menu price.
--- END PRICING RULES ---
""")
        return "\n".join(lines)

    def load_history(self):
        self.log("HISTORY", f"Loading history from {HISTORY_FILE}...", "info")
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.log("HISTORY", f"Loaded {len(data)} entries from history.", "ok")
                return data
            except json.JSONDecodeError as e:
                self.log("HISTORY", f"JSON decode error: {e} — starting fresh.", "warn")
                return []
        self.log("HISTORY", "No history file found — starting fresh.", "info")
        return []

    def save_interaction(self, user_input, ai_response):
        timestamp = datetime.datetime.now().isoformat()
        entry = {
            "timestamp": timestamp,
            "role": "user",
            "content": user_input
        }
        response_entry = {
            "timestamp": timestamp,
            "role": "model",
            "content": ai_response
        }
        self.history.append(entry)
        self.history.append(response_entry)

        # Ensure directory exists
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        self.log("HISTORY", f"Saving {len(self.history)} entries to {HISTORY_FILE}...", "info")
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)
        self.log("HISTORY", "History saved.", "ok")

    def get_calendar_context(self):
        """Build a prompt-ready block of upcoming Google Calendar events."""
        if not self.calendar_events:
            self.log("CALENDAR", "No calendar events to include in prompt.", "info")
            return ""

        lines = ["\n--- UPCOMING GOOGLE CALENDAR EVENTS (Next 7 Days) ---"]
        # Group by date for readability
        prev_date = None
        included = 0
        for event in self.calendar_events:
            try:
                event_date = event['sort_key'].strftime("%A, %B %d")
            except Exception:
                event_date = "Unknown date"
            if event_date != prev_date:
                lines.append(f"\n  {event_date}:")
                prev_date = event_date
            lines.append(f"    - {event['detail']} : {event['title']} [{event['source']}]")
            included += 1

        lines.append("--- END CALENDAR ---\n")
        context = "\n".join(lines)
        self.log("CALENDAR", f"Calendar context built — {included} events, {len(context)} chars", "info")
        return context

    def get_history_context(self):
        """Return memory context if available, otherwise fall back to raw history."""
        # Prefer shared memory (compact, categorised insights)
        if self.memory_manager:
            self.log("HISTORY", "Using shared memory context instead of raw history.", "info")
            return self.memory_manager.get_memory_context()

        # Fallback: original raw history behaviour (used when run standalone)
        if not self.history:
            self.log("HISTORY", "No history to include in context.", "info")
            return ""

        context = "\n\n--- PAST CONVERSATION HISTORY ---\n"
        # Limit context to last 10 interactions to avoid token limits if history grows large
        recent_history = self.history[-10:]
        self.log("HISTORY", f"Including last {len(recent_history)} entries as context.", "info")
        for entry in recent_history:
            role = "User (Owner)" if entry['role'] == 'user' else "AI (Planner)"
            context += f"{role}: {entry['content']}\n"
        context += "--- END HISTORY ---\n\n"
        return context

    def generate_seasonal_plan(self):
        self.log("PARTY", "Generating initial seasonal plan...", "info")
        current_date = datetime.datetime.now().strftime("%B %d, %Y")

        history_context = self.get_history_context()
        calendar_context = self.get_calendar_context()
        cocktail_context = self._get_cocktail_pricing_context()

        prompt = f"""
Current Date: {current_date}

You are the owner and creative director of Patterson Park Patio Bar in Houston, Texas.
We need a forward-looking seasonal strategy.

{history_context}
{calendar_context}
{cocktail_context}

IMPORTANT: Review the Google Calendar events above carefully. These are REAL scheduled events,
bookings, and commitments already on our calendar. Your seasonal plan MUST:
- Work around any existing bookings or reservations shown in the calendar.
- Build on or enhance any events already scheduled (don't conflict with them).
- Reference specific calendar items when relevant (e.g., "Since we already have [X] booked on [date]...").
- Identify open dates that are good opportunities for new events.

Please also research upcoming seasonal events, holidays, festivals, and major sporting events relevant to Houston and our demographic (23-39 year olds).

Create a **Seasonal Theme Plan** covering the next 3 months.
For EACH month, provide:
1. **Theme Name:** Catchy title.
2. **Key Event:** One major event to throw a party for (e.g., Super Bowl, St. Patrick's, Mardi Gras, First Day of Spring).
3. **Decorations:** Specific, actionable decor ideas.
4. **Music Vibe:** Genres or specific artists.
5. **Seasonal Cocktails:** Follow the COCKTAIL PRICING RULES above exactly. Use ONLY liquors from the inventory list provided (with their real costs per ounce). For each event, create the appropriate number of specialty cocktails with full recipes, itemized ingredient costs, total COGS, and a menu price between $10-$14 targeting 15% cost of goods. Use mid-tier liquors (a step above well) as the base spirits.
6. **Weekly events** or promotions to keep the momentum going. Focus on the latest streaming entertainment or social media trends. Be creative!

Also, list any other smaller opportunities (holidays, festivals) we should be aware of.
Flag any scheduling conflicts with existing calendar events.
"""

        self.log("GEMINI", f"Prompt built — {len(prompt)} chars (including history context)", "info")
        self.log("GEMINI", "Sending to model='gemini-2.0-flash-exp'...", "info")

        try:
            response = self.client.models.generate_content(
                model='gemini-pro-latest',
                contents=prompt
            )
            self.log("GEMINI", f"Response received — {len(response.text)} chars", "ok")
            return response.text
        except Exception as e:
            self.log("GEMINI", f"Primary model error: {e}", "warn")
            self.log("GEMINI", "Falling back to model='gemini-pro-latest'...", "warn")
            # Fallback
            try:
                response = self.client.models.generate_content(
                    model='gemini-pro-latest',
                    contents=prompt
                )
                self.log("GEMINI", f"Fallback response received — {len(response.text)} chars", "ok")
                return response.text
            except Exception as e2:
                self.log("GEMINI", f"Fallback also failed: {e2}", "err")
                return f"Error generating seasonal plan: {e}"

    def refine_plan(self, current_plan, user_feedback):
        self.log("PARTY", "Refining plan with user feedback...", "info")
        self.log("PARTY", f"Current plan size: {len(current_plan)} chars", "info")
        self.log("PARTY", f"User feedback: {user_feedback[:120]}{'...' if len(user_feedback) > 120 else ''}", "info")

        history_context = self.get_history_context()
        cocktail_context = self._get_cocktail_pricing_context()

        prompt = f"""
You are the creative director of Patterson Park Patio Bar.
{history_context}
{cocktail_context}

Here is the Current Seasonal Plan you proposed:
---
{current_plan}
---

The user (owner) has provided the following feedback:
"{user_feedback}"

Please UPDATE the plan to incorporate this feedback.
Keep the structure (Seasonal Theme Plan for next 3 months) but modify the specific sections requested.
If the feedback implies a total change of direction, feel free to rewrite the relevant parts entirely.
When creating or modifying cocktails, you MUST follow the COCKTAIL PRICING RULES above — use specific
ingredients from the inventory with real costs, itemize all ingredient costs, calculate total COGS,
and set menu prices between $10-$14 targeting 15% cost of goods. Use mid-tier liquors as the base.
"""

        self.log("GEMINI", f"Refine prompt built — {len(prompt)} chars", "info")
        self.log("GEMINI", "Sending to model='gemini-2.0-flash-exp'...", "info")

        try:
            response = self.client.models.generate_content(
                model='gemini-2.0-flash-exp',
                contents=prompt
            )
            self.log("GEMINI", f"Response received — {len(response.text)} chars", "ok")
            return response.text
        except Exception as e:
            self.log("GEMINI", f"Primary model error: {e}", "warn")
            self.log("GEMINI", "Falling back to model='gemini-pro-latest'...", "warn")
            try:
                response = self.client.models.generate_content(
                    model='gemini-pro-latest',
                    contents=prompt
                )
                self.log("GEMINI", f"Fallback response received — {len(response.text)} chars", "ok")
                return response.text
            except Exception as e2:
                self.log("GEMINI", f"Fallback also failed: {e2}", "err")
                return f"Error refining plan: {e}"


def main():
    if not secrets_config or not hasattr(secrets_config, 'GEMINI_API_KEY'):
        print("Gemini API Key missing in secrets_config.py.")
        return

    agent = PartyPlanningAgent(secrets_config.GEMINI_API_KEY)

    # Initial Generation
    plan = agent.generate_seasonal_plan()
    print("\n" + "=" * 50)
    print("INITIAL SEASONAL PLAN")
    print("=" * 50)
    print(plan)

    # Feedback Loop
    while True:
        print("\n" + "=" * 50)
        feedback = input("Enter your feedback to refine the plan (or type 'exit' to quit): ").strip()

        if feedback.lower() in ['exit', 'quit', 'no', 'done']:
            print("Exiting. Happy Planning!")
            agent.save_interaction("Session Ended", "Session Ended")
            break

        if not feedback:
            continue

        plan = agent.refine_plan(plan, feedback)

        # Save this interaction to history
        agent.save_interaction(feedback, plan)

        print("\n" + "=" * 50)
        print("UPDATED SEASONAL PLAN")
        print("=" * 50)
        print(plan)


if __name__ == "__main__":
    main()
