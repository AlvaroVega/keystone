# Copyright 2013 Metacloud, Inc.
# Copyright 2012 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Workflow Logic the Assignment service."""

import copy
import functools
import uuid

import six
from six.moves import urllib

from keystone.assignment import schema
from keystone.common import controller
from keystone.common import dependency
from keystone.common import validation
from keystone import config
from keystone import exception
from keystone.i18n import _
from keystone.models import token_model
from keystone.openstack.common import log


CONF = config.CONF
LOG = log.getLogger(__name__)


@dependency.requires('assignment_api', 'identity_api', 'token_provider_api')
class Tenant(controller.V2Controller):

    @controller.v2_deprecated
    def get_all_projects(self, context, **kw):
        """Gets a list of all tenants for an admin user."""
        if 'name' in context['query_string']:
            return self.get_project_by_name(
                context, context['query_string'].get('name'))

        self.assert_admin(context)
        tenant_refs = self.assignment_api.list_projects_in_domain(
            CONF.identity.default_domain_id)
        for tenant_ref in tenant_refs:
            tenant_ref = self.filter_domain_id(tenant_ref)
        params = {
            'limit': context['query_string'].get('limit'),
            'marker': context['query_string'].get('marker'),
        }
        return self._format_project_list(tenant_refs, **params)

    @controller.v2_deprecated
    def get_projects_for_token(self, context, **kw):
        """Get valid tenants for token based on token used to authenticate.

        Pulls the token from the context, validates it and gets the valid
        tenants for the user in the token.

        Doesn't care about token scopedness.

        """
        try:
            token_data = self.token_provider_api.validate_token(
                context['token_id'])
            token_ref = token_model.KeystoneToken(token_id=context['token_id'],
                                                  token_data=token_data)
        except exception.NotFound as e:
            LOG.warning(_('Authentication failed: %s'), e)
            raise exception.Unauthorized(e)

        tenant_refs = (
            self.assignment_api.list_projects_for_user(token_ref.user_id))
        tenant_refs = [self.filter_domain_id(ref) for ref in tenant_refs
                       if ref['domain_id'] == CONF.identity.default_domain_id]
        params = {
            'limit': context['query_string'].get('limit'),
            'marker': context['query_string'].get('marker'),
        }
        return self._format_project_list(tenant_refs, **params)

    @controller.v2_deprecated
    def get_project(self, context, tenant_id):
        # TODO(termie): this stuff should probably be moved to middleware
        self.assert_admin(context)
        ref = self.assignment_api.get_project(tenant_id)
        return {'tenant': self.filter_domain_id(ref)}

    @controller.v2_deprecated
    def get_project_by_name(self, context, tenant_name):
        self.assert_admin(context)
        ref = self.assignment_api.get_project_by_name(
            tenant_name, CONF.identity.default_domain_id)
        return {'tenant': self.filter_domain_id(ref)}

    # CRUD Extension
    @controller.v2_deprecated
    def create_project(self, context, tenant):
        tenant_ref = self._normalize_dict(tenant)

        if 'name' not in tenant_ref or not tenant_ref['name']:
            msg = _('Name field is required and cannot be empty')
            raise exception.ValidationError(message=msg)

        self.assert_admin(context)
        tenant_ref['id'] = tenant_ref.get('id', uuid.uuid4().hex)
        tenant = self.assignment_api.create_project(
            tenant_ref['id'],
            self._normalize_domain_id(context, tenant_ref))
        return {'tenant': self.filter_domain_id(tenant)}

    @controller.v2_deprecated
    def update_project(self, context, tenant_id, tenant):
        self.assert_admin(context)
        # Remove domain_id if specified - a v2 api caller should not
        # be specifying that
        clean_tenant = tenant.copy()
        clean_tenant.pop('domain_id', None)

        tenant_ref = self.assignment_api.update_project(
            tenant_id, clean_tenant)
        return {'tenant': tenant_ref}

    @controller.v2_deprecated
    def delete_project(self, context, tenant_id):
        self.assert_admin(context)
        self.assignment_api.delete_project(tenant_id)

    @controller.v2_deprecated
    def get_project_users(self, context, tenant_id, **kw):
        self.assert_admin(context)
        user_refs = []
        user_ids = self.assignment_api.list_user_ids_for_project(tenant_id)
        for user_id in user_ids:
            try:
                user_ref = self.identity_api.get_user(user_id)
            except exception.UserNotFound:
                # Log that user is missing and continue on.
                message = ("User %(user_id)s in project %(project_id)s "
                           "doesn't exist.")
                LOG.debug(message,
                          {'user_id': user_id, 'project_id': tenant_id})
            else:
                user_refs.append(self.v3_to_v2_user(user_ref))
        return {'users': user_refs}

    def _format_project_list(self, tenant_refs, **kwargs):
        marker = kwargs.get('marker')
        first_index = 0
        if marker is not None:
            for (marker_index, tenant) in enumerate(tenant_refs):
                if tenant['id'] == marker:
                    # we start pagination after the marker
                    first_index = marker_index + 1
                    break
            else:
                msg = _('Marker could not be found')
                raise exception.ValidationError(message=msg)

        limit = kwargs.get('limit')
        last_index = None
        if limit is not None:
            try:
                limit = int(limit)
                if limit < 0:
                    raise AssertionError()
            except (ValueError, AssertionError):
                msg = _('Invalid limit value')
                raise exception.ValidationError(message=msg)
            last_index = first_index + limit

        tenant_refs = tenant_refs[first_index:last_index]

        for x in tenant_refs:
            if 'enabled' not in x:
                x['enabled'] = True
        o = {'tenants': tenant_refs,
             'tenants_links': []}
        return o


