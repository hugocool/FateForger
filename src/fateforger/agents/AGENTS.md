
In **AutoGen AgentChat (stable)** you don’t have to “prompt-engineer JSON” manually: you can **bind a Pydantic model to an `AssistantAgent`** via `output_content_type`. When set, the agent’s *final* `Response.chat_message` becomes a `StructuredMessage[T]` whose `.content` is already parsed into your Pydantic type. ([microsoft.github.io][1])

That addresses “force the backend to conform” **as long as your model client supports structured output** (OpenAI/Azure clients do; other model clients may not). ([microsoft.github.io][2])

## 1) Constrain one agent to a Pydantic JSON schema

```python
from pydantic import BaseModel, Field

class MyJson(BaseModel):
    action: str
    payload: dict
    confidence: float = Field(ge=0.0, le=1.0)

    def unpack(self) -> str:
        return f"Action: {self.action}\nConfidence: {self.confidence:.2f}\nPayload: {self.payload}"
```

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

model_client = OpenAIChatCompletionClient(model="gpt-4o-mini")

json_agent = AssistantAgent(
    name="json_agent",
    model_client=model_client,
    description="Produces structured JSON outputs.",
    output_content_type=MyJson,  # <-- the schema lock
)
```

Now `json_agent`’s final message will be a `StructuredMessage[MyJson]` (not a `TextMessage`). ([microsoft.github.io][1])

## 2) Make the agent “unpack” and respond with plain text (wired into the agent)

If other agents (or your UI) should receive a normal `TextMessage`, wrap the `AssistantAgent` inside a **custom agent** that:

1. runs the inner structured agent,
2. reads `StructuredMessage.content` (the Pydantic object),
3. emits a `TextMessage` derived from `model.unpack()`.

This is the cleanest “middleware” pattern in AgentChat: implement `BaseChatAgent.on_messages()` and return a new `Response`. ([microsoft.github.io][3])

```python
from typing import Sequence, List, Optional

from autogen_agentchat.agents import BaseChatAgent, AssistantAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage, StructuredMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient

class UnpackingAgent(BaseChatAgent):
    def __init__(self, name: str, model_client: OpenAIChatCompletionClient):
        super().__init__(name, "Runs a structured agent, then unpacks to text.")
        self._inner = AssistantAgent(
            name=f"{name}__inner",
            model_client=model_client,
            description="Inner agent that MUST output structured JSON.",
            output_content_type=MyJson,
        )

    @property
    def produced_message_types(self):
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        inner_resp = await self._inner.on_messages(messages, cancellation_token)

        # Keep inner messages for debugging/auditing if you want
        inner_messages: List[BaseChatMessage] = []
        if inner_resp.inner_messages:
            inner_messages.extend(inner_resp.inner_messages)

        final = inner_resp.chat_message
        inner_messages.append(final)

        if isinstance(final, StructuredMessage):
            model: MyJson = final.content
            unpacked_text = model.unpack()
        elif isinstance(final, TextMessage):
            # Fallback if structured output wasn't honored for some reason
            unpacked_text = final.content
        else:
            unpacked_text = final.to_text()

        return Response(
            chat_message=TextMessage(content=unpacked_text, source=self.name),
            inner_messages=inner_messages,
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        await self._inner.on_reset(cancellation_token)

# Usage
model_client = OpenAIChatCompletionClient(model="gpt-4o-mini")
agent = UnpackingAgent("finalizer", model_client)
```

### What you get

* **Backend constraint**: inner agent uses `output_content_type=MyJson` and returns `StructuredMessage[MyJson]`. ([microsoft.github.io][1])
* **Frontend/plain output**: wrapper agent returns `TextMessage(unpacked_text)`.

## 3) Smaller alternative: keep `StructuredMessage` but render it nicely

If you’re fine with still returning a `StructuredMessage`, you can give it a `format_string` (or use `output_content_type_format` on `AssistantAgent`) so that `to_text()` prints a human-friendly view while keeping typed content. ([microsoft.github.io][4])

That’s useful when you want both:

* machine: `msg.content` (Pydantic object)
* human: `msg.to_text()` (formatted)

## Practical footnote (so you don’t get surprised)

Structured output is model/client-dependent; AutoGen explicitly notes it requires the model client to support it (OpenAI/Azure clients do). ([microsoft.github.io][2])

If you tell me which model client you’re using (OpenAI, Azure, LiteLLM, etc.), I can point out the exact behavior and the failure modes (e.g., what happens when the backend can’t enforce schema strictly).

[1]: https://microsoft.github.io/autogen/stable/_modules/autogen_agentchat/agents/_assistant_agent.html "autogen_agentchat.agents._assistant_agent — AutoGen"
[2]: https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/model-clients.html?utm_source=chatgpt.com "Model Clients — AutoGen"
[3]: https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/custom-agents.html "Custom Agents — AutoGen"
[4]: https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.messages.html "autogen_agentchat.messages — AutoGen"
