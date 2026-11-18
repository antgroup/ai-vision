from rest_framework.throttling import ScopedRateThrottle


class SpaceKeyRateThrottle(ScopedRateThrottle):
    """Per-SpaceKey rate throttle using SpaceKey.id as cache key identifier."""

    def get_cache_key(self, request, view):
        auth = getattr(request, "auth", None)
        if auth is not None and hasattr(auth, "id") and hasattr(auth, "key_prefix"):
            return self.cache_format % {
                "scope": self.scope,
                "ident": f"sk:{auth.id}",
            }
        return super().get_cache_key(request, view)


class UnifiedExternalRateThrottle(ScopedRateThrottle):
    """Rate throttle for machine-authenticated external APIs."""

    def get_cache_key(self, request, view):
        auth_type = getattr(request, "auth_type", None)

        if auth_type == "credential":
            public_key = getattr(getattr(request, "auth", None), "public_key", "")
            if not public_key:
                return super().get_cache_key(request, view)
            ident = f"credential:{public_key}"

        elif auth_type == "space_key":
            auth = getattr(request, "auth", None)
            if auth is not None and hasattr(auth, "id"):
                ident = f"sk:{auth.id}"
            else:
                return super().get_cache_key(request, view)

        elif auth_type == "user":
            user = getattr(request, "user", None)
            pk = getattr(user, "pk", None)
            if pk is not None:
                ident = f"user:{pk}"
            else:
                return super().get_cache_key(request, view)

        else:
            return super().get_cache_key(request, view)

        return self.cache_format % {"scope": self.scope, "ident": ident}
