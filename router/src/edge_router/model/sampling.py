import torch

from edge_router.schemas import GenerationOptions


def sample_token(logits: torch.Tensor, options: GenerationOptions) -> torch.Tensor:
    logits = logits.float()
    if not torch.isfinite(logits).any(dim=-1).all() or torch.isnan(logits).any():
        raise RuntimeError("Invalid language-model logits")
    if options.temperature == 0:
        return logits.argmax(-1, keepdim=True)
    logits = logits / options.temperature
    if options.top_p < 1:
        values, indices = logits.sort(descending=True, dim=-1)
        cumulative = values.softmax(-1).cumsum(-1)
        remove = cumulative > options.top_p
        remove[..., 1:] = remove[..., :-1].clone()
        remove[..., 0] = False
        values = values.masked_fill(remove, -torch.inf)
        logits = torch.full_like(logits, -torch.inf).scatter(-1, indices, values)
    return torch.multinomial(logits.softmax(-1), num_samples=1)
