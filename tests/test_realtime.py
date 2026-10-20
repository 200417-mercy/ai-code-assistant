"""Real-time collaboration tests (issue #50).

Exercises Socket.IO room authorization, broadcast isolation, typing indicators,
presence lifecycle, and message broadcasts through the real chat route.
"""

from flask import g

from app.extensions import db, socketio
from app.models import Conversation, ConversationShare, Workspace, WorkspaceMember


def _conversation(user, workspace=None):
    conversation = Conversation(
        user_id=user.id,
        title="Realtime",
        workspace_id=workspace.id if workspace else None,
    )
    db.session.add(conversation)
    db.session.commit()
    return conversation


def _share(conversation, user, *, owner):
    db.session.add(
        ConversationShare(
            conversation_id=conversation.id,
            user_id=user.id,
            shared_by_id=owner.id,
            permission="read_only",
        )
    )
    db.session.commit()


def _login_client(app, email):
    client = app.test_client()
    client.post("/auth/logout")
    client.post(
        "/auth/login",
        data={"email": email, "password": "supersecret123"},
        follow_redirects=True,
    )
    # The test suite keeps one application context for the whole test, so
    # Flask-Login's cached `g._login_user` can belong to a previous test
    # client. Drop it so the next request reloads from its own session cookie.
    g.pop("_login_user", None)
    return client


def _socket(app, email):
    return socketio.test_client(app, flask_test_client=_login_client(app, email))


def _events(sock, name):
    return [event for event in sock.get_received() if event["name"] == name]


def _payload(event):
    args = event.get("args")
    if isinstance(args, dict):
        return args
    if isinstance(args, (list, tuple)) and args:
        return args[0]
    return None


class TestJoinAuthorization:
    def test_unauthenticated_join_is_rejected(self, app, make_user):
        owner = make_user()
        conversation = _conversation(owner)
        sock = socketio.test_client(app)

        sock.emit("join_conversation", {"conversation_id": conversation.id})
        received = sock.get_received()
        assert any(event["name"] == "error" for event in received)
        assert not any(event["name"] == "joined" for event in received)

    def test_owner_can_join(self, app, make_user):
        owner = make_user()
        conversation = _conversation(owner)
        sock = _socket(app, "tester@example.com")

        sock.emit("join_conversation", {"conversation_id": conversation.id})
        joined = _events(sock, "joined")
        assert len(joined) == 1
        assert _payload(joined[0])["role"] == "owner"
        assert _payload(joined[0])["conversation_id"] == conversation.id

    def test_shared_user_can_join_as_read_only(self, app, make_user):
        owner = make_user()
        viewer = make_user("viewer", "viewer@example.com")
        conversation = _conversation(owner)
        _share(conversation, viewer, owner=owner)

        sock = _socket(app, "viewer@example.com")
        sock.emit("join_conversation", {"conversation_id": conversation.id})
        joined = _events(sock, "joined")
        assert _payload(joined[0])["role"] == "shared"

    def test_workspace_member_can_join(self, app, make_user):
        owner = make_user()
        member = make_user("member", "member@example.com")
        workspace = Workspace(user_id=owner.id, name="Team")
        db.session.add(workspace)
        db.session.commit()
        db.session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=member.id,
                role="viewer",
                status="active",
            )
        )
        db.session.commit()
        conversation = _conversation(owner, workspace)

        sock = _socket(app, "member@example.com")
        sock.emit("join_conversation", {"conversation_id": conversation.id})
        joined = _events(sock, "joined")
        assert _payload(joined[0])["role"] == "workspace"

    def test_unrelated_user_cannot_join(self, app, make_user):
        owner = make_user()
        make_user("stranger", "stranger@example.com")
        conversation = _conversation(owner)

        sock = _socket(app, "stranger@example.com")
        sock.emit("join_conversation", {"conversation_id": conversation.id})
        received = sock.get_received()
        assert any(event["name"] == "error" for event in received)
        assert not any(event["name"] == "joined" for event in received)

    def test_invalid_conversation_id_is_rejected(self, app, make_user):
        make_user()
        sock = _socket(app, "tester@example.com")
        sock.emit("join_conversation", {"conversation_id": "not-a-number"})
        assert any(event["name"] == "error" for event in sock.get_received())


