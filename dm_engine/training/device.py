"""Device selection and shared training-step settings for the trainers.

Trainers default to "auto". Self-play, evaluation and games default to the CPU.
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


def play_device(parser, requested: str) -> str:
    """Resolve a play script's --device value, reporting a missing GPU as a usage error."""
    try:
        return get_device(requested).type
    except ValueError as exc:
        parser.error(str(exc))


def autocast(device: torch.device):
    """bfloat16 on the GPU, plain float32 on the CPU."""
    return torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda")


def warmup(optimizer: torch.optim.Optimizer, base_lr: float, step: int, warmup_steps: int) -> None:
    """Raise the learning rate evenly over the first steps; large batches need this."""
    if warmup_steps > 0:
        for group in optimizer.param_groups:
            group["lr"] = base_lr * min(1.0, (step + 1) / warmup_steps)


def optimizer_step(model: torch.nn.Module, optimizer: torch.optim.Optimizer, loss: torch.Tensor) -> None:
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP_NORM)
    optimizer.step()
