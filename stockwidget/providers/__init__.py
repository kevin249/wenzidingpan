"""数据源注册表。顺序即 WebUI 下拉框顺序，东财排第一并作为默认。"""

from __future__ import annotations

from .base import Provider, Quote
from .eastmoney import EastmoneyProvider
from .mock import MockProvider
from .textquote import SinaProvider, TencentProvider

PROVIDERS: list[Provider] = [
    EastmoneyProvider(),
    TencentProvider(),
    SinaProvider(),
    MockProvider(),
]
_BY_ID = {p.id: p for p in PROVIDERS}
DEFAULT_PROVIDER = EastmoneyProvider.id


def resolve(provider_id: str) -> Provider:
    """未知 id 一律回落到默认数据源，保证界面始终能出数。"""
    return _BY_ID.get(provider_id, _BY_ID[DEFAULT_PROVIDER])


def listing() -> list[dict[str, str]]:
    return [{"id": p.id, "label": p.label, "placeholder": p.placeholder} for p in PROVIDERS]


__all__ = ["PROVIDERS", "DEFAULT_PROVIDER", "Provider", "Quote", "resolve", "listing"]
