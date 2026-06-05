"""Permission classes for the v7 package API.

v7 removes anonymous access entirely. Every `/api/` request must resolve
to an organisation — via an API key (`ApiKeyAuthentication`, which sets
`request.organisation`) or an authenticated session whose user has a
`Membership`. There is no finer read/write split: any valid org
principal has full access to that org's data (role-based restriction is
out of scope).
"""
from __future__ import annotations

from rest_framework.permissions import BasePermission


def resolve_org(request):
    """Return the request's organisation, or None.

    `ApiKeyAuthentication` sets `request.organisation` directly. For
    session requests we fall back to the user's `Membership` and cache
    the result back onto the request.
    """
    org = getattr(request, 'organisation', None)
    if org is None and getattr(request, 'user', None) is not None \
            and request.user.is_authenticated:
        membership = getattr(request.user, 'membership', None)
        if membership is not None:
            org = membership.organisation
            request.organisation = org
    return org


class HasOrganisation(BasePermission):
    message = 'authentication with an organisation context is required'

    def has_permission(self, request, view):
        return resolve_org(request) is not None
