"""
TurboQuant: Online Vector Quantization for KV Cache Compression.

Implements the TurboQuant algorithm (arXiv:2504.19874) for near-optimal
KV cache quantization in transformers. The method is data-oblivious and
suitable for online/streaming inference.

Two quantization modes:
- Q_mse: MSE-optimal for value vectors (random rotation + scalar quantization)
- Q_prod: Inner-product-optimal for key vectors (MSE + QJL residual correction)

Adapted from MITS project (C:\\Work\\MITS\\src\\inference\\turbo_quant.py)
for standalone use in SupCom LLM AI Bot.

Reference:
    Zandieh, Daliri, Hadian, Mirrokni.
    "TurboQuant: Online Vector Quantization with Near-optimal Distortion Rate"
    arXiv:2504.19874, April 2025.
"""

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import torch
import numpy as np
from scipy.special import gamma as gamma_fn
import logging

# NumPy 2.0 compatibility: trapz was renamed to trapezoid
_trapz = getattr(np, "trapezoid", getattr(np, "trapz", None))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────

@dataclass
class TurboQuantConfig:
    """Configuration for TurboQuant quantization.

    Args:
        key_bits: Effective bit-width for key cache (uses Q_prod).
        value_bits: Effective bit-width for value cache (uses Q_mse).
        head_dim: Dimension of each attention head.
        seed: Random seed for reproducibility of rotation/projection matrices.
        outlier_channels: Number of high-variance channels to quantize at
            higher precision. Per Section 4.3 of the paper: channels are split
            into outlier and non-outlier sets with independent TurboQuant instances.
            E.g. 32 outlier channels at 3 bits + 96 regular at 2 bits = 2.5 effective.
            Set to 0 to disable mixed precision (uniform bit-width).
        outlier_bits_extra: Extra bits allocated to outlier channels above base.
            E.g. with key_bits=2 and outlier_bits_extra=1, outliers get 3 bits.
    """
    key_bits: int = 3
    value_bits: int = 3
    head_dim: int = 128
    seed: int = 42
    outlier_channels: int = 0
    outlier_bits_extra: int = 1


# ─────────────────────────────────────────────────────────────────────
# Beta distribution PDF for rotated coordinates
# ─────────────────────────────────────────────────────────────────────

def _beta_pdf(x: np.ndarray, d: int) -> np.ndarray:
    """PDF of a coordinate after random rotation of a unit-norm vector in R^d.

    After applying a random orthogonal rotation to a unit vector,
    each coordinate follows f_X(x) = C_d * (1 - x^2)^((d-3)/2)
    on [-1, 1].

    Args:
        x: Points at which to evaluate the PDF.
        d: Ambient dimension (head_dim).

    Returns:
        PDF values at each point.
    """
    coeff = gamma_fn(d / 2) / (math.sqrt(math.pi) * gamma_fn((d - 1) / 2))
    # Clamp to avoid numerical issues at boundaries
    x_clamp = np.clip(x, -1 + 1e-12, 1 - 1e-12)
    return coeff * np.power(1 - x_clamp ** 2, (d - 3) / 2)


# ─────────────────────────────────────────────────────────────────────
# Lloyd-Max codebook computation
# ─────────────────────────────────────────────────────────────────────