@dependency.requires('assignment_api')
class Role(controller.V2Controller):

    # COMPAT(essex-3)
    @controller.v2_deprecated
    def get_user_roles(self, context, user_id, tenant_id=None):
        """Get the roles for a user and tenant pair.

        Since we're trying to ignore the idea of user-only roles we're
        not implementing them in hopes that the idea will die off.

        """
        self.assert_admin(context)
        if tenant_id is None:
            raise exception.NotImplemented(message='User roles not supported: '
                                                   'tenant ID required')

        roles = self.assignment_api.get_roles_for_user_and_project(
            user_id, tenant_id)
        return {'roles': [self.assignment_api.get_role(x)
                          for x in roles]}

    # CRUD extension
    @controller.v2_deprecated
    def get_role(self, context, role_id):
        self.assert_admin(context)
        return {'role': self.assignment_api.get_role(role_id)}

    @controller.v2_deprecated
    def create_role(self, context, role):
        role = self._normalize_dict(role)
        self.assert_admin(context)

        if 'name' not in role or not role['name']:
            msg = _('Name field is required and cannot be empty')
            raise exception.ValidationError(message=msg)

        role_id = uuid.uuid4().hex
        role['id'] = role_id
        role_ref = self.assignment_api.create_role(role_id, role)
        return {'role': role_ref}

    @controller.v2_deprecated
    def delete_role(self, context, role_id):
        self.assert_admin(context)
        self.assignment_api.delete_role(role_id)

    @controller.v2_deprecated
    def get_roles(self, context):
        self.assert_admin(context)
        return {'roles': self.assignment_api.list_roles()}

    @controller.v2_deprecated
    def add_role_to_user(self, context, user_id, role_id, tenant_id=None):
        """Add a role to a user and tenant pair.

        Since we're trying to ignore the idea of user-only roles we're
        not implementing them in hopes that the idea will die off.

        """
        self.assert_admin(context)
        if tenant_id is None:
            raise exception.NotImplemented(message='User roles not supported: '
                                                   'tenant_id required')

        self.assignment_api.add_role_to_user_and_project(
            user_id, tenant_id, role_id)

        role_ref = self.assignment_api.get_role(role_id)
        return {'role': role_ref}

    @controller.v2_deprecated
    def remove_role_from_user(self, context, user_id, role_id, tenant_id=None):
        """Remove a role from a user and tenant pair.

        Since we're trying to ignore the idea of user-only roles we're
        not implementing them in hopes that the idea will die off.

        """
        self.assert_admin(context)
        if tenant_id is None:
            raise exception.NotImplemented(message='User roles not supported: '
                                                   'tenant_id required')

        # This still has the weird legacy semantics that adding a role to
        # a user also adds them to a tenant, so we must follow up on that
        self.assignment_api.remove_role_from_user_and_project(
            user_id, tenant_id, role_id)

    # COMPAT(diablo): CRUD extension
    @controller.v2_deprecated
    def get_role_refs(self, context, user_id):
        """Ultimate hack to get around having to make role_refs first-class.

        This will basically iterate over the various roles the user has in
        all tenants the user is a member of and create fake role_refs where
        the id encodes the user-tenant-role information so we can look
        up the appropriate data when we need to delete them.

        """
        self.assert_admin(context)
        tenants = self.assignment_api.list_projects_for_user(user_id)
        o = []
        for tenant in tenants:
            # As a v2 call, we should limit the response to those projects in
            # the default domain.
            if tenant['domain_id'] != CONF.identity.default_domain_id:
                continue
            role_ids = self.assignment_api.get_roles_for_user_and_project(
                user_id, tenant['id'])
            for role_id in role_ids:
                ref = {'roleId': role_id,
                       'tenantId': tenant['id'],
                       'userId': user_id}
                ref['id'] = urllib.parse.urlencode(ref)
                o.append(ref)
        return {'roles': o}

    # COMPAT(diablo): CRUD extension
    @controller.v2_deprecated
    def create_role_ref(self, context, user_id, role):
        """This is actually used for adding a user to a tenant.

        In the legacy data model adding a user to a tenant required setting
        a role.

        """
        self.assert_admin(context)
        # TODO(termie): for now we're ignoring the actual role
        tenant_id = role.get('tenantId')
        role_id = role.get('roleId')
        self.assignment_api.add_role_to_user_and_project(
            user_id, tenant_id, role_id)

        role_ref = self.assignment_api.get_role(role_id)
        return {'role': role_ref}

    # COMPAT(diablo): CRUD extension
    @controller.v2_deprecated
    def delete_role_ref(self, context, user_id, role_ref_id):
        """This is actually used for deleting a user from a tenant.

        In the legacy data model removing a user from a tenant required
        deleting a role.

        To emulate this, we encode the tenant and role in the role_ref_id,
        and if this happens to be the last role for the user-tenant pair,
        we remove the user from the tenant.

        """
        self.assert_admin(context)
        # TODO(termie): for now we're ignoring the actual role
        role_ref_ref = urllib.parse.parse_qs(role_ref_id)
        tenant_id = role_ref_ref.get('tenantId')[0]
        role_id = role_ref_ref.get('roleId')[0]
        self.assignment_api.remove_role_from_user_and_project(
            user_id, tenant_id, role_id)


