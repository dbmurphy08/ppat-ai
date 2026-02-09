"""
Cocktail Creator Agent
======================
Standalone agent focused exclusively on generating specialty cocktails with
full recipes, itemized ingredient costs, and menu pricing.

Reads real liquor costs from Patterson_Inventory.xlsx and targets 15% COGS
with menu prices between $10-$14.  Uses mid-tier (a step above well) spirits.

The user can request cocktails by type, theme, season, spirit, occasion, or
quantity.  Follow-up messages refine the current cocktail list.

Accepts an optional log_fn(category, message, level) callback for the GUI
debug console.
"""

import datetime
import json
from pathlib import Path
from google import genai
from google.genai import types
import openpyxl

try:
    import secrets_config
except ImportError:
    secrets_config = None

INVENTORY_FILE = Path(__file__).parent / "context_data" / "Patterson_Inventory.xlsx"
HISTORY_FILE = Path(__file__).parent / "cocktail_history.json"


def _noop_log(category, message, level="info"):
    pass


class CocktailAgent:
    def __init__(self, api_key, log_fn=None, memory_manager=None):
        self.log = log_fn or _noop_log
        self.memory_manager = memory_manager
        self.log("COCKTAIL", "Initialising genai.Client...", "info")
        self.client = genai.Client(api_key=api_key)
        self.log("COCKTAIL", "genai.Client ready.", "ok")
        self.liquor_inventory = self._load_liquor_inventory()
        self.history = self._load_history()

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------
    def _load_liquor_inventory(self):
        """Load liquor names and per-ounce costs from Patterson_Inventory.xlsx."""
        if not INVENTORY_FILE.exists():
            self.log("INVENTORY", f"Inventory file not found: {INVENTORY_FILE}", "warn")
            return {}

        self.log("INVENTORY", f"Loading liquor inventory from {INVENTORY_FILE}...", "info")
        try:
            wb = openpyxl.load_workbook(INVENTORY_FILE, data_only=True)
            ws = wb["Liquor Inv"]

            inventory = {}
            for row in ws.iter_rows(min_row=5, values_only=True):
                name = row[0]
                unit_price = row[15]  # "Unit Price" = cost per ounce
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

    # ------------------------------------------------------------------
    # Prompt context
    # ------------------------------------------------------------------
    def _get_inventory_context(self):
        """Build the categorised inventory + pricing rules block for prompts."""
        if not self.liquor_inventory:
            return ""

        well, mid_tier, premium, liqueurs = [], [], [], []

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
            "\nWELL LIQUORS (avoid for specialty cocktails):",
        ]
        lines.extend(well)
        lines.append("\nMID-TIER LIQUORS (preferred — a step up from well):")
        lines.extend(mid_tier)
        lines.append("\nPREMIUM LIQUORS (use sparingly, for feature cocktails only):")
        lines.extend(premium)
        lines.append("\nLIQUEURS & SPECIALTY SPIRITS:")
        lines.extend(liqueurs)
        lines.append("\n--- END INVENTORY ---")

        lines.append("""
--- COCKTAIL PRICING RULES (MANDATORY) ---
You MUST follow ALL of these rules for every cocktail you create:

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
   - Bitters (2-3 dashes): $0.15

3. RECIPE FORMAT — use this EXACT layout for every cocktail:
   **[Cocktail Name]** — [one-line flavour / vibe description]
   - [amount] oz [Ingredient] (cost: $X.XX)
   - [amount] oz [Ingredient] (cost: $X.XX)
   - ...repeat for every ingredient...
   - Garnish: [description] (cost: $X.XX)
   - **Total COGS: $X.XX**
   - **Menu Price: $XX.00** (COGS %: XX%)

4. TARGET 15% cost of goods.  Menu Price = Total COGS / 0.15, rounded to nearest
   dollar within the $10-$14 range.  If COGS is very low, price at $10.
   If COGS pushes above $14, adjust ingredients down or substitute a cheaper spirit.

5. Use ONLY spirits that appear in the inventory list above (with their real $/oz).
   If the user requests a spirit not in inventory, note that it is not stocked and
   provide an estimated cost clearly marked as "(est.)".

6. Every cocktail MUST list specific oz measurements, specific ingredient names,
   itemized per-ingredient costs, a summed Total COGS, and the final Menu Price.
--- END PRICING RULES ---
""")
        return "\n".join(lines)

    def _get_memory_context(self):
        if self.memory_manager:
            self.log("COCKTAIL", "Using shared memory context.", "info")
            return self.memory_manager.get_memory_context()
        return ""

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def _load_history(self):
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.log("HISTORY", f"Loaded {len(data)} cocktail history entries.", "ok")
                return data
            except json.JSONDecodeError as e:
                self.log("HISTORY", f"JSON decode error: {e} — starting fresh.", "warn")
                return []
        self.log("HISTORY", "No cocktail history file — starting fresh.", "info")
        return []

    def save_interaction(self, user_input, ai_response):
        ts = datetime.datetime.now().isoformat()
        self.history.append({"timestamp": ts, "role": "user", "content": user_input})
        self.history.append({"timestamp": ts, "role": "model", "content": ai_response})
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)
        self.log("HISTORY", "Cocktail history saved.", "ok")

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def generate_cocktails(self, user_request):
        """Generate cocktails based on an open-ended user request.

        The user_request can specify themes, spirit preferences, number of
        cocktails, occasion, season, flavour profiles, etc.
        """
        self.log("COCKTAIL", f"Generating cocktails for request: {user_request[:120]}", "info")
        current_date = datetime.datetime.now().strftime("%B %d, %Y")
        inventory_ctx = self._get_inventory_context()
        memory_ctx = self._get_memory_context()

        prompt = f"""
Current Date: {current_date}

You are the head bartender and cocktail director at Patterson Park Patio Bar
in Houston, Texas.  Our clientele is 23-39 year olds who appreciate creative,
well-crafted cocktails.

{memory_ctx}
{inventory_ctx}

The bar owner has made the following request:
\"{user_request}\"

Based on this request, create the cocktails they asked for.  Follow the
COCKTAIL PRICING RULES above exactly — every cocktail needs the full recipe
format with itemized costs, Total COGS, and Menu Price ($10-$14, targeting
15% COGS).

Guidelines for interpreting the request:
- If they specify a NUMBER of cocktails, create exactly that many.
- If they specify a THEME (e.g. "tropical", "fall harvest", "Mardi Gras"),
  make every cocktail fit that theme.
- If they specify a SPIRIT type (e.g. "bourbon cocktails", "tequila-forward"),
  use that spirit as the base for each cocktail.
- If they specify a flavour direction (e.g. "citrusy", "smoky", "sweet"),
  design around that profile.
- If the request is open-ended (e.g. "surprise me" or "create a summer menu"),
  generate 3-4 varied cocktails that would work well together as a menu section.
- Give each cocktail a creative, memorable name that fits the theme.
- After all cocktails, provide a brief **Menu Summary** table showing each
  cocktail name, base spirit, COGS, and menu price side by side.
"""
        return self._call_model(prompt)

    def refine_cocktails(self, current_cocktails, user_feedback):
        """Refine the current cocktail list based on user feedback."""
        self.log("COCKTAIL", f"Refining cocktails with feedback: {user_feedback[:120]}", "info")
        inventory_ctx = self._get_inventory_context()
        memory_ctx = self._get_memory_context()

        prompt = f"""
You are the head bartender and cocktail director at Patterson Park Patio Bar.

{memory_ctx}
{inventory_ctx}

Here are the cocktails you previously created:
---
{current_cocktails}
---

The bar owner has the following feedback:
\"{user_feedback}\"

Please UPDATE the cocktail list to incorporate this feedback.  You may:
- Modify individual cocktails (swap ingredients, adjust measurements, rename)
- Remove cocktails the owner doesn't like
- Add new cocktails if requested
- Adjust pricing if the owner wants different price points

Every cocktail in the updated list MUST still follow the COCKTAIL PRICING RULES
above — full recipe format, itemized costs, Total COGS, and Menu Price.
Include the updated **Menu Summary** table at the end.
"""
        return self._call_model(prompt)

    # ------------------------------------------------------------------
    # Model call with fallback
    # ------------------------------------------------------------------
    def _call_model(self, prompt):
        self.log("GEMINI", f"Prompt built — {len(prompt)} chars", "info")
        try:
            response = self.client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=prompt,
            )
            self.log("GEMINI", f"Response received — {len(response.text)} chars", "ok")
            return response.text
        except Exception as e:
            self.log("GEMINI", f"Primary model error: {e}", "warn")
            self.log("GEMINI", "Falling back to gemini-pro-latest...", "warn")
            try:
                response = self.client.models.generate_content(
                    model="gemini-pro-latest",
                    contents=prompt,
                )
                self.log("GEMINI", f"Fallback response — {len(response.text)} chars", "ok")
                return response.text
            except Exception as e2:
                self.log("GEMINI", f"Fallback also failed: {e2}", "err")
                return f"Error generating cocktails: {e}"


# ----------------------------------------------------------------------
# Standalone CLI (for testing without the GUI)
# ----------------------------------------------------------------------
def main():
    if not secrets_config or not hasattr(secrets_config, "GEMINI_API_KEY"):
        print("Gemini API Key missing in secrets_config.py.")
        return

    agent = CocktailAgent(secrets_config.GEMINI_API_KEY)

    request = input("What kind of cocktails would you like? ").strip()
    if not request:
        request = "Create 3 creative seasonal cocktails for a summer patio bar."

    result = agent.generate_cocktails(request)
    print("\n" + "=" * 50)
    print(result)

    while True:
        print("\n" + "=" * 50)
        feedback = input("Feedback to adjust (or 'exit' to quit): ").strip()
        if feedback.lower() in ("exit", "quit", "done"):
            agent.save_interaction("Session Ended", "Session Ended")
            break
        if not feedback:
            continue

        result = agent.refine_cocktails(result, feedback)
        agent.save_interaction(feedback, result)
        print("\n" + "=" * 50)
        print(result)


if __name__ == "__main__":
    main()
