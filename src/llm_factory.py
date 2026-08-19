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
            llm = ChatGoogleGenerativeAI(model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"))
            # The free tier's 429 replies name a retryDelay around 45-60s (a
            # per-minute token quota, not a short blip), so the default
            # exponential backoff -- which starts at 1s -- gives up long before
            # that. Wait a flat 60s between attempts instead: up to 2 retries,
            # each preceded by a real 60s pause, so a same-run retry actually
            # has a shot at landing after the quota window rolls over.
            return llm.bind(
                http_options={
                    "retry_options": {
                        "attempts": 3,
                        "initial_delay": 60.0,
                        "max_delay": 60.0,
                        "exp_base": 1.0,
                        "jitter": 0.0,
                    }
                }
            )

        elif provider == "anthropic":
            return ChatAnthropic(model=model_name, anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=4)
        
        else:
            raise ValueError(f"LLM provider '{provider}' is not supported.")