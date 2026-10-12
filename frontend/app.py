"""
Gradio frontend for OrionHarness.
Login, chat, Lab synthesizer, graph sidebar, docx export of answers.
"""

import gradio as gr
import requests
import json
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import (
    ROLE_LIMITED,
    ROLE_SUPER_USER,
    create_user,
    get_user_role,
    user_exists,
    verify_user,
)
from lab.synthesizer import ask_lab, ask_lab_stream
from backend.doc_export import markdown_to_docx
from harness.agent_registry import get_agent

# Backend API URL
API_BASE_URL = os.getenv("ORION_API_BASE", "http://localhost:8000")


def api_headers() -> dict:
    key = os.getenv("ORION_API_KEY", "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def harness_role(ui_role: str | None) -> str | None:
    if ui_role == ROLE_LIMITED:
        return "limited"
    return None

# Get logo path dynamically using os.path.join
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(FRONTEND_DIR, "logo.png")

# Load logo as base64
try:
    with open(LOGO_PATH, "rb") as f:
        LOGO_BASE64 = base64.b64encode(f.read()).decode("utf-8")
except FileNotFoundError:
    LOGO_BASE64 = ""
    print(f"[WARNING] Logo not found at: {LOGO_PATH}")


def format_status_icon(status: str) -> str:
    """Return status icon based on task status"""
    icons = {
        "pending": "⏳",
        "in_progress": "🔄",
        "completed": "✅",
        "failed": "❌"
    }
    return icons.get(status, "•")


# Agent visual identity - emoji + color for each agent type
AGENT_VISUALS = {
    "hr": {"emoji": "👥", "color": "#8b5cf6", "name": "HR"},
    "finance": {"emoji": "💰", "color": "#10b981", "name": "Finance"},
    "scheduler": {"emoji": "📅", "color": "#f59e0b", "name": "Scheduler"},
    "chart": {"emoji": "📊", "color": "#3b82f6", "name": "Charts"},
    "sql": {"emoji": "🗄️", "color": "#ef4444", "name": "SQL"},
    "orchestrator": {"emoji": "🧠", "color": "#6366f1", "name": "Orchestrator"},
}


def get_agent_header(agent_key: str) -> str:
    """Return a full colored agent header line."""
    agent_key = agent_key.lower().strip()
    v = AGENT_VISUALS.get(agent_key, {"emoji": "🤖", "color": "#64748b", "name": agent_key.upper()})
    return f'<span style="background:{v["color"]};color:white;padding:3px 10px;border-radius:6px;font-weight:600;font-size:13px;">{v["emoji"]} {v["name"]}</span>'


def get_agents_used_summary(agents: list) -> str:
    """Return a formatted summary of all agents used."""
    badges = []
    for a in agents:
        a_lower = a.lower().strip()
        v = AGENT_VISUALS.get(a_lower, {"emoji": "🤖", "color": "#64748b", "name": a.upper()})
        badges.append(f'{v["emoji"]} {v["name"]}')
    return " · ".join(badges)


def extract_message_content(content):
    """Extract text content from Gradio message content (handles various formats)"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                text_parts.append(item["text"])
        return " ".join(text_parts) if text_parts else ""
    elif isinstance(content, dict):
        return content.get("text", str(content))
    else:
        return str(content)


def get_documents_list():
    """Get list of documents from data folder for sidebar with full paths"""
    data_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    documents = []

    for root, dirs, files in os.walk(data_path):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.pdf', '.xlsx', '.csv')):
                rel_path = os.path.relpath(os.path.join(root, file), data_path)
                full_path = os.path.join(root, file)
                documents.append({"name": rel_path, "path": full_path})

    return documents


def get_documents_dataframe():
    """Get documents as dataframe format for display"""
    docs = get_documents_list()
    return [[d["name"]] for d in docs]


def open_file_for_download(file_name):
    """Find and return file path for the selected document"""
    docs = get_documents_list()
    for doc in docs:
        if doc["name"] == file_name:
            return doc["path"]
    return None


def get_generated_files():
    """Get list of generated files (graphs, exports) from outputs folder"""
    outputs_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "outputs")
    files = []

    # Check graphs folder
    graphs_path = os.path.join(outputs_path, "graphs")
    if os.path.exists(graphs_path):
        for file in os.listdir(graphs_path):
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                files.append({"name": file, "type": "graph", "path": os.path.join(graphs_path, file)})

    # Check exports folder
    exports_path = os.path.join(outputs_path, "exports")
    if os.path.exists(exports_path):
        for file in os.listdir(exports_path):
            if file.lower().endswith(('.csv', '.txt')):
                files.append({"name": file, "type": "export", "path": os.path.join(exports_path, file)})

    return files


def get_all_agent_choices():
    """Get all available agent choices (default + deployed custom) for the UI checkboxes"""
    from harness.agent_registry import get_custom_agents
    from harness.types import AgentStatus
    choices = AGENT_LABELS.copy()
    try:
        agents = get_custom_agents()
        deployed = [a for a in agents if a.status == AgentStatus.DEPLOYED]
        for a in deployed:
            choices.append(f"{a.name} ({a.id})")
    except Exception:
        pass
    return choices


def get_agent_ids_from_labels(labels: list) -> list:
    """Convert UI labels back to agent IDs"""
    # Reverse mapping for default agents
    reverse_info = {f"{v['emoji']} {v['name']}": k for k, v in AGENT_VISUALS.items()}
    
    ids = []
    for label in (labels or []):
        if label in reverse_info:
            ids.append(reverse_info[label])
        elif "(" in label and label.endswith(")"):
            # Extract custom agent ID: "Name (custom_id)" -> "custom_id"
            import re
            match = re.search(r'\((.*?)\)$', label)
            if match:
                ids.append(match.group(1))
    return ids


# Modern Custom CSS with consistent form styling
custom_css = """
/* Tool indicator animation */
@keyframes toolPulse {
    0% { box-shadow: 0 0 0 0 rgba(79, 70, 229, 0.4); }
    70% { box-shadow: 0 0 0 6px rgba(79, 70, 229, 0); }
    100% { box-shadow: 0 0 0 0 rgba(79, 70, 229, 0); }
}

.tool-indicator {
    background: linear-gradient(135deg, #1e293b 0%, #312e81 100%) !important;
    border-left: 4px solid #4f46e5 !important;
    padding: 8px 12px !important;
    border-radius: 4px 8px 8px 4px !important;
    margin-bottom: 12px !important;
    display: inline-flex !important;
    align-items: center !important;
    gap: 8px !important;
    color: #e0e7ff !important;
    font-size: 14px !important;
    font-weight: 500 !important;
    animation: toolPulse 2s infinite !important;
}
.tool-indicator .icon {
    font-size: 16px !important;
}

/* Main container */
.gradio-container {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    max-width: 1400px !important;
    margin: auto !important;
}

/* Hide footer */
footer {
    display: none !important;
}

/* Login/Signup container */
.auth-container {
    max-width: 420px !important;
    margin: 80px auto !important;
    padding: 40px !important;
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%) !important;
    border-radius: 16px !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3) !important;
}