@dependency.requires('assignment_api')
class DomainV3(controller.V3Controller):
    collection_name = 'domains'
    member_name = 'domain'

    def __init__(self):
        super(DomainV3, self).__init__()
        self.get_member_from_driver = self.assignment_api.get_domain

    @controller.protected()
    @validation.validated(schema.domain_create, 'domain')
    def create_domain(self, context, domain):
        ref = self._assign_unique_id(self._normalize_dict(domain))
        ref = self.assignment_api.create_domain(ref['id'], ref)
        return DomainV3.wrap_member(context, ref)

    @controller.filterprotected('enabled', 'name')
    def list_domains(self, context, filters):
        hints = DomainV3.build_driver_hints(context, filters)
        refs = self.assignment_api.list_domains(hints=hints)
        return DomainV3.wrap_collection(context, refs, hints=hints)

    @controller.protected()
    def get_domain(self, context, domain_id):
        ref = self.assignment_api.get_domain(domain_id)
        return DomainV3.wrap_member(context, ref)

    @controller.protected()
    @validation.validated(schema.domain_update, 'domain')
    def update_domain(self, context, domain_id, domain):
        self._require_matching_id(domain_id, domain)
        ref = self.assignment_api.update_domain(domain_id, domain)
        return DomainV3.wrap_member(context, ref)

    @controller.protected()
    def delete_domain(self, context, domain_id):
        return self.assignment_api.delete_domain(domain_id)


