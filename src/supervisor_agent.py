from llm_factory import LLMFactory
from langchain_core.messages import HumanMessage, SystemMessage

class SupervisorAgent:
    def __init__(self):
        self.llm = LLMFactory.get_llm()
        self.system_prompt = """
        You are the Supervisor of a Magic: The Gathering deckbuilding system.
        Your job is to analyze the Architect's response and verify if it fully meets the user's request (synergies, colors, and inventory constraints).
        
        CRITICAL RULE:
        - If the Architect's response is satisfactory and answers the user's prompt, your response MUST begin exactly with the word "APPROVED".
        - If it needs corrections, explain what is missing or wrong, and DO NOT use the word "APPROVED".
        """

    def evaluate(self, user_query: str, architect_response: str):
        prompt = f"""
        User's original request: "{user_query}"
        
        Architect's response:
        "{architect_response}"
        
        Evaluate if the goal was met.
        """
        
        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=prompt)
        ]
        
        result = self.llm.invoke(messages)
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(block.get("text") or "")
                else:
                    parts.append(getattr(block, "text", "") or "")
            return "".join(parts)
        return str(content)