/* Consistent form input styling */
.auth-container input[type="text"],
.auth-container input[type="password"],
.auth-container textarea {
    background: #0f172a !important;
    border: 1px solid #475569 !important;
    border-radius: 8px !important;
    padding: 12px 16px !important;
    color: #f1f5f9 !important;
    font-size: 14px !important;
}

.auth-container input::placeholder {
    color: #94a3b8 !important;
}

.auth-container input:focus {
    border-color: #6366f1 !important;
    box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2) !important;
    outline: none !important;
}

/* Label styling */
.auth-container label {
    color: #e2e8f0 !important;
    font-weight: 500 !important;
    margin-bottom: 6px !important;
}

/* Fixed height chatbot container */
.chatbot, [data-testid="chatbot"] {
    min-height: 450px !important;
    max-height: 450px !important;
}

/* Sidebar styling */
.sidebar {
    background: #1e293b !important;
    border-radius: 12px !important;
    padding: 16px !important;
    border: 1px solid #334155 !important;
}

/* Button styling */
button.primary {
    background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important;
    border: none !important;
    padding: 10px 24px !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    color: white !important;
}

button.primary:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4) !important;
}

/* Success button */
.success-btn {
    background: linear-gradient(135deg, #059669 0%, #10b981 100%) !important;
}

/* Lab button */
.lab-btn {
    background: linear-gradient(135deg, #059669 0%, #10b981 100%) !important;
    border: none !important;
    font-weight: 600 !important;
}

/* Secondary buttons - always visible text */
button.secondary {
    border: 1px solid #475569 !important;
    background: #1e293b !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    color: #f1f5f9 !important;
}

button.secondary:hover {
    background: #334155 !important;
    border-color: #6366f1 !important;
    color: #ffffff !important;
}

/* Link button style */
.link-btn {
    background: transparent !important;
    border: none !important;
    color: #6366f1 !important;
    text-decoration: underline !important;
    cursor: pointer !important;
}

/* Example buttons */
.examples button, .gallery-item {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    border-radius: 8px !important;
    padding: 8px 16px !important;
    transition: all 0.2s ease !important;
    color: #f1f5f9 !important;
}

.examples button:hover, .gallery-item:hover {
    background: #334155 !important;
    border-color: #4f46e5 !important;
    color: #ffffff !important;
}

/* Input textbox */
textarea {
    border-radius: 12px !important;
    border: 1px solid #334155 !important;
    background: #0f172a !important;
    padding: 12px 16px !important;
    color: #f1f5f9 !important;
}

textarea::placeholder {
    color: #94a3b8 !important;
}

textarea:focus {
    border-color: #4f46e5 !important;
    box-shadow: 0 0 0 2px rgba(79, 70, 229, 0.2) !important;
}

/* Scrollbar styling */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: #1e293b;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb {
    background: #475569;
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: #64748b;
}

/* Export button - orange with visible dark text */
.export-btn {
    background: linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%) !important;
    color: #1e1e1e !important;
    font-weight: 600 !important;
    border: none !important;
    border-radius: 8px !important;
    padding: 8px 16px !important;
}

.export-btn:hover {
    background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%) !important;
    color: #000 !important;
}

/* Refresh button */
.refresh-btn {
    background: #334155 !important;
    color: #f1f5f9 !important;
    border: 1px solid #475569 !important;
    border-radius: 6px !important;
}

.refresh-btn:hover {
    background: #475569 !important;
    border-color: #6366f1 !important;
}

/* Error/Success messages */
.error-msg {
    color: #ef4444 !important;
    background: rgba(239, 68, 68, 0.1) !important;
    padding: 10px !important;
    border-radius: 8px !important;
    margin-top: 10px !important;
}

.success-msg {
    color: #10b981 !important;
    background: rgba(16, 185, 129, 0.1) !important;
    padding: 10px !important;
    border-radius: 8px !important;
    margin-top: 10px !important;
}

/* Hint text styling */
.hint-text {
    color: #64748b !important;
    font-size: 12px !important;
    margin: 4px 0 8px 0 !important;
}

/* File download component styling */
.file-download {
    margin-top: 8px !important;
}

/* Export status styling */
.export-status {
    color: #10b981 !important;
    font-size: 13px !important;
    padding: 6px 0 !important;
}

