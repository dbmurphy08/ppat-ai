"""
Patterson Park Patio Bar - Chatbot GUI
=======================================
Provides a chat interface to interact with three agents:
  1. Daily Briefing Agent  (from daily_briefing.py / agent.ipynb)
  2. Party Planner Agent   (from party_planner.py)
  3. Cocktail Creator Agent (from cocktail_agent.py)

User messages are fed directly into the Gemini LLM calls within each agent.
A collapsible debug console shows all behind-the-scenes activity.
"""

import tkinter as tk
from tkinter import scrolledtext
import threading
import datetime
import traceback

# ---------------------------------------------------------------------------
# Agent imports
# ---------------------------------------------------------------------------
try:
    import secrets_config
except ImportError:
    secrets_config = None

from party_planner import PartyPlanningAgent
from daily_briefing import AIAssistant, sync_google_data
from cocktail_agent import CocktailAgent
from memory_manager import MemoryManager

# ---------------------------------------------------------------------------
# Colour / style constants
# ---------------------------------------------------------------------------
BG_DARK = "#1e1e2e"
BG_CHAT = "#2a2a3c"
BG_DEBUG = "#11111b"
FG_TEXT = "#cdd6f4"
FG_USER = "#a6e3a1"
FG_BOT = "#89b4fa"
FG_SYSTEM = "#f9e2af"
FG_DEBUG = "#6c7086"
FG_DEBUG_WARN = "#fab387"
FG_DEBUG_ERR = "#f38ba8"
FG_DEBUG_OK = "#a6e3a1"
FG_DEBUG_INFO = "#74c7ec"
ACCENT = "#cba6f7"
ENTRY_BG = "#313244"
BTN_BG = "#45475a"
BTN_ACTIVE = "#585b70"
FONT_MAIN = ("Consolas", 11)
FONT_BOLD = ("Consolas", 11, "bold")
FONT_HEADER = ("Consolas", 14, "bold")
FONT_DEBUG = ("Consolas", 9)
FONT_DEBUG_BOLD = ("Consolas", 9, "bold")


class ChatbotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Patterson Park Patio Bar - AI Assistant")
        self.root.geometry("1000x800")
        self.root.configure(bg=BG_DARK)
        self.root.minsize(750, 550)

        # --- State ---
        self.current_agent = None  # "briefing", "party", or "cocktail"
        self.party_agent = None
        self.party_plan = None  # latest plan text from party planner
        self.briefing_ai = None
        self.cocktail_agent = None
        self.cocktail_result = None  # latest cocktail list from cocktail agent
        self.calendar_events = None
        self.emails = None
        self.loading = False
        self.debug_visible = True

        self._build_ui()
        self._show_welcome()
        self._log("APP", "Chatbot GUI initialised.")
        self._log("APP", f"secrets_config loaded: {secrets_config is not None}")

        # --- Shared Memory Manager ---
        api_key = secrets_config.GEMINI_API_KEY if secrets_config else None
        if api_key:
            self.memory_manager = MemoryManager(api_key, log_fn=self._log_safe)
        else:
            self.memory_manager = None
            self._log("MEMORY", "No API key — MemoryManager not available.", "warn")

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------
    def _build_ui(self):
        # --- Top bar with agent selector ---
        top = tk.Frame(self.root, bg=BG_DARK, pady=8, padx=12)
        top.pack(fill=tk.X)

        tk.Label(top, text="PPAT Bar AI", font=FONT_HEADER, bg=BG_DARK, fg=ACCENT).pack(side=tk.LEFT)

        # Agent buttons
        btn_frame = tk.Frame(top, bg=BG_DARK)
        btn_frame.pack(side=tk.RIGHT)

        self.btn_briefing = tk.Button(
            btn_frame, text="Daily Briefing", font=FONT_MAIN,
            bg=BTN_BG, fg=FG_TEXT, activebackground=BTN_ACTIVE, activeforeground=FG_TEXT,
            relief=tk.FLAT, padx=14, pady=4, cursor="hand2",
            command=self._select_briefing
        )
        self.btn_briefing.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_party = tk.Button(
            btn_frame, text="Party Planner", font=FONT_MAIN,
            bg=BTN_BG, fg=FG_TEXT, activebackground=BTN_ACTIVE, activeforeground=FG_TEXT,
            relief=tk.FLAT, padx=14, pady=4, cursor="hand2",
            command=self._select_party
        )
        self.btn_party.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_cocktail = tk.Button(
            btn_frame, text="Cocktail Creator", font=FONT_MAIN,
            bg=BTN_BG, fg=FG_TEXT, activebackground=BTN_ACTIVE, activeforeground=FG_TEXT,
            relief=tk.FLAT, padx=14, pady=4, cursor="hand2",
            command=self._select_cocktail
        )
        self.btn_cocktail.pack(side=tk.LEFT)

        # --- Input area (pack FIRST at BOTTOM so it is always visible) ---
        input_frame = tk.Frame(self.root, bg=BG_DARK, pady=8, padx=10)
        input_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.entry = tk.Entry(
            input_frame, font=FONT_MAIN,
            bg=ENTRY_BG, fg=FG_TEXT, insertbackground=FG_TEXT,
            relief=tk.FLAT, disabledbackground=ENTRY_BG
        )
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 8))
        self.entry.bind("<Return>", self._on_enter)

        self.send_btn = tk.Button(
            input_frame, text="Send", font=FONT_MAIN,
            bg=ACCENT, fg=BG_DARK, activebackground="#b48cf5", activeforeground=BG_DARK,
            relief=tk.FLAT, padx=18, pady=4, cursor="hand2",
            command=self._on_send
        )
        self.send_btn.pack(side=tk.RIGHT)

        # --- Main paned window (chat top, debug bottom) fills remaining space ---
        self.pane = tk.PanedWindow(
            self.root, orient=tk.VERTICAL, bg=BG_DARK,
            sashwidth=6, sashrelief=tk.FLAT, sashpad=0
        )
        self.pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 0))

        # --- Chat display ---
        chat_frame = tk.Frame(self.pane, bg=BG_DARK)
        self.chat_display = scrolledtext.ScrolledText(
            chat_frame, wrap=tk.WORD, state=tk.DISABLED,
            bg=BG_CHAT, fg=FG_TEXT, font=FONT_MAIN,
            insertbackground=FG_TEXT, relief=tk.FLAT,
            padx=12, pady=10, spacing3=4
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True)

        # Tag styles for chat
        self.chat_display.tag_configure("user", foreground=FG_USER, font=FONT_BOLD)
        self.chat_display.tag_configure("bot", foreground=FG_BOT)
        self.chat_display.tag_configure("system", foreground=FG_SYSTEM, font=FONT_BOLD)

        self.pane.add(chat_frame, stretch="always", minsize=200)

        # --- Debug console ---
        debug_frame = tk.Frame(self.pane, bg=BG_DARK)

        # Debug header bar with toggle + clear
        debug_header = tk.Frame(debug_frame, bg="#181825", pady=3, padx=6)
        debug_header.pack(fill=tk.X)

        self.debug_toggle_btn = tk.Button(
            debug_header, text="Debug Console", font=FONT_DEBUG_BOLD,
            bg="#181825", fg=FG_DEBUG, activebackground="#181825", activeforeground=FG_TEXT,
            relief=tk.FLAT, cursor="hand2", anchor="w",
            command=self._toggle_debug
        )
        self.debug_toggle_btn.pack(side=tk.LEFT)

        clear_btn = tk.Button(
            debug_header, text="Clear", font=FONT_DEBUG,
            bg=BTN_BG, fg=FG_DEBUG, activebackground=BTN_ACTIVE,
            relief=tk.FLAT, padx=8, cursor="hand2",
            command=self._clear_debug
        )
        clear_btn.pack(side=tk.RIGHT)

        self.debug_display = scrolledtext.ScrolledText(
            debug_frame, wrap=tk.WORD, state=tk.DISABLED,
            bg=BG_DEBUG, fg=FG_DEBUG, font=FONT_DEBUG,
            insertbackground=FG_DEBUG, relief=tk.FLAT,
            padx=8, pady=6, spacing3=2, height=12
        )
        self.debug_display.pack(fill=tk.BOTH, expand=True)

        # Tag styles for debug
        self.debug_display.tag_configure("dbg_default", foreground=FG_DEBUG)
        self.debug_display.tag_configure("dbg_info", foreground=FG_DEBUG_INFO)
        self.debug_display.tag_configure("dbg_ok", foreground=FG_DEBUG_OK)
        self.debug_display.tag_configure("dbg_warn", foreground=FG_DEBUG_WARN)
        self.debug_display.tag_configure("dbg_err", foreground=FG_DEBUG_ERR)
        self.debug_display.tag_configure("dbg_label", foreground=ACCENT, font=FONT_DEBUG_BOLD)

        self.pane.add(debug_frame, stretch="never", minsize=40)

    # -----------------------------------------------------------------------
    # Debug console helpers
    # -----------------------------------------------------------------------
    def _log(self, category, message, level="info"):
        """Append a timestamped line to the debug console.
        level: 'info' | 'ok' | 'warn' | 'err'
        Can be called from any thread via root.after."""
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        tag = f"dbg_{level}" if f"dbg_{level}" in (
            "dbg_info", "dbg_ok", "dbg_warn", "dbg_err"
        ) else "dbg_default"
        line = f"[{ts}] [{category}] {message}\n"

        def _insert():
            self.debug_display.configure(state=tk.NORMAL)
            self.debug_display.insert(tk.END, f"[{ts}] ", "dbg_default")
            self.debug_display.insert(tk.END, f"[{category}] ", "dbg_label")
            self.debug_display.insert(tk.END, f"{message}\n", tag)
            self.debug_display.configure(state=tk.DISABLED)
            self.debug_display.see(tk.END)

        # Thread-safe: always schedule on main thread
        try:
            self.root.after(0, _insert)
        except Exception:
            pass  # window may have been destroyed

    def _log_safe(self, category, message, level="info"):
        """Alias that is always safe to call from background threads."""
        self._log(category, message, level)

    def _toggle_debug(self):
        """Show/hide the debug console text area."""
        self.debug_visible = not self.debug_visible
        if self.debug_visible:
            self.debug_display.pack(fill=tk.BOTH, expand=True)
            self.debug_toggle_btn.configure(text="Debug Console")
        else:
            self.debug_display.pack_forget()
            self.debug_toggle_btn.configure(text="Debug Console (hidden)")

    def _clear_debug(self):
        self.debug_display.configure(state=tk.NORMAL)
        self.debug_display.delete("1.0", tk.END)
        self.debug_display.configure(state=tk.DISABLED)

    # -----------------------------------------------------------------------
    # Welcome / agent selection
    # -----------------------------------------------------------------------
    def _show_welcome(self):
        self._append_chat(
            "SYSTEM",
            "Welcome to the Patterson Park Patio Bar AI Assistant!\n\n"
            "Choose an agent above to get started:\n"
            "  [Daily Briefing]   - Syncs Google Calendar & Gmail, then generates your Daily Battle Plan.\n"
            "  [Party Planner]    - Creates and refines a 3-month seasonal event strategy.\n"
            "  [Cocktail Creator] - Design specialty cocktails with full recipes, costs, and pricing.\n\n"
            "Once an agent is active, type your messages below and they will be sent to the AI.",
            "system"
        )

    def _select_briefing(self):
        if self.loading:
            return
        self.current_agent = "briefing"
        self._update_btn_styles()
        self._clear_chat()
        self._log("AGENT", "User selected: Daily Briefing", "info")
        self._append_chat(
            "SYSTEM",
            "Daily Briefing agent selected.\n"
            "Syncing Google accounts (calendar + email)... this may take a moment.",
            "system"
        )
        self._set_loading(True)
        threading.Thread(target=self._init_briefing, daemon=True).start()

    def _select_party(self):
        if self.loading:
            return
        self.current_agent = "party"
        self._update_btn_styles()
        self._clear_chat()
        self._log("AGENT", "User selected: Party Planner", "info")
        self._append_chat(
            "SYSTEM",
            "Party Planner agent selected.\n"
            "Generating your initial 3-month seasonal plan...",
            "system"
        )
        self._set_loading(True)
        threading.Thread(target=self._init_party, daemon=True).start()

    def _select_cocktail(self):
        if self.loading:
            return
        self.current_agent = "cocktail"
        self._update_btn_styles()
        self._clear_chat()
        self._log("AGENT", "User selected: Cocktail Creator", "info")
        self._append_chat(
            "SYSTEM",
            "Cocktail Creator agent selected.\n"
            "Loading liquor inventory and pricing data...",
            "system"
        )
        self._set_loading(True)
        threading.Thread(target=self._init_cocktail, daemon=True).start()

    def _update_btn_styles(self):
        all_btns = {
            "briefing": self.btn_briefing,
            "party": self.btn_party,
            "cocktail": self.btn_cocktail,
        }
        for key, btn in all_btns.items():
            if key == self.current_agent:
                btn.configure(bg=ACCENT, fg=BG_DARK)
            else:
                btn.configure(bg=BTN_BG, fg=FG_TEXT)

    # -----------------------------------------------------------------------
    # Agent initialisation (runs in background thread)
    # -----------------------------------------------------------------------
    def _init_briefing(self):
        try:
            api_key = secrets_config.GEMINI_API_KEY if secrets_config else None
            if not api_key:
                self._log("BRIEFING", "GEMINI_API_KEY not found in secrets_config.py", "err")
                self.root.after(0, self._append_chat, "SYSTEM",
                               "Error: GEMINI_API_KEY not found in secrets_config.py.", "system")
                self.root.after(0, self._set_loading, False)
                return

            self._log("BRIEFING", "API key loaded (first 8 chars): " + api_key[:8] + "...", "ok")

            # --- Sync Google data ---
            self._log("GOOGLE", "Starting Google account sync...", "info")
            self._log("GOOGLE", f"Accounts to sync: {['bar', 'manager']}", "info")

            calendar_events, emails = sync_google_data(log_fn=self._log_safe)

            self.calendar_events = calendar_events
            self.emails = emails
            self._log("GOOGLE", f"Sync complete: {len(calendar_events)} calendar events, {len(emails)} emails", "ok")

            # --- Create AI and generate ---
            self._log("GEMINI", "Creating AIAssistant instance...", "info")
            self.briefing_ai = AIAssistant(api_key, log_fn=self._log_safe, memory_manager=self.memory_manager)

            self._log("GEMINI", "Calling generate_briefing() — no user message (initial run)", "info")
            result = self.briefing_ai.generate_briefing(calendar_events, emails)
            self._log("GEMINI", f"Response received — {len(result)} chars", "ok")

            self.root.after(0, self._append_chat, "AI (Daily Briefing)", result, "bot")
            self.root.after(0, self._append_chat, "SYSTEM",
                           "Data synced. You can now ask follow-up questions about your schedule, "
                           "emails, or operations.", "system")
        except Exception as e:
            self._log("BRIEFING", f"EXCEPTION: {e}", "err")
            self._log("BRIEFING", traceback.format_exc(), "err")
            self.root.after(0, self._append_chat, "SYSTEM", f"Error: {e}", "system")
        finally:
            self.root.after(0, self._set_loading, False)

    def _init_party(self):
        try:
            api_key = secrets_config.GEMINI_API_KEY if secrets_config else None
            if not api_key:
                self._log("PARTY", "GEMINI_API_KEY not found in secrets_config.py", "err")
                self.root.after(0, self._append_chat, "SYSTEM",
                               "Error: GEMINI_API_KEY not found in secrets_config.py.", "system")
                self.root.after(0, self._set_loading, False)
                return

            self._log("PARTY", "API key loaded (first 8 chars): " + api_key[:8] + "...", "ok")

            # --- Ensure Google Calendar data is available ---
            if not self.calendar_events:
                self._log("GOOGLE", "No calendar data cached — syncing Google accounts for Party Planner...", "info")
                self.root.after(0, self._append_chat, "SYSTEM",
                               "Syncing Google Calendar data...", "system")
                try:
                    calendar_events, emails = sync_google_data(log_fn=self._log_safe)
                    self.calendar_events = calendar_events
                    self.emails = emails
                    self._log("GOOGLE", f"Sync complete: {len(calendar_events)} calendar events, {len(emails)} emails", "ok")
                except Exception as ge:
                    self._log("GOOGLE", f"Google sync failed (continuing without calendar): {ge}", "warn")
                    self._log("GOOGLE", traceback.format_exc(), "warn")
                    self.calendar_events = []
                    self.emails = []
            else:
                self._log("GOOGLE", f"Using cached calendar data: {len(self.calendar_events)} events", "ok")

            self._log("PARTY", "Creating PartyPlanningAgent...", "info")
            self.party_agent = PartyPlanningAgent(
                api_key,
                log_fn=self._log_safe,
                memory_manager=self.memory_manager,
                calendar_events=self.calendar_events
            )

            self._log("PARTY", "Calling generate_seasonal_plan()...", "info")
            plan = self.party_agent.generate_seasonal_plan()
            self._log("PARTY", f"Initial plan received — {len(plan)} chars", "ok")

            self.party_plan = plan
            self.root.after(0, self._append_chat, "AI (Party Planner)", plan, "bot")
            self.root.after(0, self._append_chat, "SYSTEM",
                           "Initial plan ready. Type feedback to refine it, or ask questions.", "system")
        except Exception as e:
            self._log("PARTY", f"EXCEPTION: {e}", "err")
            self._log("PARTY", traceback.format_exc(), "err")
            self.root.after(0, self._append_chat, "SYSTEM", f"Error: {e}", "system")
        finally:
            self.root.after(0, self._set_loading, False)

    def _init_cocktail(self):
        try:
            api_key = secrets_config.GEMINI_API_KEY if secrets_config else None
            if not api_key:
                self._log("COCKTAIL", "GEMINI_API_KEY not found in secrets_config.py", "err")
                self.root.after(0, self._append_chat, "SYSTEM",
                               "Error: GEMINI_API_KEY not found in secrets_config.py.", "system")
                self.root.after(0, self._set_loading, False)
                return

            self._log("COCKTAIL", "API key loaded (first 8 chars): " + api_key[:8] + "...", "ok")
            self._log("COCKTAIL", "Creating CocktailAgent...", "info")
            self.cocktail_agent = CocktailAgent(
                api_key,
                log_fn=self._log_safe,
                memory_manager=self.memory_manager,
            )
            self.cocktail_result = None  # reset from any previous session

            self._log("COCKTAIL", "Agent ready — waiting for user request.", "ok")
            self.root.after(0, self._append_chat, "SYSTEM",
                           "Cocktail Creator ready!  Tell me what you'd like:\n\n"
                           "  Examples:\n"
                           "  - \"4 tequila-based summer cocktails\"\n"
                           "  - \"3 bourbon cocktails with a fall harvest theme\"\n"
                           "  - \"a Mardi Gras cocktail menu\"\n"
                           "  - \"2 refreshing gin drinks, citrus-forward\"\n"
                           "  - \"surprise me with 5 creative cocktails\"\n\n"
                           "I'll build full recipes with costs and pricing from our inventory.",
                           "system")
        except Exception as e:
            self._log("COCKTAIL", f"EXCEPTION: {e}", "err")
            self._log("COCKTAIL", traceback.format_exc(), "err")
            self.root.after(0, self._append_chat, "SYSTEM", f"Error: {e}", "system")
        finally:
            self.root.after(0, self._set_loading, False)

    # -----------------------------------------------------------------------
    # Sending messages
    # -----------------------------------------------------------------------
    def _on_enter(self, event=None):
        self._on_send()

    def _on_send(self):
        if self.loading:
            return
        text = self.entry.get().strip()
        if not text:
            return
        if not self.current_agent:
            self._append_chat("SYSTEM", "Please select an agent first (Daily Briefing, Party Planner, or Cocktail Creator).", "system")
            return

        self.entry.delete(0, tk.END)
        self._append_chat("You", text, "user")
        self._log("USER", f"Message sent to '{self.current_agent}': {text[:120]}{'...' if len(text) > 120 else ''}", "info")
        self._set_loading(True)
        threading.Thread(target=self._process_message, args=(text,), daemon=True).start()

    def _process_message(self, user_text):
        response = None
        agent_name = None
        try:
            if self.current_agent == "briefing":
                self._log("GEMINI", "Routing to Daily Briefing agent...", "info")
                response = self._handle_briefing_message(user_text)
                agent_name = "briefing"
            elif self.current_agent == "party":
                self._log("GEMINI", "Routing to Party Planner agent...", "info")
                response = self._handle_party_message(user_text)
                agent_name = "party_planner"
            elif self.current_agent == "cocktail":
                self._log("GEMINI", "Routing to Cocktail Creator agent...", "info")
                response = self._handle_cocktail_message(user_text)
                agent_name = "cocktail_creator"
            else:
                response = "No agent selected."

            display_names = {
                "briefing": "AI (Daily Briefing)",
                "party": "AI (Party Planner)",
                "cocktail": "AI (Cocktail Creator)",
            }
            label = display_names.get(self.current_agent, "AI")

            self._log("GEMINI", f"Response received — {len(response)} chars", "ok")
            self.root.after(0, self._append_chat, label, response, "bot")

            # Trigger memory extraction in background (non-blocking to UX)
            if self.memory_manager and agent_name and response:
                threading.Thread(
                    target=self._extract_memory,
                    args=(user_text, response, agent_name),
                    daemon=True
                ).start()

        except Exception as e:
            self._log("CHAT", f"EXCEPTION: {e}", "err")
            self._log("CHAT", traceback.format_exc(), "err")
            self.root.after(0, self._append_chat, "SYSTEM", f"Error: {e}", "system")
        finally:
            self.root.after(0, self._set_loading, False)

    def _handle_briefing_message(self, user_text):
        """Send the user's message into the Daily Briefing LLM call."""
        if not self.briefing_ai:
            self._log("BRIEFING", "Agent not initialised — cannot process message", "err")
            return "Briefing agent not initialised. Please re-select Daily Briefing."
        self._log("GEMINI", "Calling AIAssistant.chat() with user message...", "info")
        return self.briefing_ai.chat(self.calendar_events, self.emails, user_text)

    def _handle_party_message(self, user_text):
        """Send the user's message into the Party Planner LLM call."""
        if not self.party_agent:
            self._log("PARTY", "Agent not initialised — cannot process message", "err")
            return "Party Planner agent not initialised. Please re-select Party Planner."
        if self.party_plan:
            self._log("GEMINI", f"Calling refine_plan() — current plan {len(self.party_plan)} chars + user feedback", "info")
            result = self.party_agent.refine_plan(self.party_plan, user_text)
        else:
            self._log("GEMINI", "Calling refine_plan() — no existing plan", "warn")
            result = self.party_agent.refine_plan("(no plan yet)", user_text)
        self.party_plan = result
        self._log("PARTY", "Saving interaction to history...", "info")
        self.party_agent.save_interaction(user_text, result)
        self._log("PARTY", "History saved.", "ok")
        return result

    def _handle_cocktail_message(self, user_text):
        """Send the user's message into the Cocktail Creator LLM call."""
        if not self.cocktail_agent:
            self._log("COCKTAIL", "Agent not initialised — cannot process message", "err")
            return "Cocktail Creator agent not initialised. Please re-select Cocktail Creator."
        if self.cocktail_result:
            # We already have cocktails — this is refinement feedback
            self._log("GEMINI", f"Calling refine_cocktails() — current list {len(self.cocktail_result)} chars + user feedback", "info")
            result = self.cocktail_agent.refine_cocktails(self.cocktail_result, user_text)
        else:
            # First request — generate from scratch
            self._log("GEMINI", "Calling generate_cocktails() with user request...", "info")
            result = self.cocktail_agent.generate_cocktails(user_text)
        self.cocktail_result = result
        self._log("COCKTAIL", "Saving interaction to history...", "info")
        self.cocktail_agent.save_interaction(user_text, result)
        self._log("COCKTAIL", "History saved.", "ok")
        return result

    def _extract_memory(self, user_text, ai_response, source_agent):
        """Run memory extraction in a background thread. Non-blocking to UX."""
        try:
            self._log("MEMORY", f"Background extraction starting ({source_agent})...", "info")
            self.memory_manager.extract_and_store(user_text, ai_response, source_agent)
            self._log("MEMORY", "Background extraction complete.", "ok")
        except Exception as e:
            self._log("MEMORY", f"Extraction error (non-fatal): {e}", "warn")

    # -----------------------------------------------------------------------
    # Chat display helpers
    # -----------------------------------------------------------------------
    def _append_chat(self, sender, message, tag="bot"):
        self.chat_display.configure(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"\n{sender}:\n", tag)
        self.chat_display.insert(tk.END, f"{message}\n", tag if tag == "system" else "")
        self.chat_display.configure(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _clear_chat(self):
        self.chat_display.configure(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.configure(state=tk.DISABLED)

    def _set_loading(self, state):
        self.loading = state
        if state:
            self.entry.configure(state=tk.DISABLED)
            self.send_btn.configure(state=tk.DISABLED, text="...")
        else:
            self.entry.configure(state=tk.NORMAL)
            self.send_btn.configure(state=tk.NORMAL, text="Send")
            self.entry.focus_set()


def main():
    root = tk.Tk()
    ChatbotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
