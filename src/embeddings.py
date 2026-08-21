import threading
from chromadb.utils import embedding_functions

_MODEL_NAME = "all-MiniLM-L6-v2"
_cached_function = None
_cache_lock = threading.Lock()


def embedding_device() -> str:
    """Prefer CUDA whenever PyTorch can see a GPU."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _device() -> str:
    """Backward-compatible alias."""
    return embedding_device()


def describe_embedding_device() -> str:
    device = embedding_device()
    if device != "cuda":
        return "cpu"
    import torch

    idx = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(idx)
    gib = props.total_memory / (1024**3)
    return f"cuda:{idx} ({torch.cuda.get_device_name(idx)}, {gib:.1f} GiB)"


def default_encode_batch() -> int:
    """Larger batches on GPU; MiniLM-L6 is small enough for 256 on typical cards."""
    return 256 if embedding_device() == "cuda" else 64


def place_model_on_device(model) -> str:
    """Move a SentenceTransformer (or nn.Module) onto CUDA when available."""
    device = embedding_device()
    if device != "cuda":
        return device
    import torch

    torch.backends.cudnn.benchmark = True
    model.to(device)
    return device


def encode_texts(model, texts, batch_size: int | None = None, normalize: bool = True):
    """Run MiniLM encode on CUDA when possible."""
    device = place_model_on_device(model)
    if batch_size is None:
        batch_size = default_encode_batch()
    kwargs = {
        "batch_size": batch_size,
        "convert_to_numpy": True,
        "normalize_embeddings": normalize,
        "show_progress_bar": True,
    }
    try:
        return model.encode(texts, device=device, **kwargs)
    except TypeError:
        return model.encode(texts, **kwargs)


class EmbeddingStrategy:
    def get_function(self):
        raise NotImplementedError


class MiniLMStrategy(EmbeddingStrategy):
    def get_function(self):
        global _cached_function
        with _cache_lock:
            if _cached_function is None:
                device = embedding_device()
                _cached_function = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=_MODEL_NAME,
                    device=device,
                )
                place_model_on_device(_cached_function._model)
            return _cached_function


# Later you can add another provider here:
# class BGEStrategy(EmbeddingStrategy): ...
