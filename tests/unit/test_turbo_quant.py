"""Tests for TurboQuant engine and cache integration.

Runs on CPU — no GPU required. Tests quantization/dequantization roundtrip,
codebook properties, cache lifecycle, and HF client tool parsing.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

import pytest

torch = pytest.importorskip("torch", reason="torch required for TurboQuant tests")
np = pytest.importorskip("numpy", reason="numpy required for TurboQuant tests")

from turbo_quant import (
    TurboQuantConfig,
    TurboQuantEngine,
    compute_codebook,
    generate_rotation_matrix,
    generate_qjl_matrix,
    get_codebook,
)
from turbo_quant_cache import TurboQuantCache, create_turbo_quant_cache


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> TurboQuantConfig:
    return TurboQuantConfig(key_bits=3, value_bits=3, head_dim=64, seed=42)


@pytest.fixture
def engine(config) -> TurboQuantEngine:
    return TurboQuantEngine(config, device=torch.device("cpu"))


@pytest.fixture
def mixed_config() -> TurboQuantConfig:
    return TurboQuantConfig(
        key_bits=2, value_bits=2, head_dim=64, seed=42,
        outlier_channels=16, outlier_bits_extra=1,
    )


@pytest.fixture
def mixed_engine(mixed_config) -> TurboQuantEngine:
    return TurboQuantEngine(mixed_config, device=torch.device("cpu"))


# ─────────────────────────────────────────────────────────────────────
# Codebook tests
# ─────────────────────────────────────────────────────────────────────

class TestCodebook:
    def test_codebook_shape(self) -> None:
        cb = compute_codebook(d=64, b=3)
        assert cb.shape == (8,)

    def test_codebook_sorted(self) -> None:
        cb = compute_codebook(d=64, b=2)
        assert np.all(cb[:-1] <= cb[1:])

    def test_codebook_in_range(self) -> None:
        cb = compute_codebook(d=128, b=3)
        assert np.all(cb >= -1.0)
        assert np.all(cb <= 1.0)

    def test_codebook_cached(self) -> None:
        t1 = get_codebook(64, 3)
        t2 = get_codebook(64, 3)
        assert torch.equal(t1, t2)

    def test_different_dims_different_codebooks(self) -> None:
        c1 = compute_codebook(d=64, b=2)
        c2 = compute_codebook(d=128, b=2)
        assert not np.allclose(c1, c2)


# ─────────────────────────────────────────────────────────────────────
# Rotation matrix tests
# ─────────────────────────────────────────────────────────────────────

class TestRotation:
    def test_orthogonal(self) -> None:
        R = generate_rotation_matrix(64, seed=42)
        identity = R @ R.T
        assert torch.allclose(identity, torch.eye(64), atol=1e-5)

    def test_deterministic(self) -> None:
        R1 = generate_rotation_matrix(64, seed=42)
        R2 = generate_rotation_matrix(64, seed=42)
        assert torch.equal(R1, R2)

    def test_different_seed_different_matrix(self) -> None:
        R1 = generate_rotation_matrix(64, seed=42)
        R2 = generate_rotation_matrix(64, seed=99)
        assert not torch.equal(R1, R2)


# ─────────────────────────────────────────────────────────────────────
# QJL matrix tests
# ─────────────────────────────────────────────────────────────────────

class TestQJL:
    def test_shape(self) -> None:
        S = generate_qjl_matrix(64)
        assert S.shape == (64, 64)

    def test_deterministic(self) -> None:
        S1 = generate_qjl_matrix(64, seed=43)
        S2 = generate_qjl_matrix(64, seed=43)
        assert torch.equal(S1, S2)


# ─────────────────────────────────────────────────────────────────────
# MSE quantization roundtrip
# ─────────────────────────────────────────────────────────────────────

class TestMSEQuantization:
    def test_roundtrip_shape(self, engine) -> None:
        x = torch.randn(2, 4, 8, 64)  # (B, H, S, D)
        indices, norms = engine.quantize_mse(x)
        assert indices.shape == x.shape
        assert norms.shape == x.shape[:-1]

    def test_roundtrip_low_error(self, engine) -> None:
        x = torch.randn(1, 4, 16, 64)
        indices, norms = engine.quantize_mse(x)
        x_recon = engine.dequantize_mse(indices, norms)
        # Cosine similarity should be high (> 0.8 for 3-bit)
        cos = torch.nn.functional.cosine_similarity(
            x.reshape(-1, 64), x_recon.reshape(-1, 64), dim=1
        )
        assert cos.mean().item() > 0.8

    def test_convenience_api(self, engine) -> None:
        x = torch.randn(1, 2, 4, 64)
        q = engine.quantize(x, mode="mse")
        x_recon = engine.dequantize(q, mode="mse")
        assert x_recon.shape == x.shape


# ─────────────────────────────────────────────────────────────────────
# Prod quantization roundtrip
# ─────────────────────────────────────────────────────────────────────

class TestProdQuantization:
    def test_roundtrip_shape(self, engine) -> None:
        x = torch.randn(2, 4, 8, 64)
        mse_idx, qjl_signs, res_norms, norms = engine.quantize_prod(x)
        assert mse_idx.shape == x.shape
        assert qjl_signs.shape == x.shape
        assert res_norms.shape == x.shape[:-1]
        assert norms.shape == x.shape[:-1]

    def test_inner_product_preserved(self, engine) -> None:
        """Q_prod should preserve inner products better than Q_mse."""
        q = torch.randn(1, 1, 1, 64)
        k = torch.randn(1, 1, 16, 64)

        # True inner products
        true_ip = (q @ k.transpose(-2, -1)).squeeze()

        # Quantize keys with Q_prod and dequantize
        q_data = engine.quantize(k, mode="prod")
        k_recon = engine.dequantize(q_data, mode="prod")
        approx_ip = (q @ k_recon.transpose(-2, -1)).squeeze()

        # Correlation should be high
        corr = torch.corrcoef(torch.stack([true_ip, approx_ip]))[0, 1]
        assert corr.item() > 0.85

    def test_qjl_signs_binary(self, engine) -> None:
        x = torch.randn(1, 2, 4, 64)
        _, qjl_signs, _, _ = engine.quantize_prod(x)
        unique = torch.unique(qjl_signs)
        assert all(v in (-1, 1) for v in unique.tolist())


# ─────────────────────────────────────────────────────────────────────
# Mixed precision tests
# ─────────────────────────────────────────────────────────────────────

class TestMixedPrecision:
    def test_mse_roundtrip(self, mixed_engine) -> None:
        x = torch.randn(1, 2, 8, 64)
        indices, norms = mixed_engine.quantize_mse(x)
        x_recon = mixed_engine.dequantize_mse(indices, norms)
        assert x_recon.shape == x.shape

    def test_prod_roundtrip(self, mixed_engine) -> None:
        x = torch.randn(1, 2, 8, 64)
        q = mixed_engine.quantize(x, mode="prod")
        x_recon = mixed_engine.dequantize(q, mode="prod")
        assert x_recon.shape == x.shape

    def test_outlier_mask_created(self, mixed_engine) -> None:
        x = torch.randn(1, 2, 8, 64)
        mixed_engine.quantize_mse(x)
        assert mixed_engine._outlier_mask is not None
        assert mixed_engine._outlier_mask.sum().item() == 16


# ─────────────────────────────────────────────────────────────────────
# Cache tests
# ─────────────────────────────────────────────────────────────────────

class TestTurboQuantCache:
    def test_empty_cache(self, config) -> None:
        cache = TurboQuantCache(config)
        assert len(cache) == 0
        assert not bool(cache)
        assert cache.seen_tokens == 0

    def test_update_and_retrieve(self, config) -> None:
        cache = TurboQuantCache(config)
        k = torch.randn(1, 4, 8, 64)
        v = torch.randn(1, 4, 8, 64)

        k_out, v_out = cache.update(k, v, layer_idx=0)
        assert k_out.shape == k.shape
        assert v_out.shape == v.shape
        assert len(cache) == 1
        assert cache.seen_tokens == 8

    def test_incremental_append(self, config) -> None:
        cache = TurboQuantCache(config)

        # First chunk
        k1 = torch.randn(1, 4, 8, 64)
        v1 = torch.randn(1, 4, 8, 64)
        cache.update(k1, v1, layer_idx=0)

        # Second chunk (autoregressive)
        k2 = torch.randn(1, 4, 1, 64)
        v2 = torch.randn(1, 4, 1, 64)
        k_out, v_out = cache.update(k2, v2, layer_idx=0)

        assert k_out.shape == (1, 4, 9, 64)
        assert cache.seen_tokens == 9

    def test_multi_layer(self, config) -> None:
        cache = TurboQuantCache(config)

        for layer in range(4):
            k = torch.randn(1, 4, 8, 64)
            v = torch.randn(1, 4, 8, 64)
            cache.update(k, v, layer_idx=layer)

        assert len(cache) == 4

    def test_reset(self, config) -> None:
        cache = TurboQuantCache(config)
        k = torch.randn(1, 4, 8, 64)
        v = torch.randn(1, 4, 8, 64)
        cache.update(k, v, layer_idx=0)
        cache.reset()

        assert len(cache) == 0
        assert cache.seen_tokens == 0

    def test_memory_stats(self, config) -> None:
        cache = TurboQuantCache(config)
        k = torch.randn(1, 4, 32, 64)
        v = torch.randn(1, 4, 32, 64)
        cache.update(k, v, layer_idx=0)

        stats = cache.get_memory_stats()
        assert stats["num_layers"] == 1
        assert stats["seq_length"] == 32
        assert stats["compression_ratio"] > 1.0

    def test_factory_function(self) -> None:
        cache = create_turbo_quant_cache(head_dim=64, key_bits=3, value_bits=3)
        assert isinstance(cache, TurboQuantCache)

    def test_getitem(self, config) -> None:
        cache = TurboQuantCache(config)
        k = torch.randn(1, 4, 8, 64)
        v = torch.randn(1, 4, 8, 64)
        cache.update(k, v, layer_idx=0)

        k_out, v_out = cache[0]
        assert k_out.shape == (1, 4, 8, 64)
        assert v_out.shape == (1, 4, 8, 64)

    def test_iter(self, config) -> None:
        cache = TurboQuantCache(config)
        for layer in range(3):
            k = torch.randn(1, 4, 4, 64)
            v = torch.randn(1, 4, 4, 64)
            cache.update(k, v, layer_idx=layer)

        layers = list(cache)
        assert len(layers) == 3

    def test_legacy_roundtrip(self, config) -> None:
        cache = TurboQuantCache(config)
        k = torch.randn(1, 4, 8, 64)
        v = torch.randn(1, 4, 8, 64)
        cache.update(k, v, layer_idx=0)

        legacy = cache.to_legacy_cache()
        assert len(legacy) == 1
        assert legacy[0][0].shape == (1, 4, 8, 64)

        cache2 = TurboQuantCache.from_legacy_cache(legacy, config)
        assert len(cache2) == 1


# ─────────────────────────────────────────────────────────────────────
# Memory estimation tests
# ─────────────────────────────────────────────────────────────────────

class TestMemoryEstimation:
    def test_compression_ratio_positive(self, engine) -> None:
        stats = engine.estimate_memory_bytes(seq_len=1024, num_heads=4)
        assert stats["compression_ratio"] > 1.0

    def test_higher_bits_less_compression(self) -> None:
        cfg_lo = TurboQuantConfig(key_bits=2, value_bits=2, head_dim=64)
        cfg_hi = TurboQuantConfig(key_bits=4, value_bits=4, head_dim=64)
        e_lo = TurboQuantEngine(cfg_lo)
        e_hi = TurboQuantEngine(cfg_hi)

        r_lo = e_lo.estimate_memory_bytes(1024, 4)["compression_ratio"]
        r_hi = e_hi.estimate_memory_bytes(1024, 4)["compression_ratio"]
        assert r_lo > r_hi


# ─────────────────────────────────────────────────────────────────────
# HF tool parsing tests
# ─────────────────────────────────────────────────────────────────────

class TestHFToolParsing:
    """Test _parse_tool_calls from hf_llm_client."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from hf_llm_client import _parse_tool_calls
        self.parse = _parse_tool_calls

    def test_json_tool_call(self) -> None:
        text = '<tool_call>{"name": "attack", "arguments": {"unit_type": "land"}}</tool_call>'
        result = self.parse(text)
        assert len(result) == 1
        assert result[0]["name"] == "attack"
        assert result[0]["args"]["unit_type"] == "land"

    def test_multiple_json_tool_calls(self) -> None:
        text = (
            '<tool_call>{"name": "get_enemy_army", "arguments": {}}</tool_call>\n'
            '<tool_call>{"name": "attack", "arguments": {"unit_type": "air"}}</tool_call>'
        )
        result = self.parse(text)
        assert len(result) == 2

    def test_xml_fallback(self) -> None:
        text = '<function=scout><parameter=direction>north</parameter></function>'
        result = self.parse(text)
        assert len(result) == 1
        assert result[0]["name"] == "scout"
        assert result[0]["args"]["direction"] == "north"

    def test_plain_text_noop(self) -> None:
        text = "I think we should wait and see what happens."
        result = self.parse(text)
        assert len(result) == 1
        assert result[0]["name"] == "noop"

    def test_bare_json_object(self) -> None:
        text = '{"name": "defend", "arguments": {"radius": 80}}'
        result = self.parse(text)
        assert len(result) == 1
        assert result[0]["name"] == "defend"

    def test_json_with_string_args(self) -> None:
        text = '<tool_call>{"name": "chat", "arguments": "{\\"message\\": \\"Hello\\"}"}</tool_call>'
        result = self.parse(text)
        assert len(result) == 1
        assert result[0]["name"] == "chat"
