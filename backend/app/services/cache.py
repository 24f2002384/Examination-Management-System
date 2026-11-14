"""Small Redis JSON cache. Cache errors are intentionally transparent to callers."""
import json
import logging
from functools import wraps

from flask import current_app, jsonify, make_response, request, session
from redis import Redis

log = logging.getLogger(__name__)
_clients = {}


def _client():
    url = current_app.config["REDIS_URL"]
    if url not in _clients:
        _clients[url] = Redis.from_url(url, socket_connect_timeout=.25, socket_timeout=.25,
                                       decode_responses=True)
    return _clients[url]


def cache_get(key):
    if not current_app.config.get("CACHE_ENABLED", True):
        return None
    try:
        value = _client().get("emp-cache:" + key)
        return json.loads(value) if value else None
    except Exception as exc:
        log.warning("Redis cache read unavailable; using database: %s", type(exc).__name__)
        return None


def cache_set(key, value, timeout=None):
    if not current_app.config.get("CACHE_ENABLED", True):
        return False
    try:
        _client().setex("emp-cache:" + key,
                        timeout or current_app.config["CACHE_DEFAULT_TIMEOUT"],
                        json.dumps(value, separators=(",", ":"), default=str))
        return True
    except Exception as exc:
        log.warning("Redis cache write unavailable; response served from database: %s", type(exc).__name__)
        return False


def invalidate_cache(*namespaces):
    if not current_app.config.get("CACHE_ENABLED", True):
        return
    try:
        client = _client()
        patterns = ["emp-cache:" + namespace + ":*" for namespace in (namespaces or ("*",))]
        for pattern in patterns:
            keys = list(client.scan_iter(match=pattern, count=200))
            if keys:
                client.delete(*keys)
    except Exception as exc:
        log.warning("Redis cache invalidation unavailable: %s", type(exc).__name__)


def cached_response(namespace, timeout=None):
    """Cache JSON API responses after outer login/role guards have run."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            identity = session.get("user_id", "anonymous")
            params = json.dumps(sorted(request.args.items()), separators=(",", ":"))
            key = f"{namespace}:u{identity}:" + request.path + ":" + params
            saved = cache_get(key)
            if saved is not None:
                response = make_response(jsonify(saved["json"]), saved["status"])
                response.headers["X-Cache"] = "HIT"
                return response
            response = make_response(fn(*args, **kwargs))
            if response.status_code == 200 and response.is_json:
                cache_set(key, {"status": response.status_code, "json": response.get_json()}, timeout)
            response.headers["X-Cache"] = "MISS"
            return response
        return wrapped
    return decorate