def compute_codebook(d: int, b: int, n_integration_points: int = 2000) -> np.ndarray:
    """Compute MSE-optimal scalar quantizer centroids via Lloyd-Max iteration.

    Designs a 2^b-level quantizer for the Beta distribution induced by
    random rotation of a unit vector in R^d.

    Args:
        d: Ambient dimension (head_dim).
        b: Bit-width (number of bits per coordinate).
        n_integration_points: Points for numerical integration.

    Returns:
        Sorted centroid array of shape (2^b,).
    """
    num_levels = 2 ** b
    x_grid = np.linspace(-1, 1, n_integration_points)
    pdf_vals = _beta_pdf(x_grid, d)

    # Initialize centroids uniformly
    centroids = np.linspace(-0.9, 0.9, num_levels)

    # Lloyd-Max iterations
    for _ in range(200):
        # Compute boundaries (midpoints between consecutive centroids)
        boundaries = np.concatenate([
            [-1.0],
            (centroids[:-1] + centroids[1:]) / 2,
            [1.0],
        ])

        new_centroids = np.zeros(num_levels)
        for i in range(num_levels):
            mask = (x_grid >= boundaries[i]) & (x_grid < boundaries[i + 1])
            if mask.sum() == 0:
                new_centroids[i] = centroids[i]
                continue
            weights = pdf_vals[mask]
            total_weight = _trapz(weights, x_grid[mask])
            if total_weight < 1e-15:
                new_centroids[i] = centroids[i]
                continue
            weighted_x = _trapz(x_grid[mask] * weights, x_grid[mask])
            new_centroids[i] = weighted_x / total_weight

        if np.max(np.abs(new_centroids - centroids)) < 1e-10:
            break
        centroids = new_centroids

    return np.sort(centroids)


# ─────────────────────────────────────────────────────────────────────
# Precomputed codebook registry
# ─────────────────────────────────────────────────────────────────────

_CODEBOOK_CACHE: Dict[Tuple[int, int], torch.Tensor] = {}


def get_codebook(d: int, b: int, device: torch.device = torch.device("cpu")) -> torch.Tensor:
    """Get or compute codebook centroids, cached globally.

    Args:
        d: Dimension (head_dim).
        b: Bit-width.
        device: Target device.

    Returns:
        Tensor of shape (2^b,) with sorted centroids.
    """
    key = (d, b)
    if key not in _CODEBOOK_CACHE:
        centroids_np = compute_codebook(d, b)
        _CODEBOOK_CACHE[key] = torch.from_numpy(centroids_np).float()
        logger.info(f"TurboQuant codebook computed: d={d}, b={b}, levels={2**b}")
    return _CODEBOOK_CACHE[key].to(device)


# ─────────────────────────────────────────────────────────────────────
# Random rotation matrix
# ─────────────────────────────────────────────────────────────────────

def generate_rotation_matrix(
    d: int,
    seed: int = 42,
    device: torch.device = torch.device("cpu"),
) -> torch.Tensor:
    """Generate a random orthogonal rotation matrix via QR decomposition.

    Args:
        d: Dimension (head_dim).
        seed: Random seed for reproducibility.
        device: Target device.

    Returns:
        Orthogonal matrix of shape (d, d).
    """
    rng = torch.Generator().manual_seed(seed)
    M = torch.randn(d, d, generator=rng)
    Q, R = torch.linalg.qr(M)
    # Ensure proper rotation (det = +1) by correcting sign
    diag_sign = torch.sign(torch.diag(R))
    diag_sign[diag_sign == 0] = 1.0
    Q = Q * diag_sign.unsqueeze(0)
    return Q.to(device)


# ─────────────────────────────────────────────────────────────────────
# QJL projection matrix
# ─────────────────────────────────────────────────────────────────────

def generate_qjl_matrix(
    d: int,
    seed: int = 43,
    device: torch.device = torch.device("cpu"),
) -> torch.Tensor:
    """Generate random Gaussian projection matrix for QJL transform.

    Args:
        d: Dimension (head_dim).
        seed: Random seed (different from rotation seed).
        device: Target device.

    Returns:
        Matrix of shape (d, d) with i.i.d. N(0,1) entries.
    """
    rng = torch.Generator().manual_seed(seed)
    S = torch.randn(d, d, generator=rng)
    return S.to(device)


# ─────────────────────────────────────────────────────────────────────
# TurboQuant Engine
# ─────────────────────────────────────────────────────────────────────

