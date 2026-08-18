from chromadb.utils import embedding_functions


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class EmbeddingStrategy:
    def get_function(self):
        raise NotImplementedError


class MiniLMStrategy(EmbeddingStrategy):
    def get_function(self):
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2",
            device=_device(),
        )


# Later you can add another provider here:
# class BGEStrategy(EmbeddingStrategy): ...