/* Agent indicator badge */
.agent-indicator {
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%) !important;
    border: 1px solid #475569 !important;
    border-radius: 10px !important;
    padding: 10px 16px !important;
    margin-bottom: 8px !important;
    font-size: 14px !important;
    color: #f1f5f9 !important;
}
.agent-indicator * {
    color: #f1f5f9 !important;
}

/* Right sidebar panel */
.right-panel {
    background: #1e293b !important;
    border-radius: 12px !important;
    padding: 12px !important;
    border: 1px solid #334155 !important;
    color: #f1f5f9 !important;
}
.right-panel > .block > span,
.right-panel h3,
.right-panel p,
.right-panel label span,
.right-panel .hint-text span,
.right-panel hr {
    color: #f1f5f9 !important;
}
.right-panel button {
    color: #f1f5f9 !important;
}

.right-panel .hint-text {
    color: #f1f5f9 !important;
}

/* Custom Checkbox group styling for dark mode */
.right-panel .checkbox-group,
.right-panel fieldset {
    background: transparent !important;
    border: none !important;
}
.right-panel fieldset label,
.right-panel .checkbox-group label {
    background: #1e293b !important;
    border: 1px solid #334155 !important;
    color: #f1f5f9 !important;
    transition: all 0.2s ease !important;
}
.right-panel fieldset label span,
.right-panel .checkbox-group label span {
    color: #f1f5f9 !important;
}
.right-panel fieldset label:hover,
.right-panel .checkbox-group label:hover {
    background: #334155 !important;
    border-color: #4f46e5 !important;
}
.right-panel fieldset label.selected,
.right-panel .checkbox-group label.selected,
.right-panel fieldset label:has(input:checked),
.right-panel .checkbox-group label:has(input:checked) {
    background: #1e293b !important;
    border-color: #10b981 !important;
}

/* Lab header with back button */
.lab-header-row {
    display: flex !important;
    align-items: center !important;
    gap: 12px !important;
}

/* Lab action buttons row */
.lab-actions-row button {
    min-width: 120px !important;
}

