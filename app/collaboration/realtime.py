"""Real-time conversation collaboration over Socket.IO (issue #50).

Clients join a per-conversation room. New messages and typing events are
broadcast only to that room, so a member viewing one conversation never sees
another conversation's traffic. Room membership is authorized on every join
with the same rules as the HTTP read surface:

* the conversation owner,
* a user the conversation was explicitly shared with, or
* an active member of the conversation's workspace.

Socket handlers reuse the Flask-Login session, so a socket cannot join a room
an HTTP request could not read. ``leave_conversation`` and ``disconnect`` both
remove presence state; the browser client reconnects automatically and rejoins
the active room.
"""

from __future__ import annotations

import contextlib
import threading

from flask import request, session
from flask_socketio import emit, join_room, leave_room

from app.extensions import db, socketio
from app.models import Conversation, User
from app.services import permissions

ROLE_OWNER = "owner"
ROLE_SHARED = "shared"
ROLE_WORKSPACE = "workspace"

_PRESENCE: dict[str, dict[str, str]] = {}
_PRESENCE_LOCK = threading.Lock()


def room_name(conversation_id: int) -> str:
    """Return the Socket.IO room used for a conversation."""
    return f"conversation:{conversation_id}"


def socket_user():
    """Resolve the socket's user from its own Flask session.

    Flask-Login caches the authenticated user on the application context.
    Reading the session directly keeps concurrent sockets authenticated as
    different users (important for tests and for multi-tab use); it never
    grants more access than the session's own user id.
    """
    user_id = session.get("_user_id")
    if user_id is None:
        return None
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def resolve_conversation(conversation_id, user) -> tuple[Conversation | None, str | None]:
    """Return ``(conversation, role)`` when ``user`` may read it.

    ``role`` is ``None`` when access is denied. Unknown and inaccessible
    conversations are indistinguishable to the caller.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None, None
    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        return None, None
    conversation = db.session.get(Conversation, conversation_id)
    if conversation is None:
        return None, None
    if conversation.user_id == user.id:
        return conversation, ROLE_OWNER
    if conversation.is_shared_with(user.id):
        return conversation, ROLE_SHARED
    if (
        conversation.workspace_id is not None
        and permissions.role_for(conversation.workspace_id, user) is not None
    ):
        return conversation, ROLE_WORKSPACE
    return None, None


def _presence_payload(conversation_id: int) -> dict:
    room = room_name(conversation_id)
    with _PRESENCE_LOCK:
        users = sorted(set(_PRESENCE.get(room, {}).values()))
    return {"conversation_id": conversation_id, "participants": users}


def _add_presence(conversation_id: int, username: str) -> None:
    room = room_name(conversation_id)
    with _PRESENCE_LOCK:
        _PRESENCE.setdefault(room, {})[request.sid] = username


def _remove_presence(conversation_id: int) -> None:
    room = room_name(conversation_id)
    with _PRESENCE_LOCK:
        participants = _PRESENCE.get(room)
        if participants is not None:
            participants.pop(request.sid, None)
            if not participants:
                _PRESENCE.pop(room, None)


def _remove_presence_everywhere(sid: str) -> list[int]:
    """Remove ``sid`` from every room and return the affected conversation ids."""
    affected: list[int] = []
    with _PRESENCE_LOCK:
        for room, participants in list(_PRESENCE.items()):
            if sid in participants:
                participants.pop(sid, None)
                with contextlib.suppress(IndexError, ValueError):
                    affected.append(int(room.split(":", 1)[1]))
                if not participants:
                    _PRESENCE.pop(room, None)
    return affected


def _deny(message: str = "Conversation not found.") -> None:
    emit("error", {"error": message, "kind": "not_found"})


@socketio.on("join_conversation")
def handle_join_conversation(data):
    """Join one conversation room after authorizing read access."""
    data = data or {}
    user = socket_user()
    conversation, role = resolve_conversation(data.get("conversation_id"), user)
    if conversation is None or role is None:
        _deny()
        return
    room = room_name(conversation.id)
    join_room(room)
    username = getattr(user, "username", None) or f"user-{user.id}"
    _add_presence(conversation.id, username)
    emit(
        "joined",
        {
            "conversation_id": conversation.id,
            "room": room,
            "role": role,
        },
    )
    emit("presence", _presence_payload(conversation.id), to=room)


@socketio.on("leave_conversation")
def handle_leave_conversation(data):
    """Leave a conversation room and update presence."""
    data = data or {}
    try:
        conversation_id = int(data.get("conversation_id"))
    except (TypeError, ValueError):
        _deny()
        return
    room = room_name(conversation_id)
    leave_room(room)
    _remove_presence(conversation_id)
    emit("left", {"conversation_id": conversation_id})
    emit("presence", _presence_payload(conversation_id), to=room)


@socketio.on("typing")
def handle_typing(data):
    """Broadcast a typing indicator within one conversation room."""
    data = data or {}
    user = socket_user()
    conversation, role = resolve_conversation(data.get("conversation_id"), user)
    if conversation is None or role is None:
        _deny()
        return
    room = room_name(conversation.id)
    # Only participants who joined the room may publish; this also keeps a
    # shared read-only user from generating events.
    if request.sid not in _PRESENCE.get(room, {}):
        _deny()
        return
    emit(
        "typing",
        {
            "conversation_id": conversation.id,
            "user_id": user.id,
            "username": getattr(user, "username", ""),
            "typing": bool(data.get("typing", True)),
        },
        to=room,
        include_self=False,
    )


@socketio.on("disconnect")
def handle_disconnect(*_args):
    """Clear presence for a disconnecting client."""
    affected = _remove_presence_everywhere(request.sid)
    for conversation_id in affected:
        emit("presence", _presence_payload(conversation_id), to=room_name(conversation_id))


def broadcast_message(conversation: Conversation, message, *, sender=None) -> None:
    """Broadcast a persisted message to the conversation room.

    Called after the HTTP route commits so clients never receive a message that
    was rolled back. The sender id lets the originating client avoid rendering
    its own message twice.
    """
    if conversation is None or message is None:
        return
    payload = message.to_dict() if hasattr(message, "to_dict") else message
    socketio.emit(
        "message",
        {
            "conversation_id": conversation.id,
            "message": payload,
            "sender_id": getattr(sender, "id", None),
        },
        to=room_name(conversation.id),
    )


def reset_presence() -> None:
    """Clear in-process presence state (tests)."""
    with _PRESENCE_LOCK:
        _PRESENCE.clear()


__all__ = [
    "ROLE_OWNER",
    "ROLE_SHARED",
    "ROLE_WORKSPACE",
    "broadcast_message",
    "reset_presence",
    "resolve_conversation",
    "room_name",
]
