"""Place every received book level on an independent, ordered depth axis."""

from ..mcp_depth import DepthLevel


def depth_rows(levels, top: float, bottom: float, *, center_gap: float = 0.0):
    """Return one floating-point row per valid level, without pixel binning."""
    if bottom <= top:
        return []
    valid = [level for level in levels
             if level.side in {"bid", "ask"} and level.price > 0 and level.volume > 0]
    asks = sorted((level for level in valid if level.side == "ask"), key=lambda level: level.price)
    bids = sorted((level for level in valid if level.side == "bid"), key=lambda level: level.price,
                  reverse=True)
    center = (top + bottom) / 2
    gap = min(max(0.0, center_gap), (bottom - top) / 2)
    rows: list[tuple[float, DepthLevel]] = []
    for side, upper in ((asks, True), (bids, False)):
        near = center - gap if upper else center + gap
        far = top if upper else bottom
        for index, level in enumerate(side):
            y = (near + far) / 2 if len(side) == 1 else near + (far - near) * index / (len(side) - 1)
            rows.append((y, level))
    return rows