class TurboQuantEngine:
    """Stateful engine holding precomputed matrices and codebooks.

    Manages rotation matrix Pi, QJL projection matrix S, and codebooks
    for a specific (head_dim, key_bits, value_bits) configuration.

    All quantization methods are fully vectorized over batch dimensions.
    """

    def __init__(self, config: TurboQuantConfig, device: torch.device = torch.device("cpu")):
        self.config = config
        self.device = device
        d = config.head_dim
        self.use_mixed_precision = config.outlier_channels > 0

        # Precompute rotation matrix (shared for keys and values)
        self.Pi = generate_rotation_matrix(d, seed=config.seed, device=device)
        self.Pi_T = self.Pi.T.contiguous()

        # Precompute QJL matrix (for key inner-product preservation)
        self.S = generate_qjl_matrix(d, seed=config.seed + 1, device=device)
        self.S_T = self.S.T.contiguous()

        # QJL dequantization coefficient: sqrt(pi/2) / d
        self.qjl_coeff = math.sqrt(math.pi / 2) / d

        if self.use_mixed_precision:
            n_out = config.outlier_channels
            n_reg = d - n_out
            key_bits_out = config.key_bits + config.outlier_bits_extra
            key_bits_reg = config.key_bits
            val_bits_out = config.value_bits + config.outlier_bits_extra
            val_bits_reg = config.value_bits

            self.key_codebook_outlier = get_codebook(d, key_bits_out - 1, device)
            self.key_codebook_regular = get_codebook(d, key_bits_reg - 1, device)
            self.value_codebook_outlier = get_codebook(d, val_bits_out, device)
            self.value_codebook_regular = get_codebook(d, val_bits_reg, device)

            self.n_outlier = n_out
            self.n_regular = n_reg
            self._outlier_mask: Optional[torch.Tensor] = None

            eff_key = (n_out * key_bits_out + n_reg * key_bits_reg) / d
            eff_val = (n_out * val_bits_out + n_reg * val_bits_reg) / d
            logger.info(
                f"TurboQuantEngine initialized (mixed precision): head_dim={d}, "
                f"outlier_channels={n_out}, "
                f"effective_key_bits={eff_key:.1f}, effective_value_bits={eff_val:.1f}"
            )
        else:
            self.key_codebook = get_codebook(d, config.key_bits - 1, device)
            self.value_codebook = get_codebook(d, config.value_bits, device)

            logger.info(
                f"TurboQuantEngine initialized: head_dim={d}, "
                f"key_bits={config.key_bits} (Q_prod: {config.key_bits-1}+1), "
                f"value_bits={config.value_bits} (Q_mse)"
            )

    def to(self, device: torch.device) -> "TurboQuantEngine":
        """Move all matrices to a new device."""
        self.device = device
        self.Pi = self.Pi.to(device)
        self.Pi_T = self.Pi_T.to(device)
        self.S = self.S.to(device)
        self.S_T = self.S_T.to(device)
        if self.use_mixed_precision:
            self.key_codebook_outlier = self.key_codebook_outlier.to(device)
            self.key_codebook_regular = self.key_codebook_regular.to(device)
            self.value_codebook_outlier = self.value_codebook_outlier.to(device)
            self.value_codebook_regular = self.value_codebook_regular.to(device)
            if self._outlier_mask is not None:
                self._outlier_mask = self._outlier_mask.to(device)
        else:
            self.key_codebook = self.key_codebook.to(device)
            self.value_codebook = self.value_codebook.to(device)
        return self

    # ── MSE Quantization (for values) ────────────────────────────────

    def _rotate(self, x: torch.Tensor) -> torch.Tensor:
        """Apply random rotation: y = x @ Pi^T."""
        return x @ self.Pi_T

    def _unrotate(self, y: torch.Tensor) -> torch.Tensor:
        """Inverse rotation: x = y @ Pi."""
        return y @ self.Pi

    def _scalar_quantize(self, y: torch.Tensor, codebook: torch.Tensor) -> torch.Tensor:
        """Per-coordinate scalar quantization: find nearest centroid index."""
        distances = torch.abs(y.unsqueeze(-1) - codebook)
        return distances.argmin(dim=-1).to(torch.int16)

    def _scalar_dequantize(self, indices: torch.Tensor, codebook: torch.Tensor) -> torch.Tensor:
        """Reconstruct from centroid indices."""
        return codebook[indices.long()]

    def _detect_outlier_channels(self, x: torch.Tensor) -> torch.Tensor:
        """Identify outlier channels by per-channel variance."""
        flat = x.reshape(-1, x.shape[-1])
        var_per_channel = flat.var(dim=0)
        _, top_indices = torch.topk(var_per_channel, self.n_outlier)
        mask = torch.zeros(x.shape[-1], dtype=torch.bool, device=x.device)
        mask[top_indices] = True
        return mask

    def _get_outlier_mask(self, x: torch.Tensor) -> torch.Tensor:
        """Get or compute outlier channel mask (computed once, cached)."""
        if self._outlier_mask is None:
            self._outlier_mask = self._detect_outlier_channels(x)
        return self._outlier_mask

    def quantize_mse(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """MSE-optimal quantization (for value cache).

        Steps: normalize → rotate → scalar quantize (mixed precision if configured).
        """
        norms = torch.norm(x, dim=-1, keepdim=True).clamp(min=1e-8)
        x_unit = x / norms
        y = self._rotate(x_unit)

        if self.use_mixed_precision:
            mask = self._get_outlier_mask(x)
            indices = torch.zeros_like(y, dtype=torch.int16)
            indices[..., mask] = self._scalar_quantize(y[..., mask], self.value_codebook_outlier)
            indices[..., ~mask] = self._scalar_quantize(y[..., ~mask], self.value_codebook_regular)
        else:
            indices = self._scalar_quantize(y, self.value_codebook)

        return indices, norms.squeeze(-1)

    def dequantize_mse(self, indices: torch.Tensor, norms: torch.Tensor) -> torch.Tensor:
        """Dequantize MSE-quantized vectors."""
        if self.use_mixed_precision:
            mask = self._outlier_mask
            y_recon = torch.zeros(*indices.shape, dtype=torch.float32, device=indices.device)
            y_recon[..., mask] = self._scalar_dequantize(indices[..., mask], self.value_codebook_outlier)
            y_recon[..., ~mask] = self._scalar_dequantize(indices[..., ~mask], self.value_codebook_regular)
        else:
            y_recon = self._scalar_dequantize(indices, self.value_codebook)

        x_recon = self._unrotate(y_recon)
        return x_recon * norms.unsqueeze(-1)

    # ── Inner-Product Quantization (for keys) ────────────────────────

    def quantize_prod(
        self, x: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Inner-product-optimal two-stage quantization (for key cache).

        Stage 1: MSE quantizer with (b-1) bits on unit-norm vector.
        Stage 2: 1-bit QJL on the residual.
        """
        norms = torch.norm(x, dim=-1, keepdim=True).clamp(min=1e-8)
        x_unit = x / norms
        y = self._rotate(x_unit)

        if self.use_mixed_precision:
            mask = self._get_outlier_mask(x)
            mse_indices = torch.zeros_like(y, dtype=torch.int16)
            mse_indices[..., mask] = self._scalar_quantize(y[..., mask], self.key_codebook_outlier)
            mse_indices[..., ~mask] = self._scalar_quantize(y[..., ~mask], self.key_codebook_regular)
            y_recon = torch.zeros_like(y)
            y_recon[..., mask] = self._scalar_dequantize(mse_indices[..., mask], self.key_codebook_outlier)
            y_recon[..., ~mask] = self._scalar_dequantize(mse_indices[..., ~mask], self.key_codebook_regular)
        else:
            mse_indices = self._scalar_quantize(y, self.key_codebook)
            y_recon = self._scalar_dequantize(mse_indices, self.key_codebook)

        x_mse_recon = self._unrotate(y_recon)
        residual = x_unit - x_mse_recon
        residual_norms = torch.norm(residual, dim=-1)

        proj = residual @ self.S_T
        qjl_signs = torch.sign(proj).to(torch.int8)
        qjl_signs[qjl_signs == 0] = 1

        return mse_indices, qjl_signs, residual_norms, norms.squeeze(-1)

    def dequantize_prod(
        self,
        mse_indices: torch.Tensor,
        qjl_signs: torch.Tensor,
        residual_norms: torch.Tensor,
        norms: torch.Tensor,
    ) -> torch.Tensor:
        """Dequantize inner-product-quantized vectors."""
        if self.use_mixed_precision:
            mask = self._outlier_mask
            y_recon = torch.zeros(*mse_indices.shape, dtype=torch.float32, device=mse_indices.device)
            y_recon[..., mask] = self._scalar_dequantize(mse_indices[..., mask], self.key_codebook_outlier)
            y_recon[..., ~mask] = self._scalar_dequantize(mse_indices[..., ~mask], self.key_codebook_regular)
        else:
            y_recon = self._scalar_dequantize(mse_indices, self.key_codebook)

        x_mse = self._unrotate(y_recon)
        qjl_float = qjl_signs.float()
        x_qjl = self.qjl_coeff * residual_norms.unsqueeze(-1) * (qjl_float @ self.S)
        x_unit_recon = x_mse + x_qjl
        return x_unit_recon * norms.unsqueeze(-1)

    # ── Convenience: quantize/dequantize with mode ───────────────────

    def quantize(self, x: torch.Tensor, mode: str = "mse") -> Dict[str, torch.Tensor]:
        """Quantize vectors with specified mode ('mse' for values, 'prod' for keys)."""
        if mode == "mse":
            indices, norms = self.quantize_mse(x)
            return {"indices": indices, "norms": norms}
        elif mode == "prod":
            mse_idx, qjl_signs, res_norms, norms = self.quantize_prod(x)
            return {
                "mse_indices": mse_idx,
                "qjl_signs": qjl_signs,
                "residual_norms": res_norms,
                "norms": norms,
            }
        else:
            raise ValueError(f"Unknown quantization mode: {mode}. Use 'mse' or 'prod'.")

    def dequantize(self, quantized: Dict[str, torch.Tensor], mode: str = "mse") -> torch.Tensor:
        """Dequantize vectors from quantized representation."""
        if mode == "mse":
            return self.dequantize_mse(quantized["indices"], quantized["norms"])
        elif mode == "prod":
            return self.dequantize_prod(
                quantized["mse_indices"],
                quantized["qjl_signs"],
                quantized["residual_norms"],
                quantized["norms"],
            )
        else:
            raise ValueError(f"Unknown quantization mode: {mode}. Use 'mse' or 'prod'.")

    # ── Memory estimation ────────────────────────────────────────────

    def estimate_memory_bytes(
        self,
        seq_len: int,
        num_heads: int,
        batch_size: int = 1,
    ) -> Dict[str, int]:
        """Estimate memory usage for quantized KV cache per layer."""
        d = self.config.head_dim
        n = batch_size * num_heads * seq_len

        original = 2 * n * d * 2
        key_bits_per_vec = (self.config.key_bits - 1) * d + d
        key_bytes = n * (math.ceil(key_bits_per_vec / 8) + 8)
        value_bits_per_vec = self.config.value_bits * d
        value_bytes = n * (math.ceil(value_bits_per_vec / 8) + 4)

        quantized = key_bytes + value_bytes
        compression_ratio = original / quantized if quantized > 0 else float("inf")

        return {
            "original_bytes": original,
            "quantized_bytes": quantized,
            "key_bytes": key_bytes,
            "value_bytes": value_bytes,
            "compression_ratio": round(compression_ratio, 2),
        }
