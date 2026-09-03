"""The three authorization questions: role, institution, ownership."""

from __future__ import annotations

import uuid

import pytest

from app.core import authz
from app.models.enums import RoleCode
from app.services.errors import ForbiddenError
from tests.factories import make_user


class TestRequireRole:
    def test_allows_listed_role(self):
        authz.require_admin(make_user(RoleCode.ADMIN))

    def test_rejects_unlisted_role(self):
        with pytest.raises(ForbiddenError):
            authz.require_admin(make_user(RoleCode.EPS_COORDINATOR))

    def test_staff_excludes_patient(self):
        with pytest.raises(ForbiddenError):
            authz.require_staff(make_user(RoleCode.PATIENT))
        for role in (
            RoleCode.ADMIN,
            RoleCode.EPS_COORDINATOR,
            RoleCode.IPS_CLINICAL_OPERATOR,
        ):
            authz.require_staff(make_user(role))


class TestOrgScope:
    def test_admin_and_coordinator_see_whole_network(self):
        other_org = uuid.uuid4()
        authz.ensure_org_scope(make_user(RoleCode.ADMIN), other_org)
        authz.ensure_org_scope(make_user(RoleCode.EPS_COORDINATOR), other_org)

    def test_operator_limited_to_own_ips(self):
        own_org = uuid.uuid4()
        operator = make_user(
            RoleCode.IPS_CLINICAL_OPERATOR, organization_id=own_org
        )
        authz.ensure_org_scope(operator, own_org)
        with pytest.raises(ForbiddenError):
            authz.ensure_org_scope(operator, uuid.uuid4())

    def test_operator_without_org_is_denied(self):
        # A misconfigured account fails closed, never open.
        operator = make_user(RoleCode.IPS_CLINICAL_OPERATOR, organization_id=None)
        with pytest.raises(ForbiddenError):
            authz.ensure_org_scope(operator, uuid.uuid4())


class TestOwnership:
    def test_admin_bypasses_ownership(self):
        authz.ensure_owner_or_admin(make_user(RoleCode.ADMIN), uuid.uuid4())

    def test_author_may_touch_own_record(self):
        operator = make_user(RoleCode.IPS_CLINICAL_OPERATOR)
        authz.ensure_owner_or_admin(operator, operator.id)

    def test_non_author_is_denied(self):
        operator = make_user(RoleCode.IPS_CLINICAL_OPERATOR)
        with pytest.raises(ForbiddenError):
            authz.ensure_owner_or_admin(operator, uuid.uuid4())

    def test_orphan_record_is_admin_only(self):
        operator = make_user(RoleCode.IPS_CLINICAL_OPERATOR)
        with pytest.raises(ForbiddenError):
            authz.ensure_owner_or_admin(operator, None)


class TestOrgFilter:
    def test_operator_gets_own_org_as_filter(self):
        org = uuid.uuid4()
        operator = make_user(RoleCode.IPS_CLINICAL_OPERATOR, organization_id=org)
        assert authz.org_filter_for(operator) == org

    def test_network_roles_get_no_filter(self):
        assert authz.org_filter_for(make_user(RoleCode.ADMIN)) is None
        assert authz.org_filter_for(make_user(RoleCode.EPS_COORDINATOR)) is None