@dependency.requires('assignment_api')
class ProjectV3(controller.V3Controller):
    collection_name = 'projects'
    member_name = 'project'

    def __init__(self):
        super(ProjectV3, self).__init__()
        self.get_member_from_driver = self.assignment_api.get_project

    @controller.protected()
    @validation.validated(schema.project_create, 'project')
    def create_project(self, context, project):
        ref = self._assign_unique_id(self._normalize_dict(project))
        ref = self._normalize_domain_id(context, ref)
        ref = self.assignment_api.create_project(ref['id'], ref)
        return ProjectV3.wrap_member(context, ref)

    @controller.filterprotected('domain_id', 'enabled', 'name')
    def list_projects(self, context, filters):
        hints = ProjectV3.build_driver_hints(context, filters)
        refs = self.assignment_api.list_projects(hints=hints)
        return ProjectV3.wrap_collection(context, refs, hints=hints)

    @controller.filterprotected('enabled', 'name')
    def list_user_projects(self, context, filters, user_id):
        hints = ProjectV3.build_driver_hints(context, filters)
        refs = self.assignment_api.list_projects_for_user(user_id,
                                                          hints=hints)
        return ProjectV3.wrap_collection(context, refs, hints=hints)

    @controller.protected()
    def get_project(self, context, project_id):
        ref = self.assignment_api.get_project(project_id)
        return ProjectV3.wrap_member(context, ref)

    @controller.protected()
    @validation.validated(schema.project_update, 'project')
    def update_project(self, context, project_id, project):
        self._require_matching_id(project_id, project)
        self._require_matching_domain_id(
            project_id, project, self.assignment_api.get_project)
        ref = self.assignment_api.update_project(project_id, project)
        return ProjectV3.wrap_member(context, ref)

    @controller.protected()
    def delete_project(self, context, project_id):
        return self.assignment_api.delete_project(project_id)


@dependency.requires('assignment_api', 'identity_api')
class RoleV3(controller.V3Controller):
    collection_name = 'roles'
    member_name = 'role'

    def __init__(self):
        super(RoleV3, self).__init__()
        self.get_member_from_driver = self.assignment_api.get_role

    @controller.protected()
    @validation.validated(schema.role_create, 'role')
    def create_role(self, context, role):
        ref = self._assign_unique_id(self._normalize_dict(role))
        ref = self.assignment_api.create_role(ref['id'], ref)
        return RoleV3.wrap_member(context, ref)

    @controller.filterprotected('name')
    def list_roles(self, context, filters):
        hints = RoleV3.build_driver_hints(context, filters)
        refs = self.assignment_api.list_roles(
            hints=hints)
        return RoleV3.wrap_collection(context, refs, hints=hints)

    @controller.protected()
    def get_role(self, context, role_id):
        ref = self.assignment_api.get_role(role_id)
        return RoleV3.wrap_member(context, ref)

    @controller.protected()
    @validation.validated(schema.role_update, 'role')
    def update_role(self, context, role_id, role):
        self._require_matching_id(role_id, role)

        ref = self.assignment_api.update_role(role_id, role)
        return RoleV3.wrap_member(context, ref)

    @controller.protected()
    def delete_role(self, context, role_id):
        self.assignment_api.delete_role(role_id)

    def _require_domain_xor_project(self, domain_id, project_id):
        if (domain_id and project_id) or (not domain_id and not project_id):
            msg = _('Specify a domain or project, not both')
            raise exception.ValidationError(msg)

    def _require_user_xor_group(self, user_id, group_id):
        if (user_id and group_id) or (not user_id and not group_id):
            msg = _('Specify a user or group, not both')
            raise exception.ValidationError(msg)

    def _check_if_inherited(self, context):
        return (CONF.os_inherit.enabled and
                context['path'].startswith('/OS-INHERIT') and
                context['path'].endswith('/inherited_to_projects'))

    def _check_grant_protection(self, context, protection, role_id=None,
                                user_id=None, group_id=None,
                                domain_id=None, project_id=None,
                                allow_no_user=False):
        """Check protection for role grant APIs.

        The policy rule might want to inspect attributes of any of the entities
        involved in the grant.  So we get these and pass them to the
        check_protection() handler in the controller.

        """
        ref = {}
        if role_id:
            ref['role'] = self.assignment_api.get_role(role_id)
        if user_id:
            try:
                ref['user'] = self.identity_api.get_user(user_id)
            except exception.UserNotFound:
                if not allow_no_user:
                    raise
        else:
            ref['group'] = self.identity_api.get_group(group_id)

        if domain_id:
            ref['domain'] = self.assignment_api.get_domain(domain_id)
        else:
            ref['project'] = self.assignment_api.get_project(project_id)

        self.check_protection(context, protection, ref)

    @controller.protected(callback=_check_grant_protection)
    def create_grant(self, context, role_id, user_id=None,
                     group_id=None, domain_id=None, project_id=None):
        """Grants a role to a user or group on either a domain or project."""
        self._require_domain_xor_project(domain_id, project_id)
        self._require_user_xor_group(user_id, group_id)

        self.assignment_api.create_grant(
            role_id, user_id, group_id, domain_id, project_id,
            self._check_if_inherited(context), context)

    @controller.protected(callback=_check_grant_protection)
    def list_grants(self, context, user_id=None,
                    group_id=None, domain_id=None, project_id=None):
        """Lists roles granted to user/group on either a domain or project."""
        self._require_domain_xor_project(domain_id, project_id)
        self._require_user_xor_group(user_id, group_id)

        refs = self.assignment_api.list_grants(
            user_id, group_id, domain_id, project_id,
            self._check_if_inherited(context))
        return RoleV3.wrap_collection(context, refs)

    @controller.protected(callback=_check_grant_protection)
    def check_grant(self, context, role_id, user_id=None,
                    group_id=None, domain_id=None, project_id=None):
        """Checks if a role has been granted on either a domain or project."""
        self._require_domain_xor_project(domain_id, project_id)
        self._require_user_xor_group(user_id, group_id)

        self.assignment_api.get_grant(
            role_id, user_id, group_id, domain_id, project_id,
            self._check_if_inherited(context))

    # NOTE(lbragstad): This will allow users to clean up role assignments
    # from the backend in the event the user was removed prior to the role
    # assignment being removed.
    @controller.protected(callback=functools.partial(
        _check_grant_protection, allow_no_user=True))
    def revoke_grant(self, context, role_id, user_id=None,
                     group_id=None, domain_id=None, project_id=None):
        """Revokes a role from user/group on either a domain or project."""
        self._require_domain_xor_project(domain_id, project_id)
        self._require_user_xor_group(user_id, group_id)

        self.assignment_api.delete_grant(
            role_id, user_id, group_id, domain_id, project_id,
            self._check_if_inherited(context), context)


