"""Local Magentic multi-agent demo using Microsoft Agent Framework."""

from __future__ import annotations

import os
from typing import Any, cast

import gradio as gr
from dotenv import load_dotenv

from agent_framework import Agent, AgentResponseUpdate
from agent_framework.openai import OpenAIChatClient
from agent_framework.orchestrations import MagenticBuilder
from agent_framework_orchestrations import MagenticOrchestrator
from azure.identity import AzureCliCredential


DEFAULT_TASK = (
    "Prepare a concise decision brief on how a large regulated financial-services "
    "company should introduce agentic AI into a customer-support workflow. Cover "
    "the target architecture, human approval points, evaluation and monitoring, "
    "security and governance risks, and a phased 90-day pilot. Separate established "
    "recommendations from assumptions, and finish with measurable success criteria."
)


def required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {name}. Copy .env.example to .env and fill in your Azure OpenAI settings."
        )
    return value


def build_workflow():
    # Prefer an app-specific variable. Fall back to the earlier demo variable
    # for existing .env files, then remove it to avoid endpoint/base_url conflict.
    endpoint = os.getenv("FOUNDRY_OPENAI_BASE_URL", "").strip()
    if not endpoint:
        endpoint = required_setting("AZURE_OPENAI_ENDPOINT")
    deployment = required_setting("AZURE_OPENAI_CHAT_MODEL")
    # Foundry Models' OpenAI v1 base URL is /openai/v1/, not the /responses route.
    # Accept a pasted /responses URL and normalize it for the SDK.
    base_url = endpoint.removesuffix("/responses").rstrip("/")
    if not base_url.endswith("/openai/v1"):
        raise RuntimeError(
            "FOUNDRY_OPENAI_BASE_URL must be the Foundry v1 base URL ending in "
            "/openai/v1/ (do not include /responses)."
        )
    base_url += "/"
    os.environ.pop("AZURE_OPENAI_ENDPOINT", None)

    # AzureCliCredential uses the signed-in identity created by `az login`.
    # Explicit Azure routing prevents accidental use of OPENAI_* variables.
    client = OpenAIChatClient(
        model=deployment,
        base_url=base_url,
        credential=AzureCliCredential(),
    )

    researcher = Agent(
        name="Researcher",
        description="Develops domain context, assumptions, and evidence needs.",
        instructions=(
            "You are a careful enterprise AI researcher. Identify relevant context, "
            "assumptions, risks, and questions that need validation. Do not invent "
            "citations or claim to have browsed the web."
        ),
        client=client,
    )
    architect = Agent(
        name="SolutionArchitect",
        description="Designs secure, practical enterprise agentic AI architectures.",
        instructions=(
            "You are an enterprise AI solution architect. Propose a practical "
            "architecture, workflow boundaries, human approval points, and a phased "
            "implementation. Make trade-offs explicit."
        ),
        client=client,
    )
    risk_reviewer = Agent(
        name="RiskReviewer",
        description="Reviews AI systems for security, governance, evaluation, and operational risks.",
        instructions=(
            "You are a skeptical AI risk and operations reviewer. Find failure modes, "
            "security and privacy concerns, evaluation gaps, and operational controls. "
            "Offer concrete mitigations."
        ),
        client=client,
    )
    manager = Agent(
        name="MagenticManager",
        description="Coordinates specialists and synthesizes the decision brief.",
        instructions=(
            "Coordinate the specialist team to answer the user's request. Delegate "
            "work to the best-fit participant, assess progress, and replan when needed. "
            "The final answer should be concise, actionable, and clearly label assumptions."
        ),
        client=client,
    )

    return MagenticBuilder(
        participants=[researcher, architect, risk_reviewer],
        intermediate_output_from=[researcher, architect, risk_reviewer],
        manager_agent=manager,
        max_round_count=8,
        max_stall_count=3,
        max_reset_count=2,
    ).build()


def conversation_task(message: str, history: list[dict[str, Any]] | None) -> str:
    """Carry recent UI chat turns into this run; Magentic runs are stateless per turn."""
    prior = []
    for item in (history or [])[-8:]:
        role = str(item.get("role", "assistant"))
        content = item.get("content", "")
        if isinstance(content, str) and content.strip():
            prior.append(f"{role}: {content.strip()}")
    if not prior:
        return message
    return (
        "Conversation so far (for context):\n"
        + "\n".join(prior)
        + "\n\nCurrent user request:\n"
        + message
    )


async def respond(message: str, history: list[dict[str, Any]] | None):
    """Stream participant activity while keeping a normal chat transcript."""
    message = (message or "").strip()
    chat = list(history or [])
    if not message:
        yield chat, "Enter a message to start the workflow.", "Ready", ""
        return

    task = conversation_task(message, history)
    chat.extend([
        {"role": "user", "content": message},
        {"role": "assistant", "content": "The Magentic team is planning…"},
    ])
    activity = "### Magentic activity\n\nStarting the workflow…"
    yield chat, activity, "Running", ""

    workflow = build_workflow()
    final_text: str | None = None
    last_message_id: str | None = None

    async for event in workflow.run(task, stream=True):
        if event.type in ("intermediate", "output"):
            if event.executor_id == MagenticOrchestrator.MANAGER_NAME:
                manager_response = cast(AgentResponseUpdate, event.data)
                final_text = manager_response.text
                continue

            update = cast(AgentResponseUpdate, event.data)
            message_id = update.message_id or event.executor_id
            if message_id != last_message_id:
                activity += f"\n\n**{event.executor_id}**\n\n"
                last_message_id = message_id
            activity += str(update)
            yield chat, activity, f"Working · {event.executor_id}", ""

        elif event.type == "magentic_orchestrator":
            event_name = event.data.event_type.name.replace("_", " ").title()
            activity += f"\n\n---\n**Magentic: {event_name}**\n\n"
            content = event.data.content
            if hasattr(content, "text"):
                activity += content.text
            elif hasattr(content, "to_dict"):
                activity += f"```json\n{content.to_dict()}\n```"
            yield chat, activity, event_name, ""

    if final_text is None:
        raise RuntimeError("Workflow completed without a final manager response.")

    chat[-1] = {"role": "assistant", "content": final_text}
    yield chat, activity, "Complete", ""


def create_app() -> gr.Blocks:
    with gr.Blocks(title="Magentic Agent Team") as demo:
        gr.Markdown(
            "# Magentic Agent Team\n"
            "Chat with a manager that delegates to research, solution-architecture, "
            "and risk-review agents. Watch their streamed contributions below."
        )
        with gr.Row():
            with gr.Column(scale=3):
                # Gradio 6 uses messages format by default and removed `type`.
                chat = gr.Chatbot(label="Conversation", height=560)
                message = gr.Textbox(
                    label="Your message",
                    placeholder="Ask the agent team a question…",
                    lines=2,
                )
                with gr.Row():
                    send = gr.Button("Send", variant="primary")
                    clear = gr.Button("Clear conversation")
                status = gr.Markdown("Ready")
            with gr.Column(scale=2):
                activity = gr.Markdown(
                    "Agent messages and Magentic planning/progress events will appear here.",
                )

        send.click(respond, inputs=[message, chat], outputs=[chat, activity, status, message])
        message.submit(respond, inputs=[message, chat], outputs=[chat, activity, status, message])
        clear.click(
            lambda: ([], "Conversation cleared.", "Ready", ""),
            outputs=[chat, activity, status, message],
        )

    return demo


if __name__ == "__main__":
    load_dotenv()
    create_app().queue().launch(server_name="127.0.0.1", server_port=7860)
