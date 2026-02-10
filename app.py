"""
Patterson Park Patio Bar - Streamlit Web App
=============================================
Web frontend for the Party Planner and Cocktail Creator agents.
Reuses the existing agent classes unchanged.

Run locally:  streamlit run app.py
Deploy:       Push to GitHub → Render auto-deploys via render.yaml
"""

import os
import streamlit as st

# ---------------------------------------------------------------------------
# API key: Render env var first, local secrets_config fallback
# ---------------------------------------------------------------------------
def _get_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        import secrets_config
        return getattr(secrets_config, "GEMINI_API_KEY", None)
    except ImportError:
        return None

# ---------------------------------------------------------------------------
# Lazy agent constructors (cached in session state so they survive reruns)
# ---------------------------------------------------------------------------
def _get_memory_manager(api_key):
    if "memory_manager" not in st.session_state:
        from memory_manager import MemoryManager
        st.session_state.memory_manager = MemoryManager(api_key)
    return st.session_state.memory_manager


def _get_party_agent(api_key):
    if "party_agent" not in st.session_state:
        from party_planner import PartyPlanningAgent
        st.session_state.party_agent = PartyPlanningAgent(
            api_key,
            memory_manager=_get_memory_manager(api_key),
            calendar_events=[],  # Google sync disabled for web
        )
    return st.session_state.party_agent


def _get_cocktail_agent(api_key):
    if "cocktail_agent" not in st.session_state:
        from cocktail_agent import CocktailAgent
        st.session_state.cocktail_agent = CocktailAgent(
            api_key,
            memory_manager=_get_memory_manager(api_key),
        )
    return st.session_state.cocktail_agent


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Patterson Park AI Assistant",
    page_icon=":cocktail:",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title(":cocktail: Patterson Park AI Assistant")
    st.caption("Patterson Park Patio Bar - AI Assistant")
    st.divider()

    AGENTS = ("Cocktail Creator", "Party Planner")
    agent_choice = st.radio("Choose an agent", AGENTS, index=0)

    # Detect agent switch → reset conversation
    if "agent_choice" not in st.session_state:
        st.session_state.agent_choice = agent_choice
    if agent_choice != st.session_state.agent_choice:
        st.session_state.agent_choice = agent_choice
        st.session_state.messages = []
        st.session_state.party_plan = None
        st.session_state.cocktail_result = None

    st.divider()
    if st.button("New Conversation", use_container_width=True):
        st.session_state.messages = []
        st.session_state.party_plan = None
        st.session_state.cocktail_result = None
        st.rerun()

    st.divider()
    st.markdown(
        "**Cocktail Creator** — Design specialty cocktails with "
        "full recipes, itemized costs, and menu pricing.\n\n"
        "**Party Planner** — Create a 3-month seasonal event "
        "strategy with themed cocktails."
    )

# ---------------------------------------------------------------------------
# API key check
# ---------------------------------------------------------------------------
api_key = _get_api_key()
if not api_key:
    st.error(
        "**Gemini API key not found.**  \n"
        "Set the `GEMINI_API_KEY` environment variable (Render dashboard) "
        "or create a local `secrets_config.py` with `GEMINI_API_KEY = '...'`."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Session-state defaults
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "party_plan" not in st.session_state:
    st.session_state.party_plan = None
if "cocktail_result" not in st.session_state:
    st.session_state.cocktail_result = None

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
agent_label = st.session_state.agent_choice
st.header(f":cocktail: {agent_label}")

# Show starter hint when conversation is empty
if not st.session_state.messages:
    if agent_label == "Cocktail Creator":
        st.info(
            "Tell me what you'd like!  Examples:\n"
            "- *4 tequila-based summer cocktails*\n"
            "- *3 bourbon cocktails with a fall harvest theme*\n"
            "- *a Mardi Gras cocktail menu*\n"
            "- *2 refreshing gin drinks, citrus-forward*\n"
            "- *surprise me with 5 creative cocktails*\n\n"
            "I'll build full recipes with costs and pricing from our inventory."
        )
    else:
        st.info(
            "I'll generate a 3-month seasonal event plan for Patterson Park "
            "Patio Bar with themed cocktails, decorations, music, and weekly "
            "promotions.\n\n"
            "Type **go** (or anything) to generate the initial plan, then give "
            "feedback to refine it."
        )

# ---------------------------------------------------------------------------
# Render chat history
# ---------------------------------------------------------------------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Type your message..."):
    # Show user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Generate response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            if agent_label == "Cocktail Creator":
                agent = _get_cocktail_agent(api_key)
                if st.session_state.cocktail_result:
                    response = agent.refine_cocktails(
                        st.session_state.cocktail_result, prompt
                    )
                else:
                    response = agent.generate_cocktails(prompt)
                st.session_state.cocktail_result = response
                agent.save_interaction(prompt, response)

            else:  # Party Planner
                agent = _get_party_agent(api_key)
                if st.session_state.party_plan:
                    response = agent.refine_plan(
                        st.session_state.party_plan, prompt
                    )
                else:
                    response = agent.generate_seasonal_plan()
                st.session_state.party_plan = response
                agent.save_interaction(prompt, response)

            st.markdown(response)

    st.session_state.messages.append({"role": "assistant", "content": response})
