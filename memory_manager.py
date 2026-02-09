"""
Memory Manager - Shared hybrid memory system for Patterson Park Patio Bar agents.
==================================================================================
Provides a MemoryManager class that:
  - Stores categorized key insights in bar_memory.json
  - Extracts new insights from conversation turns via a lightweight Gemini Flash call
  - Deduplicates and auto-prunes old entries
  - Injects a compact memory context block into agent prompts

Both agents (Daily Briefing and Party Planner) share a single MemoryManager instance
so insights flow across agents.
"""

import json
import datetime
import threading
from pathlib import Path
from google import genai
from google.genai import types

MEMORY_FILE = Path(__file__).parent / "bar_memory.json"
EXTRACTION_MODEL = "gemini-pro-latest"
VALID_CATEGORIES = [
    "owner_preferences",
    "approved_decisions",
    "rejected_ideas",
    "operational_notes",
    "event_history",
]
MAX_ENTRIES_PER_CATEGORY_IN_PROMPT = 5


def _noop_log(category, message, level="info"):
    """Default no-op logger used when no GUI callback is provided."""
    pass


class MemoryManager:
    """Thread-safe manager for shared agent memory backed by bar_memory.json."""

    def __init__(self, api_key, log_fn=None, memory_path=None):
        self.log = log_fn or _noop_log
        self.memory_path = Path(memory_path) if memory_path else MEMORY_FILE
        self.log("MEMORY", f"Initialising genai.Client for extraction model...", "info")
        self.client = genai.Client(api_key=api_key)
        self._lock = threading.Lock()
        self.memory = self.load_memory()
        self.log("MEMORY", f"MemoryManager ready. File: {self.memory_path}", "ok")

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------
    def load_memory(self) -> dict:
        """Load memory from disk, or create an empty structure."""
        self.log("MEMORY", f"Loading memory from {self.memory_path}...", "info")
        if self.memory_path.exists():
            try:
                with open(self.memory_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                total = sum(len(data.get("categories", {}).get(c, [])) for c in VALID_CATEGORIES)
                self.log("MEMORY", f"Loaded {total} active memory entries.", "ok")
                return data
            except (json.JSONDecodeError, KeyError) as e:
                self.log("MEMORY", f"Error loading memory: {e} — creating fresh.", "warn")

        self.log("MEMORY", "No memory file found — creating empty structure.", "info")
        return self._empty_memory()

    def save_memory(self) -> None:
        """Persist current memory to disk (thread-safe)."""
        with self._lock:
            self.memory["last_updated"] = datetime.datetime.now().isoformat()
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.memory_path, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2, ensure_ascii=False)
            total = sum(len(self.memory["categories"].get(c, [])) for c in VALID_CATEGORIES)
            self.log("MEMORY", f"Memory saved — {total} active entries.", "ok")

    @staticmethod
    def _empty_memory() -> dict:
        return {
            "version": 1,
            "last_updated": datetime.datetime.now().isoformat(),
            "categories": {cat: [] for cat in VALID_CATEGORIES},
            "archive": [],
        }

    # -------------------------------------------------------------------
    # Prompt injection
    # -------------------------------------------------------------------
    def get_memory_context(self) -> str:
        """Build a compact text block of key insights for injection into prompts.

        Caps at MAX_ENTRIES_PER_CATEGORY_IN_PROMPT most-recent entries per category
        to keep token usage predictable (~500-800 tokens).
        """
        with self._lock:
            categories = self.memory.get("categories", {})

        total_entries = sum(len(categories.get(c, [])) for c in VALID_CATEGORIES)
        if total_entries == 0:
            self.log("MEMORY", "No memory entries to inject.", "info")
            return ""

        section_map = {
            "owner_preferences": "PREFERENCES",
            "approved_decisions": "APPROVED",
            "rejected_ideas": "VETOED",
            "operational_notes": "OPS NOTES",
            "event_history": "PAST EVENTS",
        }

        lines = ["\n--- OWNER MEMORY (Key Insights from Past Interactions) ---"]
        included = 0
        for cat_key, label in section_map.items():
            entries = categories.get(cat_key, [])
            if entries:
                recent = entries[-MAX_ENTRIES_PER_CATEGORY_IN_PROMPT:]
                lines.append(f"[{label}]")
                for entry in recent:
                    lines.append(f"- {entry['content']}")
                    included += 1

        lines.append("--- END OWNER MEMORY ---\n")

        context = "\n".join(lines)
        self.log("MEMORY", f"Memory context built — {len(context)} chars, {included}/{total_entries} entries included", "info")
        return context

    # -------------------------------------------------------------------
    # Extraction pipeline
    # -------------------------------------------------------------------
    def extract_and_store(self, user_message: str, ai_response: str, source_agent: str) -> None:
        """Extract insights from a conversation turn and persist them.

        Called from a background thread AFTER the main response is displayed.
        """
        self.log("MEMORY", f"Starting extraction for '{source_agent}' turn...", "info")

        # Build compact summary of existing memory for the extraction prompt
        current_summary = self.get_memory_context()

        # Call the extraction LLM
        new_entries = self._call_extraction_llm(user_message, ai_response, current_summary)

        if not new_entries:
            self.log("MEMORY", "No new insights extracted.", "info")
            return

        # Validate
        valid_entries = []
        for entry in new_entries:
            if (isinstance(entry, dict)
                    and entry.get("category") in VALID_CATEGORIES
                    and entry.get("content")
                    and len(entry["content"]) > 3):
                valid_entries.append(entry)
            else:
                self.log("MEMORY", f"Skipping invalid entry: {entry}", "warn")

        # Deduplicate
        unique_entries = self._deduplicate(valid_entries)

        if not unique_entries:
            self.log("MEMORY", "All extracted entries were duplicates.", "info")
            return

        # Store
        now = datetime.datetime.now().isoformat()
        with self._lock:
            for entry in unique_entries:
                cat = entry["category"]
                mem_entry = {
                    "id": self._generate_id(cat),
                    "content": entry["content"],
                    "source_agent": source_agent,
                    "created": now,
                    "confidence": entry.get("confidence", "medium"),
                }
                self.memory["categories"][cat].append(mem_entry)
                self.log("MEMORY", f"Stored [{cat}]: {entry['content'][:80]}", "ok")

        self.save_memory()

        # Auto-prune after each save
        pruned = self.prune_old_entries()
        if pruned > 0:
            self.save_memory()

    def _call_extraction_llm(self, user_message: str, ai_response: str, current_memory_summary: str) -> list:
        """Make a lightweight Gemini Flash call to extract new insights."""
        extraction_prompt = f"""You are a memory extraction system for a bar management AI assistant.
Analyze this conversation exchange between the bar owner and AI assistant.
Extract ONLY genuinely new, important insights that should be remembered long-term.

CURRENT MEMORY (do NOT re-extract anything already here):
{current_memory_summary if current_memory_summary else "(empty — no prior memory)"}

CONVERSATION EXCHANGE:
Owner: {user_message}
AI: {ai_response[:1500]}

RULES:
- Only extract CONCRETE facts, decisions, or preferences — not vague chitchat.
- Each entry must be a single concise sentence (under 20 words).
- If the owner explicitly approved or rejected something, categorize it correctly.
- If nothing new or noteworthy was said, return an empty list.
- Do NOT duplicate anything already in CURRENT MEMORY.

Return a JSON array (and NOTHING else) of objects with these fields:
- "category": one of ["owner_preferences", "approved_decisions", "rejected_ideas", "operational_notes", "event_history"]
- "content": the concise insight text
- "confidence": "high" if owner explicitly stated it, "medium" if inferred

Example output:
[{{"category": "owner_preferences", "content": "Prefers craft beer over imports", "confidence": "high"}}]

If nothing new to extract, return: []"""

        self.log("MEMORY", f"Calling extraction LLM ({EXTRACTION_MODEL})...", "info")
        try:
            response = self.client.models.generate_content(
                model=EXTRACTION_MODEL,
                contents=extraction_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1
                ),
            )
            raw = response.text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

            entries = json.loads(raw)
            self.log("MEMORY", f"Extraction returned {len(entries)} candidate entries.", "info")
            return entries if isinstance(entries, list) else []
        except json.JSONDecodeError as e:
            self.log("MEMORY", f"Extraction LLM returned invalid JSON: {e}", "warn")
            self.log("MEMORY", f"Raw response: {response.text[:200] if response else '(none)'}", "warn")
            return []
        except Exception as e:
            self.log("MEMORY", f"Extraction LLM call failed: {e}", "err")
            return []

    # -------------------------------------------------------------------
    # Deduplication and ID generation
    # -------------------------------------------------------------------
    def _deduplicate(self, new_entries: list) -> list:
        """Remove entries whose content matches existing entries (case-insensitive)."""
        unique = []
        with self._lock:
            existing_contents = set()
            for cat in VALID_CATEGORIES:
                for entry in self.memory["categories"].get(cat, []):
                    existing_contents.add(entry["content"].lower().strip().rstrip("."))

        for entry in new_entries:
            normalized = entry["content"].lower().strip().rstrip(".")
            if normalized not in existing_contents:
                unique.append(entry)
                existing_contents.add(normalized)  # prevent duplicates within batch
            else:
                self.log("MEMORY", f"Dedup: skipping '{entry['content'][:60]}'", "info")

        return unique

    def _generate_id(self, category: str) -> str:
        """Generate a unique ID like pref_001, dec_002, etc."""
        prefix_map = {
            "owner_preferences": "pref",
            "approved_decisions": "dec",
            "rejected_ideas": "rej",
            "operational_notes": "ops",
            "event_history": "evt",
        }
        prefix = prefix_map.get(category, "mem")
        existing = len(self.memory["categories"].get(category, []))
        archived = sum(
            1 for e in self.memory.get("archive", [])
            if e.get("original_category") == category
        )
        return f"{prefix}_{existing + archived + 1:03d}"

    # -------------------------------------------------------------------
    # Pruning
    # -------------------------------------------------------------------
    def prune_old_entries(self, max_age_days: int = 180) -> int:
        """Move entries older than max_age_days to the archive section."""
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=max_age_days)).isoformat()
        pruned_count = 0

        with self._lock:
            for cat in VALID_CATEGORIES:
                entries = self.memory["categories"].get(cat, [])
                keep = []
                for entry in entries:
                    if entry.get("created", "") < cutoff:
                        archived = dict(entry)
                        archived["original_category"] = cat
                        archived["archived_on"] = datetime.datetime.now().isoformat()
                        self.memory["archive"].append(archived)
                        pruned_count += 1
                        self.log("MEMORY", f"Archived old entry [{cat}]: {entry['content'][:60]}", "info")
                    else:
                        keep.append(entry)
                self.memory["categories"][cat] = keep

        if pruned_count > 0:
            self.log("MEMORY", f"Pruned {pruned_count} entries older than {max_age_days} days to archive.", "ok")

        return pruned_count