/* Compact button group */
.compact-btn-group button {
    font-size: 13px !important;
    padding: 6px 12px !important;
}
"""

# Header HTML with logo
def get_header_html():
    return f'''
    <div style="display: flex; align-items: center; gap: 20px; padding: 20px 30px; background: linear-gradient(135deg, #1e293b 0%, #334155 100%); border-radius: 16px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);">
        <img src="data:image/png;base64,{LOGO_BASE64}" alt="Orion" style="width: 60px; height: 60px; border-radius: 12px; object-fit: contain;">
        <div style="flex-grow: 1;">
            <h1 style="margin: 0; color: #f1f5f9; font-size: 28px; font-weight: 700;">Orion</h1>
            <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 14px;">Multi-Agent Orchestrator - HR | Finance | Scheduler | Charts | SQL</p>
        </div>
    </div>
    '''

AUTH_HEADER = '''
<div style="text-align: center; margin-bottom: 30px;">
    <h1 style="color: #f1f5f9; font-size: 32px; margin-bottom: 8px;">Orion</h1>
    <p style="color: #94a3b8;">Multi-Agent RAG System</p>
</div>
'''

# Define theme
theme = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="slate",
    neutral_hue="slate"
)

AVAILABLE_AGENTS = ["hr", "finance", "scheduler", "chart", "sql"]


def agent_label(agent_key: str) -> str:
    v = AGENT_VISUALS[agent_key]
    return f"{v['emoji']} {v['name']}"


AGENT_LABELS = [agent_label(a) for a in AVAILABLE_AGENTS]


# Build interface
with gr.Blocks(title="Orion", css=custom_css, theme=theme) as demo:
    # Session state
    logged_in = gr.State(False)
    current_user = gr.State("")
    user_role = gr.State("limited")  # Track user role for access control
    selected_file = gr.State(None)  # Track selected file for Open/Download
    enabled_agents = gr.State(AVAILABLE_AGENTS.copy())

    # ==================== LOGIN PAGE ====================
    with gr.Column(visible=True, elem_classes="auth-container") as login_page:
        gr.HTML(AUTH_HEADER)
        gr.Markdown("### Sign In")

        login_username = gr.Textbox(
            label="Username",
            placeholder="Enter your username",
            elem_id="login-username"
        )
        login_password = gr.Textbox(
            label="Password",
            placeholder="Enter your password",
            type="password",
            elem_id="login-password"
        )
        login_btn = gr.Button("Sign In", variant="primary", size="lg")
        login_error = gr.Markdown("", visible=False, elem_classes="error-msg")

        gr.Markdown("<br>")
        gr.Markdown("<p style='text-align: center; color: #94a3b8;'>Don't have an account?</p>")
        goto_signup_btn = gr.Button("Create Account", variant="secondary", size="sm")

        gr.Markdown(
            "<p style='text-align: center; color: #64748b; margin-top: 20px; font-size: 12px;'>Admin password: ORION_ADMIN_PASSWORD (dev default admin). API: Bearer ORION_API_KEY when set.</p>"
        )

    # ==================== SIGNUP PAGE ====================
    with gr.Column(visible=False, elem_classes="auth-container") as signup_page:
        gr.HTML(AUTH_HEADER)
        gr.Markdown("### Create Account")

        signup_username = gr.Textbox(
            label="Username",
            placeholder="Choose a username",
            elem_id="signup-username"
        )
        signup_password = gr.Textbox(
            label="Password",
            placeholder="Create a password",
            type="password",
            elem_id="signup-password"
        )
        signup_confirm = gr.Textbox(
            label="Confirm Password",
            placeholder="Confirm your password",
            type="password",
            elem_id="signup-confirm"
        )
        signup_btn = gr.Button("Create Account", variant="primary", size="lg")
        signup_msg = gr.Markdown("", visible=False)

        gr.Markdown("<br>")
        gr.Markdown("<p style='text-align: center; color: #94a3b8;'>Already have an account?</p>")
        goto_login_btn = gr.Button("Sign In", variant="secondary", size="sm")

    # ==================== MAIN APP ====================
    with gr.Column(visible=False) as main_app:
        gr.HTML(get_header_html())
        
        # Visual flag for audit & guardrails
        gr.Markdown("**🛡️ Audit Logging ACTIVE | 🔒 Guardrails ENFORCED**", elem_classes="success-msg")

        with gr.Row():
            # LEFT COLUMN: Generated files & export
            with gr.Column(scale=1, min_width=200) as sidebar:
                gr.Markdown("### Generated Files")
                gr.Markdown("*Graphs and exports*", elem_classes="hint-text")
                generated_files_list = gr.Dataframe(
                    headers=["File", "Type"],
                    value=[[f["name"], f["type"]] for f in get_generated_files()],
                    interactive=False,
                    max_height=180
                )

                refresh_generated_btn = gr.Button("Refresh", size="sm", variant="secondary", elem_classes="refresh-btn")

            # CENTER COLUMN: Chat area
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    height=450,
                    show_label=False,
                    container=True,
                    render_markdown=True,
                )

                with gr.Row():
                    msg = gr.Textbox(
                        placeholder="Ask about HR, Finance, Schedule, or request charts...",
                        show_label=False,
                        scale=9,
                        container=False,
                    )
                    submit_btn = gr.Button("Send", variant="primary", scale=1)
                chat_files = gr.File(
                    label="Attach to live KB (not the paper lake / data/frozen)",
                    file_count="multiple",
                    type="filepath",
                )

                gr.Examples(
                    examples=[
                        "How many employees are in the Engineering department?",
                        "What meetings are scheduled for January 15th?",
                        "What is the total revenue for January 2025?",
                        "Forecast next 3 months revenue",
                        "Show me a bar chart of expenses by department",
                        "Query total salary by department",
                    ],
                    inputs=msg,
                    label="Quick Prompts"
                )
                # Hidden file component for doc download
                doc_file_download = gr.File(
                    visible=False,
                    interactive=False,
                    value=None
                )

            # RIGHT COLUMN: agent selection
            with gr.Column(scale=1, min_width=200, elem_classes="right-panel"):
                gr.Markdown("### Available Agents")
                gr.Markdown("Enable/disable agents", elem_classes="hint-text")

                agent_checkboxes = gr.CheckboxGroup(
                    choices=get_all_agent_choices(),
                    value=get_all_agent_choices(), # Default to all enabled
                    label="Agents",
                    show_label=False,
                    interactive=True,
                )
                
                main_refresh_agents_btn = gr.Button("Refresh Agents", size="sm", variant="secondary", elem_classes="refresh-btn")

                gr.Markdown("---")
                gr.Markdown("### Actions")

                with gr.Column(elem_classes="compact-btn-group"):
                    clear_btn = gr.Button("Clear Chat", variant="secondary", size="sm")
                    download_doc_btn = gr.Button("Download Doc", variant="secondary", size="sm", elem_classes="export-btn")

                gr.Markdown("---")

                with gr.Column(visible=False) as lab_btn_container:
                    lab_btn = gr.Button("Lab", variant="primary", elem_classes="lab-btn")

                logout_btn = gr.Button("Logout", variant="secondary", size="sm")

    # ==================== LAB PAGE ====================
    with gr.Column(visible=False) as lab_page:
        # Lab header with logo (matching main page style)
        gr.HTML(f'''
        <div style="display: flex; align-items: center; gap: 20px; padding: 20px 30px; background: linear-gradient(135deg, #059669 0%, #10b981 100%); border-radius: 16px; margin-bottom: 10px; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);">
            <img src="data:image/png;base64,{LOGO_BASE64}" alt="Orion" style="width: 60px; height: 60px; border-radius: 12px; object-fit: contain;">
            <div style="flex-grow: 1;">
                <h1 style="margin: 0; color: white; font-size: 28px; font-weight: 700;">Orion Lab</h1>
                <p style="margin: 4px 0 0 0; color: rgba(255,255,255,0.8); font-size: 14px;">Synthesize and deploy new workers</p>
            </div>
        </div>
        ''')

        # Visual flag for audit & guardrails
        gr.Markdown("**🛡️ Lab Audit Logging ACTIVE | 🔒 Guardrails ENFORCED**", elem_classes="success-msg")

        # Back to Chat button below header
        back_btn = gr.Button("Back to Chat", variant="secondary", size="sm")

        # Lab action buttons in a horizontal row
        with gr.Row(elem_classes="lab-actions-row"):
            customize_btn = gr.Button("Customize Agent", variant="primary", size="lg")
            deploy_btn = gr.Button("Deploy Agent", variant="secondary", size="lg")
            help_btn = gr.Button("Help", variant="secondary", size="lg")

        with gr.Row():
            # Lab sidebar: My Agents, Documents, Upload
            with gr.Column(scale=1, min_width=200):
                gr.Markdown("### My Agents")

                def get_custom_agents_dataframe():
                    from harness.agent_registry import get_custom_agents
                    agents = get_custom_agents()
                    if not agents:
                        return [["No agents yet"]]
                    return [[f"{a.name} ({a.status.value})"] for a in agents]

                my_agents_list = gr.Dataframe(
                    headers=["Agent"],
                    value=get_custom_agents_dataframe(),
                    interactive=False,
                    max_height=150
                )
                refresh_agents_btn = gr.Button("Refresh Agents", size="sm", variant="secondary")

                gr.Markdown("---")
                gr.Markdown("### Documents")
                gr.Markdown("*Click a file, then Download*", elem_classes="hint-text")
                docs_list = gr.Dataframe(
                    headers=["File"],
                    value=get_documents_dataframe(),
                    interactive=False,
                    max_height=180
                )

                download_file_btn = gr.Button("Download", size="sm", variant="secondary")

                refresh_docs_btn = gr.Button("Refresh", size="sm", variant="secondary", elem_classes="refresh-btn")

                # Status message for file operations
                file_status = gr.Markdown("", visible=True)

                # Hidden file component for downloads
                doc_download = gr.File(
                    label="Download",
                    visible=False,
                    interactive=False,
                    value=None
                )

                # Role indicator
                role_indicator = gr.Markdown("")

            # Lab chat area
            with gr.Column(scale=3):
                # Agent indicator for Lab
                lab_agent_indicator = gr.Markdown(
                    "**Active:** Lab Agent",
                    elem_classes="agent-indicator"
                )

                lab_chatbot = gr.Chatbot(
                    height=400,
                    show_label=False,
                    container=True,
                    render_markdown=True,
                    value=[{"role": "assistant", "content": "**Welcome to Orion Lab**\n\nSynthesize new workers (card + sandboxed `agent.py`). This does not wrap the builtin agents together.\n\n**Commands:**\n- **customize** — create a new agent\n- **list** — view synthesized agents\n- **use &lt;id&gt;** — chat with one\n- **test &lt;id&gt;** — static gate\n- **deploy &lt;id&gt;** — DRAFT → DEPLOYED\n\nType `help` for the full list."}]
                )

                with gr.Row():
                    lab_msg = gr.Textbox(
                        placeholder="Type a command or respond to the Lab Agent...",
                        show_label=False,
                        scale=9,
                        container=False,
                    )
                    lab_submit_btn = gr.Button("Send", variant="primary", scale=1)

                with gr.Row(elem_classes="compact-btn-group"):
                    lab_clear_btn = gr.Button("Clear Chat", variant="secondary", size="sm")
                    lab_download_doc_btn = gr.Button("Download Doc", variant="secondary", size="sm", elem_classes="export-btn")
                    lab_logout_btn = gr.Button("Logout", variant="secondary", size="sm")

                # Hidden file component for lab doc download
                lab_doc_file_download = gr.File(
                    visible=False,
                    interactive=False,
                    value=None
                )

    # ==================== EVENT HANDLERS ====================

    def do_login(username, password):
        """Handle login with role detection"""
        if not username or not password:
            return (
                gr.update(visible=True),   # login_page
                gr.update(visible=False),  # signup_page
                gr.update(visible=False),  # main_app
                gr.update(visible=False),  # lab_page
                False,                      # logged_in
                "",                         # current_user
                "limited",                  # user_role
                gr.update(visible=True, value="Please enter username and password"),
                gr.update(visible=False),  # lab_btn_container
                [],                         # enabled_agents
                gr.update(value=[]),
            )

        if verify_user(username, password):
            role = get_user_role(username) or "limited"
            # Both roles go to main chat; Lab button only visible for admin
            show_lab = role == ROLE_SUPER_USER
            return (
                gr.update(visible=False),  # login_page
                gr.update(visible=False),  # signup_page
                gr.update(visible=True),   # main_app
                gr.update(visible=False),  # lab_page
                True,                       # logged_in
                username,                   # current_user
                role,                       # user_role
                gr.update(visible=False, value=""),  # login_error
                gr.update(visible=show_lab),  # lab_btn_container
                [],                         # enabled_agents
                gr.update(value=[]),
            )
        else:
            return (
                gr.update(visible=True),   # login_page
                gr.update(visible=False),  # signup_page
                gr.update(visible=False),  # main_app
                gr.update(visible=False),  # lab_page
                False,                      # logged_in
                "",                         # current_user
                "limited",                  # user_role
                gr.update(visible=True, value="Invalid username or password"),
                gr.update(visible=False),  # lab_btn_container
                [],                         # enabled_agents
                gr.update(value=[]),
            )

    def do_signup(username, password, confirm):
        """Handle signup"""
        if not username or not password or not confirm:
            return gr.update(visible=True, value="<p class='error-msg'>Please fill in all fields</p>")

        if len(username) < 3:
            return gr.update(visible=True, value="<p class='error-msg'>Username must be at least 3 characters</p>")

        if len(password) < 6:
            return gr.update(visible=True, value="<p class='error-msg'>Password must be at least 6 characters</p>")

        if password != confirm:
            return gr.update(visible=True, value="<p class='error-msg'>Passwords do not match</p>")

        if user_exists(username):
            return gr.update(visible=True, value="<p class='error-msg'>Username already exists</p>")

        if create_user(username, password):
            return gr.update(visible=True, value="<p class='success-msg'>Account created! You can now sign in.</p>")
        else:
            return gr.update(visible=True, value="<p class='error-msg'>Failed to create account</p>")

    def do_logout():
        """Handle logout"""
        return (
            gr.update(visible=True),   # login_page
            gr.update(visible=False),  # signup_page
            gr.update(visible=False),  # main_app
            gr.update(visible=False),  # lab_page
            False,                      # logged_in
            "",                         # current_user
            "limited",                  # user_role
            [],                         # enabled_agents
            gr.update(value=[]),
        )

    def goto_signup():
        """Navigate to signup page"""
        return (
            gr.update(visible=False),  # login_page
            gr.update(visible=True),   # signup_page
        )

    def goto_login():
        """Navigate to login page"""
        return (
            gr.update(visible=True),   # login_page
            gr.update(visible=False),  # signup_page
        )

    def go_to_lab(username, role):
        """Navigate to lab page - admin only"""
        if role != ROLE_SUPER_USER:
            # Limited users cannot access Lab
            return (
                gr.update(),  # login_page unchanged
                gr.update(),  # signup_page unchanged
                gr.update(),  # main_app unchanged
                gr.update(),  # lab_page unchanged
                "**Access Denied:** Lab access requires admin privileges."
            )
        role_text = "**Role:** Super User (Full Access)"
        return (
            gr.update(visible=False),  # login_page
            gr.update(visible=False),  # signup_page
            gr.update(visible=False),  # main_app
            gr.update(visible=True),   # lab_page
            role_text                   # role_indicator
        )

    def go_to_main(role):
        """Navigate back to main app"""
        return (
            gr.update(visible=False),  # login_page
            gr.update(visible=False),  # signup_page
            gr.update(visible=True),   # main_app
            gr.update(visible=False),  # lab_page
            ""  # Clear role_indicator
        )

    def refresh_documents():
        """Refresh documents list"""
        return get_documents_dataframe()

    def refresh_generated_files():
        """Refresh generated files list"""
        return [[f["name"], f["type"]] for f in get_generated_files()]

    def user_submit(message, history, files, username):
        """Handle user message submission; optional files go to live KB via /ingest."""
        msg_text = extract_message_content(message) or ""
        msg_text = msg_text.strip()
        attach_block = ""
        file_list = files if isinstance(files, list) else ([files] if files else [])
        file_list = [f for f in file_list if f]
        if file_list:
            opened = []
            try:
                multipart = []
                for path in file_list:
                    p = path if isinstance(path, str) else str(path)
                    fh = open(p, "rb")
                    opened.append(fh)
                    multipart.append(("files", (os.path.basename(p), fh)))
                resp = requests.post(
                    f"{API_BASE_URL}/ingest",
                    files=multipart,
                    data={"user": username or "chat"},
                    headers=api_headers(),
                    timeout=120,
                )
            except Exception as exc:
                attach_block = f"(live KB ingest failed: {exc})\n"
                resp = None
            finally:
                for fh in opened:
                    try:
                        fh.close()
                    except Exception:
                        pass
            if resp is not None:
                if resp.status_code != 200:
                    attach_block = f"(live KB ingest HTTP {resp.status_code}: {resp.text[:300]})\n"
                else:
                    payload = resp.json()
                    lines = ["Attached to live KB (not the paper lake / data/frozen):"]
                    for rec in payload.get("ingested") or []:
                        if rec.get("error"):
                            lines.append(f"- {rec.get('filename')}: error {rec.get('error')}")
                        else:
                            lines.append(
                                f"- {rec.get('filename')} resource_id={rec.get('id')} "
                                f"kind={rec.get('kind')} table={rec.get('table') or 'none'} chunks={rec.get('chunks')}"
                            )
                    lines.append("Use kb_search / kb_query / kb_list on these uploads.")
                    attach_block = "\n".join(lines) + "\n"
        if attach_block and not msg_text:
            msg_text = "Summarize and answer from the attached files using kb_search / kb_query."
        full = (attach_block + "\n" + msg_text).strip() if attach_block else msg_text
        if not full:
            return "", history, None
        new_history = history + [{"role": "user", "content": full}]
        return "", new_history, None

    def bot_respond(history, selected_agent_labels, username, role):
        """Stream bot response - routes through selected agent checkboxes if set"""
        if not history:
            return history

        last_msg = history[-1]
        user_message = extract_message_content(last_msg.get("content", ""))

        if not user_message:
            return history

        history = history + [{"role": "assistant", "content": ""}]
        
        allowed_agent_ids = get_agent_ids_from_labels(selected_agent_labels)

        try:
            response = requests.post(
                f"{API_BASE_URL}/chat/stream",
                json={
                    "message": user_message.strip(),
                    "stream": True,
                    "allowed_agents": allowed_agent_ids,
                    "user": username or "system",
                    "role": harness_role(role),
                },
                headers=api_headers(),
                stream=True,
                timeout=600
            )
            
            if response.status_code != 200:
                history[-1]["content"] = f"API Error: Status {response.status_code}"
                yield history
                return
            
            output_parts = []
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith("data: "):
                        try:
                            data = json.loads(line_str[6:])
                            event_type = data.get("type", "")
                            
                            if event_type == "init":
                                output_parts = [f"**{format_status_icon('in_progress')} Orchestrator:** {data.get('message', 'Processing...')}\n\n"]
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                            
                            elif event_type == "tasks_identified":
                                tasks = data.get("tasks", [])
                                agents = data.get("agents", [])
                                output_parts.append("---\n\n")
                                output_parts.append(f"**Tasks Identified:** {len(tasks)} | **Agents:** {', '.join(a.upper() for a in agents)}\n\n")
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                            
                            elif event_type == "task_start":
                                agent = data.get("agent", "unknown")
                                task_idx = data.get("task_index", 1)
                                total = data.get("total_tasks", 1)
                                desc = data.get("description", "")
                                agent_hdr = get_agent_header(agent)
                                output_parts.append(f"**[{task_idx}/{total}]** {format_status_icon('in_progress')} {agent_hdr}\n> **Agent Prompt:** *{desc}*\n\n")
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                            
                            elif event_type == "task_complete":
                                status = data.get("status", "completed")
                                if output_parts:
                                    for i in range(len(output_parts) - 1, -1, -1):
                                        if "🔄" in output_parts[i]:
                                            output_parts[i] = output_parts[i].replace("🔄", format_status_icon(status))
                                            break
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                            
                            elif event_type == "backtrack":
                                action = data.get("action") or "rewind"
                                target = data.get("node") or data.get("nodes") or ""
                                output_parts.append(f"\n*Backtrack ({action}): {target}*\n\n")
                                history[-1]["content"] = "".join(output_parts)
                                yield history

                            elif event_type == "error":
                                output_parts.append(f"\n**{format_status_icon('failed')} Error:** {data.get('message', 'Unknown error')}")
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                                return
                            
                            elif event_type == "synthesizing":
                                output_parts.append(f"\n**{format_status_icon('in_progress')} Orchestrator:** {data.get('message', 'Combining results...')}\n")
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                            
                            elif event_type == "final":
                                final_response = data.get("response", "")
                                agents_used = data.get("agents_used", [])
                                if output_parts:
                                    for i in range(len(output_parts) - 1, -1, -1):
                                        if "🔄" in output_parts[i] and "Orchestrator" in output_parts[i]:
                                            output_parts[i] = output_parts[i].replace("🔄", format_status_icon("completed"))
                                            break
                                output_parts.append("\n---\n\n")
                                agents_summary = get_agents_used_summary(agents_used)
                                output_parts.append(f"### Response\n*via {agents_summary}*\n\n")
                                output_parts.append(final_response)
                                history[-1]["content"] = "".join(output_parts)
                                yield history
                        except json.JSONDecodeError:
                            continue
        except requests.exceptions.ConnectionError:
            history[-1]["content"] = "**Cannot connect to backend.** Make sure the FastAPI server is running on port 8000."
            yield history
        except Exception as e:
            history[-1]["content"] = f"**Error:** {str(e)}"
            yield history

    def reset_enabled_agents():
        choices = get_all_agent_choices()
        return [], gr.update(choices=choices, value=choices)

    # Login events
    login_btn.click(
        do_login,
        [login_username, login_password],
        [login_page, signup_page, main_app, lab_page, logged_in, current_user, user_role, login_error, lab_btn_container, enabled_agents, agent_checkboxes]
    ).then(
        reset_enabled_agents,
        None,
        [enabled_agents, agent_checkboxes],
        queue=False
    )


    login_password.submit(
        do_login,
        [login_username, login_password],
        [login_page, signup_page, main_app, lab_page, logged_in, current_user, user_role, login_error, lab_btn_container, enabled_agents, agent_checkboxes]
    ).then(
        reset_enabled_agents,
        None,
        [enabled_agents, agent_checkboxes],
        queue=False
    )

    # Signup events
    signup_btn.click(
        do_signup,
        [signup_username, signup_password, signup_confirm],
        [signup_msg]
    )

    # Navigation between login/signup
    goto_signup_btn.click(goto_signup, None, [login_page, signup_page])
    goto_login_btn.click(goto_login, None, [login_page, signup_page])

    # Main app navigation
    logout_btn.click(do_logout, None, [login_page, signup_page, main_app, lab_page, logged_in, current_user, user_role, enabled_agents, agent_checkboxes])
    lab_logout_btn.click(do_logout, None, [login_page, signup_page, main_app, lab_page, logged_in, current_user, user_role, enabled_agents, agent_checkboxes])
    lab_btn.click(go_to_lab, [current_user, user_role], [login_page, signup_page, main_app, lab_page, role_indicator])
    back_btn.click(go_to_main, [user_role], [login_page, signup_page, main_app, lab_page, role_indicator])

    # Document and file refresh
    refresh_docs_btn.click(refresh_documents, None, docs_list)
    refresh_generated_btn.click(refresh_generated_files, None, generated_files_list)

    # My Agents refresh
    def refresh_my_agents():
        from harness.agent_registry import get_custom_agents
        agents = get_custom_agents()
        if not agents:
            return [["No agents yet"]]
        return [[f"{a.name} ({a.status.value})"] for a in agents]

    refresh_agents_btn.click(refresh_my_agents, None, my_agents_list)

    # Main page: Refresh agent checkboxes to include newly deployed custom agents
    def refresh_main_agent_checkboxes():
        choices = get_all_agent_choices()
        return gr.update(choices=choices, value=choices)

    main_refresh_agents_btn.click(refresh_main_agent_checkboxes, None, agent_checkboxes)

    def on_doc_select(evt: gr.SelectData, dataframe):
        """Handle document selection - store the selected file name"""
        try:
            # In Gradio, evt.value contains the actual cell value
            # evt.index is (row, col) tuple or just row number
            if evt.value is not None:
                file_name = str(evt.value)
                print(f"[DEBUG] Selected file: {file_name}")
                return file_name, f"**Selected:** {file_name}"

            # Fallback: try to get from dataframe using index
            if evt.index is not None and dataframe is not None:
                # Handle both tuple (row, col) and single int index
                if isinstance(evt.index, (list, tuple)):
                    row_idx = evt.index[0]
                else:
                    row_idx = int(evt.index)

                if 0 <= row_idx < len(dataframe):
                    file_name = str(dataframe[row_idx][0])
                    print(f"[DEBUG] Selected file (fallback): {file_name}")
                    return file_name, f"**Selected:** {file_name}"
        except Exception as e:
            print(f"[DEBUG] Error selecting: {e}")

        return None, ""

    def on_download_file(file_name):
        """Download the selected file"""
        if not file_name:
            return gr.update(visible=False, value=None), "**No file selected.** Click a file in the list first."

        file_path = open_file_for_download(file_name)
        if file_path and os.path.exists(file_path):
            return gr.update(visible=True, value=file_path), f"**Downloading:** {file_name}"
        else:
            return gr.update(visible=False, value=None), f"**Error:** File not found - {file_name}"

    # Document selection
    docs_list.select(
        on_doc_select,
        [docs_list],
        [selected_file, file_status]
    )

    download_file_btn.click(
        on_download_file,
        [selected_file],
        [doc_download, file_status]
    )

    # Chat events
    msg.submit(
        user_submit,
        [msg, chatbot, chat_files, current_user],
        [msg, chatbot, chat_files],
        queue=False
    ).then(
        bot_respond,
        [chatbot, agent_checkboxes, current_user, user_role],
        chatbot
    )

    submit_btn.click(
        user_submit,
        [msg, chatbot, chat_files, current_user],
        [msg, chatbot, chat_files],
        queue=False
    ).then(
        bot_respond,
        [chatbot, agent_checkboxes, current_user, user_role],
        chatbot
    )

    clear_btn.click(lambda: [], None, [chatbot], queue=False)

    # Download Doc - extract last assistant message and convert to docx
    def download_last_response(history):
        """Download the last assistant response as a Word document"""
        if not history:
            return gr.update(visible=False, value=None)

        # Find last assistant message
        last_response = None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_response = extract_message_content(msg.get("content", ""))
                break

        if not last_response:
            return gr.update(visible=False, value=None)

        try:
            filepath = markdown_to_docx(last_response, "Orion Response")
            return gr.update(visible=True, value=filepath)
        except Exception as e:
            print(f"[ERROR] Doc export failed: {e}")
            return gr.update(visible=False, value=None)

    download_doc_btn.click(download_last_response, [chatbot], [doc_file_download])
    lab_download_doc_btn.click(download_last_response, [lab_chatbot], [lab_doc_file_download])

    # ==================== LAB CHAT EVENTS ====================

    def lab_user_submit(message, history, username):
        """Handle Lab user message submission"""
        msg_text = extract_message_content(message)
        if not msg_text or not msg_text.strip():
            return "", history
        new_history = history + [{"role": "user", "content": msg_text.strip()}]
        return "", new_history

    def lab_bot_respond(history, username, role):
        """Process Lab Agent response with streaming - typing animation during simulation"""
        if not history or len(history) < 1:
            yield history, gr.update()
            return

        last_msg = history[-1]
        if last_msg.get("role") != "user":
            yield history, gr.update()
            return

        user_message = extract_message_content(last_msg.get("content", ""))
        if not user_message:
            yield history, gr.update()
            return

        # Determine which agent indicator to show
        msg_lower = user_message.lower().strip()
        if msg_lower.startswith('use '):
            agent_id = user_message.strip()[4:].strip()
            try:
                agent_info = get_agent(agent_id)
                if agent_info:
                    indicator = f"**Active:** {agent_info.name}"
                else:
                    indicator = "**Active:** Lab Agent"
            except Exception:
                indicator = "**Active:** Lab Agent"
        elif msg_lower == 'cancel' or msg_lower == 'reset':
            indicator = "**Active:** Lab Agent"
        else:
            indicator = gr.update()  # Keep current indicator

        # Check role for restricted actions
        if msg_lower.startswith("deploy") and role != ROLE_SUPER_USER:
            history = history + [{"role": "assistant", "content": "**Access Denied**\n\nDeploying and simulating agents requires super_user privileges. Contact an administrator to upgrade your access."}]
            yield history, indicator
            return

        history = history + [{"role": "assistant", "content": ""}]
        accumulated = ""

        try:
            for chunk in ask_lab_stream(user_message, username or "anonymous"):
                accumulated += chunk
                history[-1]["content"] = accumulated
                yield history, indicator
        except Exception as e:
            print(f"[ERROR] Lab Agent Error: {str(e)}")
            history[-1]["content"] = accumulated + "\n\n**Error occurred**\n\nAn internal error occurred while processing your request. Please check the terminal logs for details and try again later."
            yield history, indicator

    def send_lab_command(command, history, username, role):
        """Send a command button to Lab Agent"""
        if role != ROLE_SUPER_USER and command == "deploy":
            new_history = history + [
                {"role": "user", "content": command},
                {"role": "assistant", "content": "**Access Denied**\n\nThis action requires super_user privileges."}
            ]
            return new_history

        new_history = history + [{"role": "user", "content": command}]
        try:
            response = ask_lab(command, username or "anonymous")
        except Exception as e:
            print(f"[ERROR] Lab Command Error: {str(e)}")  # Log error to terminal
            response = "**Error occurred**\n\nAn internal error occurred while processing your request. Please check the terminal logs for details and try again later."

        new_history = new_history + [{"role": "assistant", "content": response}]
        return new_history

    # Lab chat submit
    lab_msg.submit(
        lab_user_submit,
        [lab_msg, lab_chatbot, current_user],
        [lab_msg, lab_chatbot],
        queue=False
    ).then(
        lab_bot_respond,
        [lab_chatbot, current_user, user_role],
        [lab_chatbot, lab_agent_indicator]
    )

    lab_submit_btn.click(
        lab_user_submit,
        [lab_msg, lab_chatbot, current_user],
        [lab_msg, lab_chatbot],
        queue=False
    ).then(
        lab_bot_respond,
        [lab_chatbot, current_user, user_role],
        [lab_chatbot, lab_agent_indicator]
    )

    # Lab action buttons
    customize_btn.click(
        lambda h, u, r: send_lab_command("customize", h, u, r),
        [lab_chatbot, current_user, user_role],
        lab_chatbot
    )

    deploy_btn.click(
        lambda h, u, r: send_lab_command("deploy", h, u, r),
        [lab_chatbot, current_user, user_role],
        lab_chatbot
    )

    help_btn.click(
        lambda h, u, r: send_lab_command("help", h, u, r),
        [lab_chatbot, current_user, user_role],
        lab_chatbot
    )

    lab_clear_btn.click(
        lambda: [{"role": "assistant", "content": "**Welcome to Orion Lab**\n\nChat cleared. Type 'help' for available commands."}],
        None,
        lab_chatbot,
        queue=False
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True,
    )