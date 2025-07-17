"""
Router Agent for agent-to-agent handoff using simple routing.

This module provides a lightweight routing agent that decides which
downstream agent should handle haunter payloads. Uses direct OpenAI
calls for minimal cost routing decisions.
"""

import json
import logging
from typing import Any, Dict, Optional

import openai

from ..actions.haunt_payload import HauntPayload
from ..common import get_config, get_logger

logger = get_logger("router_agent")

# System prompt for routing decisions
ROUTER_SYSTEM_PROMPT = """
You are a stateless router for productivity bot actions.

Your job is to route haunter payloads to the appropriate downstream agent.
Currently, all payloads should be routed to the "planner" agent.

Always respond with valid JSON in this exact format:
{"target": "planner", "payload": <verbatim input payload>}

Do not modify the payload content - pass it through exactly as received.
Do not add explanations or extra text - only return the JSON response.
"""


class RouterAgent:
    """
    Lightweight routing agent for haunter payloads.
    
    This agent uses a very cheap model to make routing decisions
    with minimal token cost. Currently routes all payloads to
    the PlanningAgent, but designed for future extensibility.
    """
    
    def __init__(self):
        """Initialize the router agent."""
        config = get_config()
        self.client = openai.AsyncOpenAI(api_key=config.openai_api_key)
        logger.info("RouterAgent initialized")
    
    async def route_payload(self, payload: HauntPayload) -> Dict[str, Any]:
        """
        Route a haunter payload to the appropriate agent.
        
        Args:
            payload: The HauntPayload to route
            
        Returns:
            Dictionary with target agent and routed payload
            
        Raises:
            Exception: If routing fails
        """
        try:
            logger.info(f"Routing payload: {payload}")
            
            # Convert payload to string for the router
            payload_str = json.dumps(payload.to_dict())
            
            # Send to router via OpenAI
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo-0125",
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": payload_str}
                ],
                temperature=0.1,
                max_tokens=200,
            )
            
            # Parse the response
            response_content = response.choices[0].message.content
            
            # Parse JSON response
            if response_content:
                try:
                    result = json.loads(response_content)
                    logger.info(f"Router decision: {result.get('target')}")
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse router response: {response_content}")
            else:
                logger.error("Empty response from router")
            
            # Fallback to default routing
            return {
                "target": "planner",
                "payload": payload.to_dict()
            }
                
        except Exception as e:
            logger.error(f"Routing failed for payload {payload}: {e}")
            # Fallback to default routing
            return {
                "target": "planner", 
                "payload": payload.to_dict()
            }


# Global router instance
_router_instance: Optional[RouterAgent] = None


def get_router_agent() -> RouterAgent:
    """
    Get the global router agent instance.
    
    Returns:
        RouterAgent instance (singleton)
    """
    global _router_instance
    if _router_instance is None:
        _router_instance = RouterAgent()
    return _router_instance


async def route_haunt_payload(payload: HauntPayload) -> Dict[str, Any]:
    """
    Route a haunter payload using the global router agent.
    
    Args:
        payload: The HauntPayload to route
        
    Returns:
        Dictionary with routing decision and payload
    """
    router = get_router_agent()
    return await router.route_payload(payload)