class TestRoomIsolation:
    def test_typing_only_reaches_the_joined_room(self, app, make_user):
        owner = make_user()
        other = make_user("other", "other@example.com")
        first = _conversation(owner)
        second = _conversation(other)

        owner_sock = _socket(app, "tester@example.com")
        other_sock = _socket(app, "other@example.com")
        owner_sock.emit("join_conversation", {"conversation_id": first.id})
        other_sock.emit("join_conversation", {"conversation_id": second.id})

        owner_sock.get_received()
        other_sock.get_received()
        owner_sock.emit("typing", {"conversation_id": first.id, "typing": True})

        assert _events(other_sock, "typing") == []
        assert len(_events(owner_sock, "typing")) == 0  # include_self=False

    def test_typing_reaches_another_participant(self, app, make_user):
        owner = make_user()
        viewer = make_user("viewer", "viewer@example.com")
        conversation = _conversation(owner)
        _share(conversation, viewer, owner=owner)

        owner_sock = _socket(app, "tester@example.com")
        viewer_sock = _socket(app, "viewer@example.com")
        owner_sock.emit("join_conversation", {"conversation_id": conversation.id})
        viewer_sock.emit("join_conversation", {"conversation_id": conversation.id})
        owner_sock.get_received()
        viewer_sock.get_received()

        owner_sock.emit("typing", {"conversation_id": conversation.id, "typing": True})
        typing = _events(viewer_sock, "typing")
        assert len(typing) == 1
        payload = _payload(typing[0])
        assert payload["typing"] is True
        assert payload["username"] == owner.username

        owner_sock.emit("typing", {"conversation_id": conversation.id, "typing": False})
        assert _payload(_events(viewer_sock, "typing")[-1])["typing"] is False

    def test_typing_requires_joining_first(self, app, make_user):
        owner = make_user()
        conversation = _conversation(owner)
        sock = _socket(app, "tester@example.com")

        sock.emit("typing", {"conversation_id": conversation.id, "typing": True})
        received = sock.get_received()
        assert any(event["name"] == "error" for event in received)


class TestMessageBroadcast:
    def test_chat_route_broadcasts_to_room_only(self, app, make_user):
        owner = make_user()
        viewer = make_user("viewer", "viewer@example.com")
        conversation = _conversation(owner)
        unrelated = _conversation(viewer)
        _share(conversation, viewer, owner=owner)

        viewer_sock = _socket(app, "viewer@example.com")
        viewer_sock.emit("join_conversation", {"conversation_id": conversation.id})
        viewer_sock.get_received()

        # A second room owned by the viewer must stay silent.
        other_owner = make_user("otherowner", "otherowner@example.com")
        second = _conversation(other_owner)
        viewer_sock.emit("join_conversation", {"conversation_id": second.id})
        viewer_sock.get_received()

        owner_client = _login_client(app, "tester@example.com")
        response = owner_client.post(
            f"/api/conversations/{conversation.id}/messages",
            json={"content": "hello team"},
        )
        assert response.status_code == 201, response.get_json()

        messages = _events(viewer_sock, "message")
        assert len(messages) == 2  # user message + assistant reply
        payloads = [_payload(event) for event in messages]
        assert {payload["message"]["role"] for payload in payloads} == {
            "user",
            "assistant",
        }
        assert all(payload["conversation_id"] == conversation.id for payload in payloads)
        assert unrelated.id != conversation.id

    def test_own_message_is_marked_with_sender_id(self, app, make_user):
        owner = make_user()
        conversation = _conversation(owner)

        owner_sock = _socket(app, "tester@example.com")
        owner_sock.emit("join_conversation", {"conversation_id": conversation.id})
        owner_sock.get_received()

        owner_client = _login_client(app, "tester@example.com")
        owner_client.post(
            f"/api/conversations/{conversation.id}/messages",
            json={"content": "hi"},
        )

        messages = _events(owner_sock, "message")
        assert messages
        user_messages = [
            _payload(event)
            for event in messages
            if isinstance(_payload(event), dict)
            and _payload(event).get("message", {}).get("role") == "user"
        ]
        assert user_messages
        assert user_messages[0]["sender_id"] == owner.id


class TestPresenceLifecycle:
    def test_presence_updates_on_join_and_leave(self, app, make_user):
        owner = make_user()
        viewer = make_user("viewer", "viewer@example.com")
        conversation = _conversation(owner)
        _share(conversation, viewer, owner=owner)

        owner_sock = _socket(app, "tester@example.com")
        viewer_sock = _socket(app, "viewer@example.com")
        owner_sock.emit("join_conversation", {"conversation_id": conversation.id})
        owner_sock.get_received()
        viewer_sock.emit("join_conversation", {"conversation_id": conversation.id})

        presence = _events(owner_sock, "presence")
        assert presence
        assert owner.username in _payload(presence[-1])["participants"]
        assert viewer.username in _payload(presence[-1])["participants"]

        viewer_sock.emit("leave_conversation", {"conversation_id": conversation.id})
        presence = _events(owner_sock, "presence")
        assert presence
        assert viewer.username not in _payload(presence[-1])["participants"]

    def test_disconnect_removes_presence(self, app, make_user):
        owner = make_user()
        viewer = make_user("viewer", "viewer@example.com")
        conversation = _conversation(owner)
        _share(conversation, viewer, owner=owner)

        owner_sock = _socket(app, "tester@example.com")
        viewer_sock = _socket(app, "viewer@example.com")
        owner_sock.emit("join_conversation", {"conversation_id": conversation.id})
        viewer_sock.emit("join_conversation", {"conversation_id": conversation.id})
        owner_sock.get_received()

        viewer_sock.disconnect()
        presence = _events(owner_sock, "presence")
        assert presence
        assert viewer.username not in _payload(presence[-1])["participants"]
