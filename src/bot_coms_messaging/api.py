from __future__ import annotations

import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ConfigDict

from .config import allowed, default_root, load_config
from .store import Problem, Store


class Send(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_message_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=32000)
    recipients: list[str] = Field(default_factory=list, max_length=12)
    revision: int | None = None


class Group(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)
    profiles: list[str] = Field(min_length=2, max_length=12)
    default_responder: str
    client_request_id: str = Field(min_length=1, max_length=128)


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)
    profiles: list[str] = Field(min_length=2, max_length=12)
    default_responder: str
    revision: int


class ReadState(BaseModel):
    sequence: int = Field(ge=0)


def create_router(root_factory=default_root):
    router = APIRouter(prefix='/v1')

    def context(request: Request):
        # Never trust a client principal header. Older auth without verified identities fails closed.
        session = getattr(request.state, 'session', None)
        if session is None or not getattr(session, 'user_id', None):
            raise HTTPException(401, 'A verified Hermes dashboard identity is required')
        principal = f'{session.provider}:{session.user_id}'
        if getattr(session, 'org_id', None):
            principal += ':' + session.org_id
        try:
            root = root_factory()
            config = load_config(root)
            if not request.url.path.endswith('/capabilities') and (
                    request.query_params.get('expected_server') != config['server_id'] or
                    request.query_params.get('expected_principal') != principal):
                raise Problem(403, 'The authenticated account or server changed; refresh messaging')
            profiles = allowed(config, principal)
            if not any(principal in p['principals'] for p in config['profiles']):
                raise Problem(403, 'This account has no configured messaging profiles')
            return Store(root), config, principal, profiles
        except Problem as e:
            raise HTTPException(e.status, e.detail)

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Problem as e:
            raise HTTPException(e.status, e.detail)

    def authorize(ctx, cid):
        store, _, owner, profiles = ctx
        h = invoke(store.history, owner, cid)
        # Revoked profile access revokes shared history; an unavailable configured worker does not.
        config = ctx[1]
        accessible = {p['id'] for p in config['profiles'] if owner in p['principals']}
        if not set(h['conversation']['profiles']).issubset(accessible):
            raise HTTPException(403, 'Conversation access changed')
        return h

    def recipients(ctx, targets):
        accessible = {p['id'] for p in ctx[1]['profiles'] if ctx[2] in p['principals']}
        if not set(targets).issubset(accessible):
            raise HTTPException(403, 'A requested profile is unauthorized')
        if not set(targets).issubset({p['id'] for p in ctx[3]}):
            raise HTTPException(409, 'A requested profile is temporarily unavailable')

    @router.get('/capabilities')
    def capabilities(request: Request):
        try:
            ctx = context(request)
        except HTTPException as error:
            if error.status_code != 503:
                raise
            return dict(server_id='', principal_id='', api_version=1, state='needs_configuration', features=[], profiles=[])
        store, config, owner, profiles = ctx
        with store.db() as db:
            heartbeat = db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
        active = heartbeat is not None and time.time() - float(heartbeat['value']) < 30
        # Worker writes heartbeat only after loading bot-coms and initializing its spool.
        return dict(server_id=config['server_id'], principal_id=owner, api_version=1,
                    state='ready' if active and profiles else 'needs_configuration', features=['dm', 'groups', 'read_state'],
                    profiles=[dict(id=p['id'], name=p['name'], displayName=p.get('display_name', p['name'])) for p in profiles])

    @router.get('/conversations')
    def conversations(cursor: str = '', ctx=Depends(context)):
        page = invoke(ctx[0].conversations, ctx[2], cursor)
        accessible = {p['id'] for p in ctx[1]['profiles'] if ctx[2] in p['principals']}
        page['conversations'] = [c for c in page['conversations'] if set(c['profiles']).issubset(accessible)]
        return page

    @router.post('/conversations')
    def group(body: Group, ctx=Depends(context)):
        recipients(ctx, body.profiles)
        return invoke(ctx[0].groups, ctx[2], body.title, body.profiles, body.default_responder, body.client_request_id)

    @router.get('/conversations/{cid}')
    def history(cid: str, before: int | None = Query(None, ge=1), ctx=Depends(context)):
        authorize(ctx, cid)
        return invoke(ctx[0].history, ctx[2], cid, before)

    @router.get('/dms/{profile}')
    def dm(profile: str, before: int | None = Query(None, ge=1), ctx=Depends(context)):
        recipients(ctx, [profile])
        cid = invoke(ctx[0].dm_id, ctx[2], profile)
        return invoke(ctx[0].history, ctx[2], cid, before)

    @router.post('/dms/{profile}/messages')
    def dm_send(profile: str, body: Send, ctx=Depends(context)):
        recipients(ctx, [profile] + body.recipients)
        p = next(p for p in ctx[3] if p['id'] == profile)
        return invoke(ctx[0].send, ctx[2], body.client_message_id, body.body, body.recipients,
                      dm=(profile, p.get('display_name', p['name'])), revision=body.revision)

    @router.post('/conversations/{cid}/messages')
    def send(cid: str, body: Send, ctx=Depends(context)):
        h = authorize(ctx, cid)
        recipients(ctx, body.recipients or [h['conversation']['default_responder']])
        return invoke(ctx[0].send, ctx[2], body.client_message_id, body.body, body.recipients, cid=cid, revision=body.revision)

    @router.get('/dms/{profile}/messages/by-client-id/{client_id}')
    def dm_receipt(profile: str, client_id: str, ctx=Depends(context)):
        recipients(ctx, [profile])
        return invoke(ctx[0].receipt, ctx[2], invoke(ctx[0].dm_id, ctx[2], profile), client_id)

    @router.get('/conversations/{cid}/messages/by-client-id/{client_id}')
    def receipt(cid: str, client_id: str, ctx=Depends(context)):
        authorize(ctx, cid)
        return invoke(ctx[0].receipt, ctx[2], cid, client_id)

    @router.put('/conversations/{cid}/read-state')
    def read(cid: str, body: ReadState, ctx=Depends(context)):
        authorize(ctx, cid)
        return invoke(ctx[0].read, ctx[2], cid, body.sequence)

    @router.patch('/conversations/{cid}/user-state')
    def state(cid: str, body: dict, ctx=Depends(context)):
        authorize(ctx, cid)
        return invoke(ctx[0].user_state, ctx[2], cid, body)

    @router.patch('/conversations/{cid}')
    def update(cid: str, body: GroupUpdate, ctx=Depends(context)):
        authorize(ctx, cid); recipients(ctx, body.profiles)
        return invoke(ctx[0].update_group, ctx[2], cid, body.revision, body.title, body.profiles, body.default_responder)

    @router.post('/runs/{run}/{action}')
    def run_action(run: str, action: str, ctx=Depends(context)):
        if action not in ('cancel', 'retry'):
            raise HTTPException(404)
        with ctx[0].db() as db:
            d = db.execute('SELECT conversation FROM dispatches WHERE id=?', (run,)).fetchone()
        if d is None:
            raise HTTPException(404)
        authorize(ctx, d['conversation'])
        return invoke(ctx[0].run_action, ctx[2], run, action)

    @router.get('/events')
    def events(after: int = Query(0, ge=0), ctx=Depends(context)):
        result = invoke(ctx[0].events, ctx[2], after)
        # Events contain only IDs; revalidate membership before publishing even those IDs.
        visible = []
        for event in result['events']:
            try: authorize(ctx, event['conversation'])
            except HTTPException: continue
            visible.append(event)
        result['events'] = visible
        return result

    return router


router = create_router()
