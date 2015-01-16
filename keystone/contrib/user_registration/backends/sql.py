# Copyright (C) 2014 Universidad Politecnica de Madrid
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

import uuid

from keystone import exception
from keystone.common import sql
from keystone.contrib import user_registration
from keystone.i18n import _
from oslo.utils import timeutils

class ActivationProfile(sql.ModelBase, sql.ModelDictMixin):
    __tablename__ = 'user_registration_activation_profile'
    attributes = ['id', 'user_id', 'expires_at', 'used']              
    id = sql.Column(sql.String(64), primary_key=True, nullable=False)
    user_id = sql.Column(sql.String(64), nullable=False, index=True)
    project_id = sql.Column(sql.String(64), nullable=False, index=True)
    # TODO(garcianavalon) datetime type or similar?
    expires_at = sql.Column(sql.String(64), nullable=False)
    used = sql.Column(sql.Boolean(), default=False, nullable=False)

class ResetProfile(sql.ModelBase, sql.DictBase):
    __tablename__ = 'user_registration_reset_profile'
    attributes = ['id', 'user_id', 'expires_at', 'used']              
    id = sql.Column(sql.String(64), primary_key=True, nullable=False)
    user_id = sql.Column(sql.String(64), nullable=False, index=True)
    # TODO(garcianavalon) datetime type or similar?
    expires_at = sql.Column(sql.String(64), nullable=False)
    used = sql.Column(sql.Boolean(), default=False, nullable=False)

class Registration(user_registration.Driver):
    """ CRUD driver for the SQL backend """

    def create_reset_profile(self, profile):
        session = sql.get_session()
        with session.begin():
            profile_ref = ResetProfile.from_dict(profile)
            session.add(profile_ref)
        return profile_ref.to_dict()

    def get_reset_profile(self, user_id, reset_token):
        session = sql.get_session()
        with session.begin():
            profile_ref = (session.query(ResetProfile).get(reset_token)
                            .filter_by(user_id=user_id))
        return profile_ref.to_dict()

    def create_activation_profile(self, profile):
        session = sql.get_session()
        with session.begin():
            profile_ref = ActivationProfile.from_dict(profile)
            session.add(profile_ref)
        return profile_ref.to_dict()

    def get_activation_profile(self, user_id, activation_key):
        session = sql.get_session()
        with session.begin():
            profile_ref = session.query(ActivationProfile).get(activation_key)
        return profile_ref.to_dict() if profile_ref.user_id == user_id else None