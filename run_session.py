"""Runtime - every invocation. Requires AGENT_ID and ENV_ID from setup.sh (see .env / env vars)."""
import os
import sys

import anthropic

client = anthropic.Anthropic()

AGENT_ID = os.environ["AGENT_ID"]
ENV_ID = os.environ["ENV_ID"]


def run(task: str) -> None:
    session = client.beta.sessions.create(
        agent={"type": "agent", "id": AGENT_ID},
        environment_id=ENV_ID,
    )
    print(f"Trace: https://platform.claude.com/workspaces/default/sessions/{session.id}")

    # Stream-first: open the event stream before sending the kickoff.
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": task}]}],
        )

        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        print(block.text, end="", flush=True)
            elif event.type == "agent.tool_use":
                print(f"\n[Using tool: {event.name}]")
            elif event.type == "session.status_terminated":
                print("\n--- session terminated ---")
                break
            elif event.type == "session.status_idle":
                if event.stop_reason == "budget_reached":
                    # not terminal - only a budget change/removal resumes it
                    print("\n--- budget reached, not resuming ---")
                    break
                if event.stop_reason != "requires_action":
                    print("\n--- agent idle ---")
                    break


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "Say hello and describe what you can help with.")
