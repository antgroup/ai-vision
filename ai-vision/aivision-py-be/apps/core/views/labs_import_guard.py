"""Shared preview and optimistic write protection for local Labs imports."""

from django.db.models import Value

from ..models import Space
from .common import check_space_access_or_error, error_response


IMPORT_CONFLICT_MESSAGE = "The configuration name or version changed. Review the import again."


class ImportWriteGuard:
    def __init__(self, overwrites, names, name_field, version_field):
        if not isinstance(overwrites, list) or len(overwrites) > len(names):
            raise ValueError("overwrites must be a list of submitted names and versions")
        self.approvals = {}
        for item in overwrites:
            if not isinstance(item, dict):
                raise ValueError("Invalid overwrite confirmation")
            name = item.get("name")
            version = item.get("version")
            if (
                not isinstance(name, str)
                or name not in names
                or name in self.approvals
                or not isinstance(version, str)
                or not version
                or len(version) > 64
            ):
                raise ValueError("Invalid overwrite confirmation")
            self.approvals[name] = version
        self.name_field = name_field
        self.version_field = version_field
        self.written = {}

    def check(self, requested_name, config, created):
        """Check a row held under the caller's write transaction."""
        version = (
            None
            if created or config.gmt_deleted is not None
            else getattr(config, self.version_field).isoformat()
        )
        previous = self.written.get(config.pk)
        if previous is not None:
            first_name, first_version = previous
            return {
                "requestedName": requested_name,
                "conflictingRequestedName": first_name,
                "name": getattr(config, self.name_field),
                # Report the committed version, since this batch will roll back.
                "version": first_version,
            }
        if self.approvals.get(requested_name) != version:
            return {
                "requestedName": requested_name,
                "name": getattr(config, self.name_field),
                "version": version,
            }
        self.written[config.pk] = (requested_name, version)
        return None


def conflict_matches(queryset, names, name_field, version_field, limit):
    if (
        not isinstance(names, list)
        or not names
        or len(names) > limit
        or any(
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or len(name) > 128
            for name in names
        )
    ):
        raise ValueError(f"names must contain 1 to {limit} valid configuration names")

    # Keep the submitted spelling with each database match. Each branch uses
    # the name column's own collation, including OceanBase's case folding.
    names = list(dict.fromkeys(names))
    queries = [
        queryset.filter(**{name_field: name})
        .order_by()
        .annotate(submitted_name=Value(name))
        .values_list("submitted_name", name_field, version_field)
        for name in names
    ]
    rows = queries[0]
    if len(queries) > 1:
        rows = rows.union(*queries[1:], all=True)
    matches = {
        submitted: {
            "requestedName": submitted,
            "name": stored,
            "version": version.isoformat(),
        }
        for submitted, stored, version in rows
    }
    return [matches[name] for name in names if name in matches]


def managed_config_space(request, space_id, check_admin):
    try:
        space = Space.objects.get(uuid=space_id, status=1, gmt_deleted__isnull=True)
    except Space.DoesNotExist:
        return None, error_response("Space not found", 3001, 404)
    denied = check_space_access_or_error(request.user, space)
    if denied:
        return None, denied
    return space, check_admin(request.user, space)
