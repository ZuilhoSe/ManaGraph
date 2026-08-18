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
            # max_retries: a transient 429 (rate limit) retries the same request
            # with backoff instead of failing the whole agent run.
            return ChatGoogleGenerativeAI(model=model_name, google_api_key=os.getenv("GOOGLE_API_KEY"), max_retries=4)

        elif provider == "anthropic":
            return ChatAnthropic(model=model_name, anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"), max_retries=4)
        
        else:
            raise ValueError(f"LLM provider '{provider}' is not supported.")