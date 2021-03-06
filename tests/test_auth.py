import flask
from marshmallow import fields, Schema
import pytest
from sqlalchemy import Column, Integer, sql, String

from flask_resty import (
    Api,
    ApiError,
    AuthenticationBase,
    GenericModelView,
    HasAnyCredentialsAuthorization,
    HasCredentialsAuthorizationBase,
)
from flask_resty.testing import assert_response

# -----------------------------------------------------------------------------


@pytest.yield_fixture
def models(db):
    class Widget(db.Model):
        __tablename__ = 'widgets'

        id = Column(Integer, primary_key=True)
        owner_id = Column(String)
        name = Column(String)

    db.create_all()

    yield {
        'widget': Widget,
    }

    db.drop_all()


@pytest.fixture
def schemas():
    class WidgetSchema(Schema):
        id = fields.Integer(as_string=True)
        owner_id = fields.String()
        name = fields.String()

    return {
        'widget': WidgetSchema(),
    }


@pytest.fixture
def auth():
    class FakeAuthentication(AuthenticationBase):
        def get_request_credentials(self):
            return flask.request.args.get('user_id')

    class UserAuthorization(HasCredentialsAuthorizationBase):
        def filter_query(self, query, view):
            return query.filter(
                (view.model.owner_id == self.get_request_credentials()) |
                (view.model.owner_id == sql.null()),
            )

        def authorize_save_item(self, item):
            return self.authorize_modify_item(item)

        def authorize_update_item(self, item, data):
            return self.authorize_modify_item(item)

        def authorize_delete_item(self, item):
            return self.authorize_modify_item(item)

        def authorize_modify_item(self, item):
            if item.owner_id != self.get_request_credentials():
                raise ApiError(403, {'code': 'invalid_user'})

    return {
        'authentication': FakeAuthentication(),
        'authorization': UserAuthorization(),
    }


@pytest.fixture(autouse=True)
def routes(app, models, schemas, auth):
    class WidgetViewBase(GenericModelView):
        model = models['widget']
        schema = schemas['widget']

        authentication = auth['authentication']
        authorization = auth['authorization']

    class WidgetListView(WidgetViewBase):
        def get(self):
            return self.list()

        def post(self):
            return self.create()

    class WidgetView(WidgetViewBase):
        def get(self, id):
            return self.retrieve(id)

        def patch(self, id):
            return self.update(id, partial=True)

        def delete(self, id):
            return self.destroy(id)

    class WidgetAnyCredentialsView(WidgetViewBase):
        authorization = HasAnyCredentialsAuthorization()

        def get(self, id):
            return self.retrieve(id)

    class WidgetCreateMissingView(WidgetViewBase):
        def create_missing_item(self, id):
            return self.model(id=id, owner_id=flask.request.args['owner_id'])

        def get(self, id):
            return self.retrieve(id, create_missing=True)

        def put(self, id):
            return self.update(id, create_missing=True)

    api = Api(app)
    api.add_resource(
        '/widgets', WidgetListView, WidgetView, id_rule='<int:id>',
    )
    api.add_resource(
        '/widgets_any_credentials/<int:id>', WidgetAnyCredentialsView,
    )
    api.add_resource(
        '/widgets_create_missing/<int:id>', WidgetCreateMissingView,
    )


@pytest.fixture(autouse=True)
def data(db, models):
    db.session.add_all((
        models['widget'](owner_id='foo', name="Foo"),
        models['widget'](owner_id='bar', name="Bar"),
        models['widget'](owner_id=None, name="Public"),
    ))
    db.session.commit()


# -----------------------------------------------------------------------------


def test_list(client):
    response = client.get('/widgets?user_id=foo')
    assert_response(response, 200, [
        {
            'id': '1',
            'owner_id': 'foo',
            'name': "Foo",
        },
        {
            'id': '3',
            'owner_id': None,
            'name': "Public",
        },
    ])


def test_retrieve(client):
    response = client.get('/widgets/1?user_id=foo')
    assert_response(response, 200)


def test_create(client):
    response = client.post('/widgets?user_id=foo', data={
        'owner_id': 'foo',
        'name': "Created",
    })
    assert_response(response, 201)


def test_update(client):
    response = client.patch('/widgets/1?user_id=foo', data={
        'id': '1',
        'owner_id': 'foo',
        'name': "Updated",
    })
    assert_response(response, 204)


def test_delete(client):
    response = client.delete('/widgets/1?user_id=foo')
    assert_response(response, 204)


def test_retrieve_any_credentials(client):
    response = client.get('/widgets_any_credentials/1?user_id=bar')
    assert response.status_code == 200


def test_retrieve_create_missing(client):
    response = client.get('/widgets_create_missing/4?user_id=foo&owner_id=foo')
    assert_response(response, 200, {
        'id': '4',
        'owner_id': 'foo',
        'name': None,
    })


def test_update_update_missing(client):
    response = client.put(
        '/widgets_create_missing/4?user_id=foo&owner_id=foo',
        data={
            'id': '4',
            'name': "Created",
        },
    )
    assert_response(response, 204)


# -----------------------------------------------------------------------------


def test_error_unauthenticated(client):
    response = client.get('/widgets')
    assert_response(response, 401, [{
        'code': 'invalid_credentials.missing',
    }])


def test_error_retrieve_unauthorized(client):
    response = client.get('/widgets/1?user_id=bar')
    assert_response(response, 404)


def test_error_create_unauthorized(client):
    response = client.post('/widgets?user_id=bar', data={
        'owner_id': 'foo',
        'name': "Created",
    })
    assert_response(response, 403, [{
        'code': 'invalid_user',
    }])


def test_error_update_unauthorized(client):
    not_found_response = client.patch('/widgets/1?user_id=bar', data={
        'id': '1',
        'owner_id': 'bar',
        'name': "Updated",
    })
    assert_response(not_found_response, 404)

    forbidden_response = client.patch('/widgets/3?user_id=bar', data={
        'id': '3',
        'owner_id': 'bar',
        'name': "Updated",
    })
    assert_response(forbidden_response, 403, [{
        'code': 'invalid_user',
    }])


def test_error_delete_unauthorized(client):
    not_found_response = client.delete('/widgets/1?user_id=bar')
    assert_response(not_found_response, 404)

    forbidden_response = client.delete('/widgets/3?user_id=bar')
    assert_response(forbidden_response, 403, [{
        'code': 'invalid_user',
    }])


def test_error_any_credentials_unauthenticated(client):
    response = client.get('/widgets_any_credentials/1')
    assert response.status_code == 401


def test_error_retrieve_create_missing_unauthorized(client):
    response = client.get('/widgets_create_missing/4?user_id=bar&owner_id=foo')
    assert_response(response, 404)


def test_error_update_create_missing_unauthorized(client):
    response = client.put(
        '/widgets_create_missing/4?user_id=bar&owner_id=foo',
        data={
            'id': '4',
            'name': "Created",
        },
    )
    assert_response(response, 403, [{
        'code': 'invalid_user',
    }])
