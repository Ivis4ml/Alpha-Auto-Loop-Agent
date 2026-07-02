"""infra.seeds 的行为测试。"""

from __future__ import annotations

from alphaloop.infra.seeds import derive_rng, derive_seed


def test_derive_seed_is_deterministic() -> None:
    assert derive_seed(42, "generator") == derive_seed(42, "generator")


def test_derive_seed_separates_components() -> None:
    assert derive_seed(42, "generator") != derive_seed(42, "reviewer")


def test_derive_seed_separates_roots() -> None:
    assert derive_seed(42, "generator") != derive_seed(43, "generator")


def test_derive_rng_reproduces_sequence() -> None:
    first = derive_rng(7, "sampler").random(4)
    second = derive_rng(7, "sampler").random(4)
    assert (first == second).all()
