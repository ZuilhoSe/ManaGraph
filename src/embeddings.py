from chromadb.utils import embedding_functions

class EmbeddingStrategy:
    def get_function(self):
        raise NotImplementedError

class MiniLMStrategy(EmbeddingStrategy):
    def get_function(self):
        return embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

# Futuramente, você apenas adicionaria aqui:
# class BGEStrategy(EmbeddingStrategy): ...