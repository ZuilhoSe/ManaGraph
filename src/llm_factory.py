import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

load_dotenv()

class LLMFactory:
    @staticmethod
    def get_llm():
        provider = os.getenv("LLM_PROVIDER", "google").lower()
        model_name = os.getenv("LLM_MODEL")

        if provider == "google":
            # max_retries must be a constructor field, not a `.bind(http_options=...)`
            # kwarg: every caller of this factory (ArchitectAgent, InventoryAgent) hands
            # the model to langgraph's create_react_agent, which calls .bind_tools(tools)
            # on it. RunnableBinding.__getattr__ delegates that call straight to the
            # wrapped model and returns a *fresh* binding carrying only {"tools": [...]}
            # -- any earlier `.bind(http_options=...)` kwargs are silently dropped, so a
            # bound retry config never actually reached a single request. Confirmed empirically:
            # `model.bind(http_options={...}).bind_tools([]).kwargs` == {"tools": [...]},
            # the http_options key is just gone. max_retries survives because it's a real
            # field on the model instance itself, not a runtime-only bound kwarg.
            # The free tier's 429 replies name a retryDelay of ~5-60s (a per-minute
            # token quota, not a short blip); the default exponential backoff (1s
            # doubling, 60s cap) needs several attempts before a wait is even in that
            # range, so bump the attempt count well past the SDK default (6) instead of
            # trying to force a flat delay we can't actually configure from here.
            return ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=os.getenv("GOOGLE_API_KEY"),
                max_retries=8,
            )

        elif provider == "anthropic":
            return ChatAnthropic(model=model_name, anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=4)
        
        else:
            raise ValueError(f"LLM provider '{provider}' is not supported.")