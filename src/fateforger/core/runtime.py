from dataclasses import dataclass
from typing import Callable

from autogen_core import (
    AgentId,
    DefaultTopicId,
    MessageContext,
    RoutedAgent,
    SingleThreadedAgentRuntime,
    default_subscription,
    message_handler,
)


# Import agents
from fateforger.agents.schedular.agent import PlannerAgent


async def initialize_runtime() -> SingleThreadedAgentRuntime:
    """Initialize the runtime with all agents."""
    runtime = SingleThreadedAgentRuntime()
    await PlannerAgent.register(
        runtime, "planner_agent", lambda: PlannerAgent("planner_agent")
    )
    return runtime


# in this file we register the agents

# @dataclass
# class Message:
#     content: int


# @default_subscription
# class Modifier(RoutedAgent):
#     def __init__(self, modify_val: Callable[[int], int]) -> None:
#         super().__init__("A modifier agent.")
#         self._modify_val = modify_val

#     @message_handler
#     async def handle_message(self, message: Message, ctx: MessageContext) -> None:
#         val = self._modify_val(message.content)
#         print(f"{'-'*80}\nModifier:\nModified {message.content} to {val}")
#         await self.publish_message(Message(content=val), DefaultTopicId())  # type: ignore


# @default_subscription
# class Checker(RoutedAgent):
#     def __init__(self, run_until: Callable[[int], bool]) -> None:
#         super().__init__("A checker agent.")
#         self._run_until = run_until

#     @message_handler
#     async def handle_message(self, message: Message, ctx: MessageContext) -> None:
#         if not self._run_until(message.content):
#             print(f"{'-'*80}\nChecker:\n{message.content} passed the check, continue.")
#             await self.publish_message(Message(content=message.content), DefaultTopicId())
#         else:
#             print(f"{'-'*80}\nChecker:\n{message.content} failed the check, stopping.")

# # Create a local embedded runtime.


# await Schedular.register(
#     runtime,
#     "schedular",
#     lambda

# )

# # Register the modifier and checker agents by providing
# # their agent types, the factory functions for creating instance and subscriptions.
# await Modifier.register(
#     runtime,
#     "modifier",
#     # Modify the value by subtracting 1
#     lambda: Modifier(modify_val=lambda x: x - 1),
# )

# await Checker.register(
#     runtime,
#     "checker",
#     # Run until the value is less than or equal to 1
#     lambda: Checker(run_until=lambda x: x <= 1),
# )

# # Start the runtime and send a direct message to the checker.
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
