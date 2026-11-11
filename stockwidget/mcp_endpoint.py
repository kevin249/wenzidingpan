"""公开服务的实时入口迁移；显式独立地址优先。"""
from urllib.parse import urlsplit, urlunsplit

PUBLIC_HOST = 'gupiao.yanglaogu.cn'
REALTIME_PATH = '/api/notifications/mcp/realtime'


def realtime_url(config):
    base = config.mcp_url.strip()
    explicit = config.mcp_realtime_url.strip()
    if explicit and explicit != base:
        return explicit
    parsed = urlsplit(explicit or base)
    if parsed.scheme == 'https' and parsed.hostname == PUBLIC_HOST and parsed.path.rstrip('/') == '/mcp':
        return urlunsplit((parsed.scheme, parsed.netloc, REALTIME_PATH, '', ''))
    return explicit