@dependency.requires('assignment_api', 'identity_api')
class RoleAssignmentV3(controller.V3Controller):

    # TODO(henry-nash): The current implementation does not provide a full
    # first class entity for role-assignment. There is no role_assignment_id
    # and only the list_role_assignment call is supported. Further, since it
    # is not a first class entity, the links for the individual entities
    # reference the individual role grant APIs.

    collection_name = 'role_assignments'
    member_name = 'role_assignment'

    @classmethod
    def wrap_member(cls, context, ref):
        # NOTE(henry-nash): Since we are not yet a true collection, we override
        # the wrapper as have already included the links in the entities
        pass

    def _format_entity(self, context, entity):
        """Format an assignment entity for API response.

        The driver layer returns entities as dicts containing the ids of the
        actor (e.g. user or group), target (e.g. domain or project) and role.
        If it is an inherited role, then this is also indicated. Examples:

        For a non-inherited expanded assignment from group membership:
        {'user_id': user_id,
         'project_id': project_id,
         'role_id': role_id,
         'indirect': {'group_id': group_id}}

        or, for a project inherited role:

        {'user_id': user_id,
         'project_id': project_id,
         'role_id': role_id,
         'indirect': {'project_id': parent_id}}

        It is possible to deduce if a role assignment came from group
        membership if it has both 'user_id' in the main body of the dict and
        'group_id' in the 'indirect' subdict, as well as it is possible to
        deduce if it has come from inheritance if it contains both a
        'project_id' in the main body of the dict and 'parent_id' in the
        'indirect' subdict.

        This function maps this into the format to be returned via the API,
        e.g. for the second example above:

        {
            'user': {
                {'id': user_id}
            },
            'scope': {
                'project': {
                    {'id': project_id}
                },
                'OS-INHERIT:inherited_to': 'projects
            },
            'role': {
                {'id': role_id}
            },
            'links': {
                'assignment': '/OS-INHERIT/projects/parent_id/users/user_id/'
                              'roles/role_id/inherited_to_projects'
            }
        }

        """

        formatted_entity = {'links': {}}
        inherited_assignment = entity.get('inherited_to_projects')

        if 'project_id' in entity:
            formatted_entity['scope'] = (
                {'project': {'id': entity['project_id']}})

            if 'domain_id' in entity.get('indirect', {}):
                inherited_assignment = True
                formatted_link = ('/domains/%s' %
                                  entity['indirect']['domain_id'])
            elif 'project_id' in entity.get('indirect', {}):
                inherited_assignment = True
                formatted_link = ('/projects/%s' %
                                  entity['indirect']['project_id'])
            else:
                formatted_link = '/projects/%s' % entity['project_id']
        elif 'domain_id' in entity:
            formatted_entity['scope'] = {'domain': {'id': entity['domain_id']}}
            formatted_link = '/domains/%s' % entity['domain_id']

        if 'user_id' in entity:
            formatted_entity['user'] = {'id': entity['user_id']}

            if 'group_id' in entity.get('indirect', {}):
                membership_url = (
                    self.base_url(context, '/groups/%s/users/%s' % (
                        entity['indirect']['group_id'], entity['user_id'])))
                formatted_entity['links']['membership'] = membership_url
                formatted_link += '/groups/%s' % entity['indirect']['group_id']
            else:
                formatted_link += '/users/%s' % entity['user_id']
        elif 'group_id' in entity:
            formatted_entity['group'] = {'id': entity['group_id']}
            formatted_link += '/groups/%s' % entity['group_id']

        formatted_entity['role'] = {'id': entity['role_id']}
        formatted_link += '/roles/%s' % entity['role_id']

        if inherited_assignment:
            formatted_entity['scope']['OS-INHERIT:inherited_to'] = (
                'projects')
            formatted_link = ('/OS-INHERIT%s/inherited_to_projects' %
                              formatted_link)

        formatted_entity['links']['assignment'] = self.base_url(context,
                                                                formatted_link)

        return formatted_entity

    def _assert_effective_filters(self, inherited, group, domain):
        """Assert that useless filter combinations are avoided.

        In effective mode, the following filter combinations are useless, since
        they would always return an empty list of role assignments:
        - group id, since no group assignment is returned in effective mode;
        - domain id and inherited, since no domain inherited assignment is
        returned in effective mode.

        """
        if group:
            msg = _('Combining effective and group filter will always '
                    'result in an empty list.')
            raise exception.ValidationError(msg)

        if inherited and domain:
            msg = _('Combining effective, domain and inherited filters will '
                    'always result in an empty list.')
            raise exception.ValidationError(msg)

    def _assert_domain_nand_project(self, domain_id, project_id):
        if domain_id and project_id:
            msg = _('Specify a domain or project, not both')
            raise exception.ValidationError(msg)

    def _assert_user_nand_group(self, user_id, group_id):
        if user_id and group_id:
            msg = _('Specify a user or group, not both')
            raise exception.ValidationError(msg)

    @controller.filterprotected('group.id', 'role.id',
                                'scope.domain.id', 'scope.project.id',
                                'scope.OS-INHERIT:inherited_to', 'user.id')
    def list_role_assignments(self, context, filters):
        """List role assignments to user and groups on domains and projects.

        Return a list of all existing role assignments in the system, filtered
        by assignments attributes, if provided.

        If effective option is used and OS-INHERIT extension is enabled, the
        following functions will be applied:
        1) For any group role assignment on a target, replace it by a set of
        role assignments containing one for each user of that group on that
        target;
        2) For any inherited role assignment for an actor on a target, replace
        it by a set of role assignments for that actor on every project under
        that target.

        It means that, if effective mode is used, no group or domain inherited
        assignments will be present in the resultant list. Thus, combining
        effective with them is invalid.

        As a role assignment contains only one actor and one target, providing
        both user and group ids or domain and project ids is invalid as well.

        """
        params = context['query_string']
        effective = 'effective' in params and (
            self.query_filter_is_true(params['effective']))

        if 'scope.OS-INHERIT:inherited_to' in params:
            inherited = (
                params['scope.OS-INHERIT:inherited_to'] == 'projects')
        else:
            # None means querying both inherited and direct assignments
            inherited = None

        self._assert_domain_nand_project(params.get('scope.domain.id'),
                                         params.get('scope.project.id'))
        self._assert_user_nand_group(params.get('user.id'),
                                     params.get('group.id'))

        if effective:
            self._assert_effective_filters(inherited=inherited,
                                           group=params.get('group.id'),
                                           domain=params.get(
                                               'scope.domain.id'))

        refs = self.assignment_api.list_role_assignments(
            role_id=params.get('role.id'),
            user_id=params.get('user.id'),
            group_id=params.get('group.id'),
            domain_id=params.get('scope.domain.id'),
            project_id=params.get('scope.project.id'),
            inherited=inherited, effective=effective)

        formatted_refs = [self._format_entity(context, ref) for ref in refs]

        return self.wrap_collection(context, formatted_refs)

    @controller.protected()
    def get_role_assignment(self, context):
        raise exception.NotImplemented()

    @controller.protected()
    def update_role_assignment(self, context):
        raise exception.NotImplemented()

    @controller.protected()
    def delete_role_assignment(self, context):
        raise exception.NotImplemented()
