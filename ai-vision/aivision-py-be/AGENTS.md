# Django Backend (aivision-py-be)

**Stack:** Django + Django REST Framework
**Package Manager:** uv

## OVERVIEW
REST API backend for AI Vision observability platform.

## STRUCTURE
```
aivision-py-be/
├── aivision_backend/   # Django project config (settings, urls, wsgi)
├── apps/core/          # Core app with model/view packages and serializers.py
├── scripts/            # Mock data generation
└── manage.py
```

## WHERE TO LOOK
| Task | File |
|------|------|
| Models | `apps/core/models/` |
| API Views | `apps/core/views/` |
| Serializers | `apps/core/serializers.py` |
| URLs | `aivision_backend/urls.py`, `apps/core/urls/` |
| Auth | `apps/core/authentication.py` |
| Core-specific rules | `apps/core/AGENTS.md` |

## CONVENTIONS
- **Table prefix**: `aivision_` for all tables
- **Status encoding**: `SmallIntegerField` (0=Disabled, 1=Normal)
- **Soft delete**: models that support soft deletion use `gmt_deleted`; check the target model and query filters.
- **Response format**: `{code: 0, message: "success", data: {...}}`
- **Foreign Keys**: ALWAYS use `db_constraint=False` on ForeignKey and ManyToMany fields

## DEVELOPMENT AND VERIFICATION

Read the parent [platform rules](../AGENTS.md) and [backend README](README.md)
for the current environment, startup and targeted check commands. Read
[Core rules](apps/core/AGENTS.md) before changing its APIs or models. Current
runtime configuration is in `aivision_backend/settings.py`. The root URL
configuration includes the route modules in `apps/core/urls/`.

Follow the root [contribution guide](../../CONTRIBUTING.md) for PR self-test and evidence requirements. Verify affected API inputs, response contracts, errors, identity and space isolation, and relevant callers. Label mocked dependencies and unverified boundaries; backend-only changes do not need unrelated screenshots or a full-stack run.

## NOTES
- Custom User model (not Django's auth.User)
- Default API authentication uses `LocalSessionAuthentication`, with server-side sessions and HttpOnly cookies. Check the selected view's authentication and CSRF requirements before modifying an endpoint.
- Settings module: `aivision_backend.settings` (not `aivision.settings`)
