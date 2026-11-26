"""Device selection and shared training-step settings for the trainers.

Self-play and evaluation stay on the CPU; only training uses the GPU.
"""

from __future__ import annotations

import torch

GRAD_CLIP_NORM = 1.0


def get_device(prefer: str = "auto") -> torch.device:
    """Resolve "auto", "cpu" or "cuda". Asking for cuda without a GPU is an error."""
    if prefer == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        return torch.device("cuda")
    if prefer == "cuda":
        raise ValueError("--device cuda was requested but PyTorch cannot see a GPU")
    return torch.device("cpu")


def autocast(device: torch.device):
    """bfloat16 on the GPU, plain float32 on the CPU."""
    return torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda")


def optimizer_step(model: torch.nn.Module, optimizer: torch.optim.Optimizer, loss: torch.Tensor) -> None:
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP_NORM)
    optimizer.step()
