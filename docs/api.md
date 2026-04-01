## API Overview

- **Base URL (local dev)**: `http://127.0.0.1:8000`
- **Base URL (deployed Heroku App)**: "https://garr-distributedsocial-9837ac888a84.herokuapp.com/admin"
- **API prefix**: all API paths below are relative to `{BASE_URL}` (whether it is from local dev, or heroku app)
- **Authentication**:
  - **Local** (browser / same node): Django session (login form).
  - **Remote** (node-to-node): HTTP Basic Auth (see [Node-to-Node Authentication](#node-to-node-authentication) below).

## Authorization (owner-scoped mutations)

For local endpoints that mutate author-owned resources, the caller must be authenticated
as the same author in the URL (`AuthorAccount.author.uuid == AUTHOR_SERIAL`).

If this ownership check fails, the server returns `403 Forbidden`.

Protected operations include:
- `POST /api/authors/{AUTHOR_SERIAL}/entries`
- `PUT /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}`
- `DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}`
- `POST /api/authors/{AUTHOR_SERIAL}/images`
- `POST /authors/{AUTHOR_SERIAL}/entries/new/`
- `GET|POST /authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/edit/`
- `GET|POST /authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/delete/`
- `PUT /api/authors/{AUTHOR_SERIAL}`
- `GET|POST /authors/{AUTHOR_SERIAL}/edit`

All API objects use **FQIDs** (fully qualified IDs) in their `id` fields, e.g.:

- Author: `{BASE_URL}/api/authors/{AUTHOR_SERIAL}`
- Entry: `{BASE_URL}/api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}`

`AUTHOR_SERIAL` and `ENTRY_SERIAL` are serials (UUIDs or similar) that are unique per node. The combination of `{BASE_URL}` and the serial is globally unique.

---

## Node-to-Node Authentication

Remote nodes authenticate to our API using **HTTP Basic Auth**. This is separate from the session-based login used by browser users.

### How it works

Each remote node is represented by a `RemoteNode` record in the database, managed through the Django admin panel at `/admin/core/remotenode/`.

A `RemoteNode` stores two sets of credentials:

| Direction | Fields | Purpose |
|-----------|--------|---------|
| **Incoming** | Auto-created Django User (`node_user`) | The remote node uses these credentials (username + password) to call **our** API |
| **Outgoing** | `outgoing_username`, `outgoing_password` | **We** use these credentials to call the remote node's API |

When a node admin adds a new remote node via the admin panel, the system auto-generates a Django User with a random password. The admin panel displays the incoming credentials once at creation time — these must be shared with the remote team.

### Incoming requests (remote node → our API)

Remote nodes must include an `Authorization` header with every request to protected endpoints:

```http
GET /api/authors HTTP/1.1
Host: our-node.herokuapp.com
Authorization: Basic <base64(username:password)>
```

Where `<base64(username:password)>` is the Base64 encoding of `username:password` using the incoming credentials provided by our admin.

#### Example

If the incoming username is `node-remote.example.com` and password is `abc123`:

```http
Authorization: Basic bm9kZS1yZW1vdGUuZXhhbXBsZS5jb206YWJjMTIz
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `401 Unauthorized` | No `Authorization` header, malformed header, or invalid credentials. Response includes `WWW-Authenticate: Basic realm="node-to-node"` header. |
| `403 Forbidden` | Credentials are valid but the node has been **disabled** by the admin (`is_active = False`). Contact the node administrator to re-enable. |

#### Protected endpoints

Any endpoint decorated with `@require_node_auth` requires node-to-node Basic Auth. Currently this applies to federation / inbox endpoints as they are implemented. Public GET endpoints (authors list, stream, entry detail for public entries) remain accessible without node auth.

### Outgoing requests (our node → remote node)

When our node sends data to a remote node (e.g. pushing entries to a remote inbox), it uses the `outgoing_username` and `outgoing_password` stored in the `RemoteNode` record. This is handled by the `make_node_request()` utility in `config/core/request_utils.py`.

If the target node has been **disabled** (`is_active = False`), `make_node_request()` raises `NodeDisabled` and **no HTTP request is sent**. Callers should handle this exception appropriately (e.g. skip that node, log a warning).

### Disabling a Remote Node

A node admin can disable a remote node to cut off **all** node-to-node communication with it in both directions:

| Direction | Behaviour when disabled |
|-----------|------------------------|
| **Incoming** (remote → us) | Valid Basic Auth credentials are rejected with `403 Forbidden`. |
| **Outgoing** (us → remote) | `make_node_request()` raises `NodeDisabled` before any HTTP request is sent. |

Disabling a node does **not** delete any previously received remote content. It only stops future communication.

#### Example scenario

1. Admin adds remote node `https://other.herokuapp.com` with `is_active = True`.
2. That node can call our protected endpoints and we can push data to it -- everything works normally.
3. The remote node starts misbehaving, so the admin unchecks `is_active` in the Django admin list view.
4. **Incoming**: the remote node's next request gets `403 Forbidden`, even though its credentials are still valid.
5. **Outgoing**: any code that tries to call `make_node_request()` for that node raises `NodeDisabled` and no request is sent.
6. To re-enable, the admin checks `is_active` again. Communication resumes immediately.

### Admin management

Node admins manage remote node connections at `/admin/core/remotenode/`:

- **Add a node**: enter the remote node's base URL and outgoing credentials. Incoming credentials are auto-generated and displayed once.
- **Disable a node**: uncheck `is_active` in the list view. All communication is blocked in both directions.
- **Re-enable a node**: check `is_active` again. Communication resumes immediately.
- **Reset incoming password**: use the "Reset incoming password" admin action to generate a new password (share the new password with the remote team).
- **Remove a node**: delete the record. The associated Django User is also deleted.

---

## Template

Use this template for any list endpoint, e.g. `GET /api/authors/`, `GET /api/entries/`, `GET /api/stream/`.

```json
{
  "type": "authors",
  "authors": [
    {
      "type": "author",
      "id": "http://nodeaaaa/api/authors/111",
      "host": "http://nodeaaaa/api/",
      "displayName": "Greg Johnson",
      "github": "http://github.com/gjohnson",
      "profileImage": "https://i.imgur.com/k7XVwpB.jpeg",
      "web": "http://nodeaaaa/authors/greg"
    }
  ]
}
```

---

## Template: single-object endpoint

Use this template for endpoints like `GET /api/authors/{AUTHOR_SERIAL}/` or `GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}`.

### GET /api/.../{serial}/

- **When to use**:  
  Explain what the object is (author, entry, comment, like) and when a client should fetch it.

#### Request

- **Method**: `GET`
- **Path params**:
  - `{serial}` – the local serial (UUID or integer) used to look up the object.

#### Response

- **Status**: `200 OK` (or `404 Not Found` if the object does not exist or is soft-deleted).
- **Body** (`application/json`):
  - Object of the appropriate type:
    - Author: `type = "author"`
    - Entry:  `type = "entry"`
    - Comment: `type = "comment"`
    - Like: `type = "like"`

#### Example response (single author)

```json
{
  "type": "author",
  "id": "http://nodeaaaa/api/authors/111",
  "host": "http://nodeaaaa/api/",
  "displayName": "Lara Croft",
  "github": "http://github.com/gjohnson",
  "profileImage": "https://i.imgur.com/k7XVwpB.jpeg",
  "web": "http://nodeaaaa/authors/greg"
}
```

---

## Template: update endpoint (PUT/PATCH)

### PUT /api/.../{serial}/

- **When to use**:  
  Update an existing object (e.g. author profile, entry content).

#### Request

- **Method**: `PUT` (or `PATCH`, if supported).
- **Auth**:  
  Must be authenticated as the owning author or a node admin.
- **Body** (`application/json`):
  - Only the updatable fields for this object (e.g. displayName, github, profileImage).

#### Response

- **Status**: `200 OK` on success (or `400`/`403` as appropriate).
- **Body**: updated object in the same format as the corresponding `GET`.

---

## Template: delete endpoint (soft delete)

### DELETE /api/.../{serial}/

- **When to use**:  
  Soft-delete an object that should no longer appear in UI/API but must stay in the database.

#### Behaviour

- Mark the object as deleted (e.g. `is_deleted=True` or set `deleted_at`).
- Exclude it from all list/detail endpoints, except for node admin views.

#### Response

- **Status**: `204 No Content` on success.

---

## Stream Endpoint

### GET /stream/

- **Purpose**: Template-rendered stream page for a consolidated feed.
- **Behaviour**: node-wide PUBLIC discovery stream for entries known by this node.
- **Query logic parity**: Uses the same filters and ordering as `GET /api/stream`.

### GET /api/stream

- **Purpose**: Return a consolidated stream of entries an author should know about.
- **Auth**: None currently required for local development.
- **Query params**:
  - `page` (optional, default `1`)
  - `size` (optional, default `10`)

#### Latest version definition

This project currently uses an overwrite edit model: editing an entry updates the same database row.
The stream therefore returns the current row state, sorted most recent first by `updated_at`
(then `published`, then `uuid` descending as a deterministic tie-breaker).
Older versions are not returned, and deleted entries are excluded.

#### Deleted definition

An entry is treated as deleted and excluded from stream results when either of these is true:
- `is_deleted` is `true`
- `deleted_at` is set (not null)

Entries with `visibility = DELETED` are also excluded from stream results.

#### Entries included in this implementation

- Anonymous viewers receive `PUBLIC` entries only.
- Authenticated viewers also receive:
  - `UNLISTED` entries from authors they follow with `APPROVED` status.
  - `FRIENDS` entries from authors where the relationship is mutual `APPROVED` follow.
- Entries are read from the local `Entry` store, so the same rules apply to locally created entries and any remote entries that have been ingested and stored here.

#### Entry object shape

| Field | Type | Notes |
|-------|------|-------|
| `type` | `"entry"` | |
| `title` | string | May be empty |
| `id` | string (FQID) | Canonical API URL for this entry |
| `web` | string (URL) | Browser-viewable URL |
| `contentType` | string | `"text/plain"` or `"text/markdown"` |
| `content` | string | Body of the entry |
| `author` | author object | Embedded author |
| `comments` | comments object | First 5 comments; same shape as Comments API list |
| `likes` | likes object | First 5 likes; same shape as Likes API list |
| `published` | ISO 8601 | Creation time (UTC) |
| `updated_at` | ISO 8601 | Last edit time (UTC) |
| `visibility` | string | `PUBLIC`, `UNLISTED`, or `FRIENDS` |
| `image_urls` | array of strings | Absolute URLs of node-hosted images attached to this entry |

#### Example request

```http
GET /api/stream?page=1&size=1
```

The response order is newest-first according to `updated_at`.

#### Example response

```json
{
  "type": "entries",
  "page_number": 1,
  "size": 1,
  "count": 1,
  "src": [
    {
      "type": "entry",
      "title": "Weekly update",
      "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
      "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
      "contentType": "text/plain",
      "content": "Hello from the stream.",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "Stream Author",
        "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43",
        "github": "",
        "profileImage": ""
      },
      "comments": {
        "type": "comments",
        "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f/comments",
        "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      },
      "likes": {
        "type": "likes",
        "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f/likes",
        "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      },
      "published": "2026-02-28T12:00:00+00:00",
      "updated_at": "2026-02-28T12:05:00+00:00",
      "visibility": "PUBLIC",
      "image_urls": []
    }
  ]
}
```

Deleted entries are not included in stream responses.

---

## Entries API

### GET /api/authors/{AUTHOR_SERIAL}/entries

- **Purpose**: List entries belonging to a specific author, filtered by the caller's relationship to that author.
- **Auth**: None required (anonymous callers receive `PUBLIC` entries only).
- **Query params**:
  - `page` (optional, default `1`)
  - `size` (optional, default `10`)

#### Visibility rules

| Viewer | Entries returned |
|--------|-----------------|
| Anonymous / no relationship | `PUBLIC` only |
| Approved follower (non-mutual) | `PUBLIC` + `UNLISTED` |
| Mutual friend or the owner | `PUBLIC` + `UNLISTED` + `FRIENDS` |

#### Response

- **Status**: `200 OK`
- **Body**: Paginated `entries` envelope (same shape as the stream response).

---

### POST /api/authors/{AUTHOR_SERIAL}/entries

- **Purpose**: Create a new entry as the given author.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only). Returns `403` otherwise.
- **Body** (`application/json`):

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `content` | yes | — | Entry body text |
| `title` | no | `""` | Display title |
| `contentType` | no | `"text/plain"` | `"text/plain"` or `"text/markdown"` |
| `visibility` | no | `"PUBLIC"` | `"PUBLIC"`, `"UNLISTED"`, or `"FRIENDS"` |

#### Response

- **Status**: `201 Created` with the new entry object.
- **Status**: `400 Bad Request` if `content` is missing, `contentType` is unrecognised, or `visibility` is unrecognised.
- **Status**: `403 Forbidden` if not the owner.

#### Example request

```http
POST /api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries
Content-Type: application/json

{
  "title": "My first post",
  "content": "Hello world!",
  "contentType": "text/plain",
  "visibility": "PUBLIC"
}
```

#### Example response

```json
{
  "type": "entry",
  "title": "My first post",
  "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
  "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
  "contentType": "text/plain",
  "content": "Hello world!",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "Stream Author",
    "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43",
    "github": "",
    "profileImage": ""
  },
  "comments": { "type": "comments", "count": 0, "src": [] },
  "likes": { "type": "likes", "count": 0, "src": [] },
  "published": "2026-03-16T00:00:00+00:00",
  "updated_at": "2026-03-16T00:00:00+00:00",
  "visibility": "PUBLIC",
  "image_urls": []
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}

- **Purpose**: Fetch a single entry by its UUID.
- **Auth**: None required for `PUBLIC` and `UNLISTED` entries. `FRIENDS` entries require the caller to be a mutual friend. Node admins can access soft-deleted entries.

#### Visibility rules

| Entry visibility | Who can access |
|-----------------|---------------|
| `PUBLIC` | Anyone |
| `UNLISTED` | Anyone with the direct link |
| `FRIENDS` | Owner, mutual friends, node admins |
| `DELETED` | Node admins only |

### Friendship definition

A friendship means a mutual approved follow:

- `A -> B` is `APPROVED`
- `B -> A` is `APPROVED`

This is implemented with `FollowRelationship.are_friends(...)`.

#### Response

- **Status**: `200 OK` with entry object.
- **Status**: `403 Forbidden` if the caller lacks permission.
- **Status**: `404 Not Found` if the author or entry does not exist.
- `PUBLIC`: `200 OK` for anyone
- `UNLISTED`: `200 OK` for anyone with the link
- `FRIENDS`: `200 OK` only for author, friend, or admin; otherwise `403 Forbidden`
- `DELETED`: `200 OK` only for admin; otherwise `403 Forbidden`
---

### PUT /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}

- **Purpose**: Update an existing entry. All fields are optional; omitted fields keep their current value.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Body** (`application/json`):

| Field | Notes |
|-------|-------|
| `title` | New title |
| `content` | New body (must not be empty if provided) |
| `contentType` | `"text/plain"` or `"text/markdown"` |
| `visibility` | `"PUBLIC"`, `"UNLISTED"`, or `"FRIENDS"` |

#### Response

- **Status**: `200 OK` with the updated entry object.
- **Status**: `400 Bad Request` if `content` is empty, `contentType` or `visibility` is unrecognised, or the entry is already deleted.
- **Status**: `403 Forbidden` if not the owner.
- **Status**: `404 Not Found` if the entry does not exist.

---

### DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}

- **Purpose**: Soft-delete an entry. Sets `is_deleted=True`, `visibility=DELETED`, and records `deleted_at`. The row is retained in the database.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).

#### Response

- **Status**: `204 No Content` on success (also returned if the entry was already deleted).
- **Status**: `403 Forbidden` if not the owner.
- **Status**: `404 Not Found` if the entry or author does not exist.

---

## Image Hosting API

Node-hosted images can be uploaded and served at stable URLs suitable for embedding in `text/markdown` entry content.

### POST /api/authors/{AUTHOR_SERIAL}/images

- **Purpose**: Upload an image file to be hosted by this node.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Body** (`multipart/form-data`):
  - `file` or `image` — the image file to upload.
  - Accepted MIME types: `image/png`, `image/jpeg`, `image/gif`, `image/webp`.

#### Response

- **Status**: `201 Created`
- **Body** (`application/json`):

```json
{
  "url": "http://127.0.0.1:8000/api/media/images/e1a2b3c4-0000-0000-0000-000000000000/",
  "uuid": "e1a2b3c4-0000-0000-0000-000000000000"
}
```

- **Status**: `400 Bad Request` if no file is provided or the MIME type is not allowed.
- **Status**: `403 Forbidden` if not the owner.

---

### GET /api/media/images/{IMAGE_SERIAL}/

- **Purpose**: Serve a node-hosted image file by its UUID.
- **Auth**: Visibility follows the entry the image is attached to.
  - `PUBLIC` / `UNLISTED` entry images: accessible to anyone with the URL.
  - `FRIENDS` entry images: requires the caller to be a mutual friend of the uploader or a node admin.
  - Images not attached to any entry use the image's own `visibility` field with the same rules.
  - `DELETED` images are accessible to node admins only.
- **Response**: Raw image binary with the appropriate `Content-Type` header. Returns `400` if the file is missing on disk, `403` if the caller lacks permission.

---

## Follow API

Follow relationships represent one author wanting to follow another. A follow goes through states: **requesting** (pending approval) → **accepted** (approved) or **rejected** (denied).

FQIDs used in follow URL paths must be **percent-encoded**, e.g. `http%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fauthors%2F111`.

---

### GET /api/authors/{AUTHOR_SERIAL}/following

- **When to use**: List all authors that `{AUTHOR_SERIAL}` is following, including both pending and approved relationships. Use this when an author wants to see who they are following.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only). Returns `403` if not the owner.
- **Why not remote**: This is a local-only endpoint; a remote node has no reason to inspect another node's outgoing follow list.

#### Request

- **Method**: `GET`
- **Path params**: `AUTHOR_SERIAL` — UUID of the local author.

#### Response

- **Status**: `200 OK`
- **Status**: `403 Forbidden` if not the owner.
- **Body** (`application/json`):

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `type` | string | `"following"` | Identifies this as a following list |
| `following` | array | `[...]` | Array of author objects (pending + approved) |

Each item in `following` is a full author object.

#### Example response

```json
{
  "type": "following",
  "following": [
    {
      "type": "author",
      "id": "http://127.0.0.1:8000/api/authors/b-uuid",
      "host": "http://127.0.0.1:8000/api/",
      "displayName": "UserB",
      "web": "http://127.0.0.1:8000/authors/b-uuid",
      "github": "",
      "profileImage": ""
    }
  ]
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}

- **When to use**: Check whether `{AUTHOR_SERIAL}` is currently following (pending or approved) the author identified by `{FOREIGN_AUTHOR_FQID}`. Use this to drive UI state (e.g. show "Following" vs "Follow" button).
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Why**: Exposes only the requesting author's own follow state, so it is owner-scoped.

#### Request

- **Method**: `GET`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the author being checked.

#### Response

- **Status**: `200 OK` — a pending or approved follow exists. Body is the followed author object.
- **Status**: `404 Not Found` — no follow relationship exists.
- **Status**: `403 Forbidden` — not authenticated as the owner.

#### Example response (`200 OK`)

```json
{
  "type": "author",
  "id": "http://127.0.0.1:8000/api/authors/b-uuid",
  "host": "http://127.0.0.1:8000/api/",
  "displayName": "UserB",
  "web": "http://127.0.0.1:8000/authors/b-uuid",
  "github": "",
  "profileImage": ""
}
```

---

### PUT /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}

- **When to use**: Initiate a follow request from `{AUTHOR_SERIAL}` to `{FOREIGN_AUTHOR_FQID}`. If a previously denied request exists, it is re-set to pending. For remote authors, the follow request is POSTed to the remote author's inbox automatically. For local authors, the request is stored as pending until the followee approves it.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Why not remote**: Only the local author's own node initiates follow requests on their behalf.

#### Request

- **Method**: `PUT`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author initiating the follow.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the author to follow. For remote authors this must be the full URL of the author on their home node.
- **Body**: None required.

#### Behaviour

- If no relationship exists: creates it as `PENDING` (local target) or `APPROVED` (remote target — per spec, treat remote follows as immediately followed from the actor's node perspective, even before the remote author accepts).
- If a `DENIED` relationship exists: resets it to `PENDING`/`APPROVED` and re-sends the request.
- If already `PENDING` or `APPROVED`: returns `200 OK` with no change.
- For remote targets: POSTs a follow request object to `{FOREIGN_AUTHOR_HOST}/api/authors/{FOREIGN_SERIAL}/inbox`.
- On success for a remote author that is already `APPROVED`: also distributes existing entries from `{AUTHOR_SERIAL}` to the remote follower's inbox.

#### Response

- **Status**: `201 Created` — new follow relationship created or previously-denied request re-sent.
- **Status**: `200 OK` — relationship already existed.
- **Status**: `400 Bad Request` — tried to follow yourself, or remote fetch/distribution failed.
- **Status**: `403 Forbidden` — not the owner.
- **Status**: `404 Not Found` — target author does not exist locally and could not be fetched remotely.
- **Status**: `502 Bad Gateway` — remote node unreachable.
- **Body** (`application/json`):

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `type` | string | `"follow"` | Object type |
| `summary` | string | `"UserA wants to follow UserB"` | Human-readable description |
| `state` | string | `"requesting"` or `"accepted"` | Current follow state |
| `actor` | author object | `{...}` | The author sending the follow |
| `object` | author object | `{...}` | The author being followed |

#### Example response (`201 Created`)

```json
{
  "type": "follow",
  "summary": "UserA wants to follow UserB",
  "state": "requesting",
  "actor": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/a-uuid",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "UserA",
    "web": "http://127.0.0.1:8000/authors/a-uuid",
    "github": "",
    "profileImage": ""
  },
  "object": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/b-uuid",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "UserB",
    "web": "http://127.0.0.1:8000/authors/b-uuid",
    "github": "",
    "profileImage": ""
  }
}
```

---

### DELETE /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}

- **When to use**: Unfollow the target author. Permanently deletes the follow relationship record regardless of whether it was pending or approved. Use when the local author wants to stop following someone.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Why**: Only the follower should be able to remove their own outgoing follow.

#### Request

- **Method**: `DELETE`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author unfollowing.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the author to unfollow.

#### Response

- **Status**: `204 No Content` — relationship deleted.
- **Status**: `404 Not Found` — no follow relationship existed.
- **Status**: `403 Forbidden` — not the owner.

---

### GET /api/authors/{AUTHOR_SERIAL}/followers

- **When to use**: List all **approved** followers of `{AUTHOR_SERIAL}`. Use when displaying who follows an author, or when a remote node needs to verify the follower list.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner) **or** as an authenticated remote node (HTTP Basic Auth). Remote nodes may use this to verify whether a follow was accepted.
- **Why both**: The spec marks this `[local, remote]`.

#### Request

- **Method**: `GET`
- **Path params**: `AUTHOR_SERIAL` — UUID of the local author.

#### Response

- **Status**: `200 OK`
- **Status**: `403 Forbidden` — not the owner and not an authenticated remote node.
- **Body** (`application/json`):

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `type` | string | `"followers"` | Identifies this as a followers list |
| `followers` | array | `[...]` | Array of author objects (approved followers only; pending is excluded) |

#### Example response

```json
{
  "type": "followers",
  "followers": [
    {
      "type": "author",
      "id": "http://nodebbbb/api/authors/222",
      "host": "http://nodebbbb/api/",
      "displayName": "Lara Croft",
      "web": "http://nodebbbb/authors/222",
      "github": "http://github.com/laracroft",
      "profileImage": "http://nodebbbb/api/authors/222/entries/217/image"
    }
  ]
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}

- **When to use**: Check whether `{FOREIGN_AUTHOR_FQID}` is an **approved** follower of `{AUTHOR_SERIAL}`. Use to verify a follow was accepted. Note: a pending follow request does NOT satisfy this check — only `APPROVED` status returns `200`.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner) **or** as an authenticated remote node.

#### Request

- **Method**: `GET`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author being followed.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the potential follower.

#### Response

- **Status**: `200 OK` — `FOREIGN_AUTHOR_FQID` is an approved follower. Body is the follower's author object.
- **Status**: `404 Not Found` — not an approved follower (pending is not sufficient).
- **Status**: `403 Forbidden` — not authorized.

#### Example response (`200 OK`)

```json
{
  "type": "author",
  "id": "http://nodebbbb/api/authors/222",
  "host": "http://nodebbbb/api/",
  "displayName": "Lara Croft",
  "web": "http://nodebbbb/authors/222",
  "github": "http://github.com/laracroft",
  "profileImage": "http://nodebbbb/api/authors/222/entries/217/image"
}
```

---

### PUT /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}

- **When to use**: Accept a **pending** follow request from `{FOREIGN_AUTHOR_FQID}`. Use when the author reviews their follow requests and approves one. After approval, if the new follower is a remote author, existing entries from `{AUTHOR_SERIAL}` are distributed to their inbox.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Why not remote**: Only the followee approves their own followers.

#### Request

- **Method**: `PUT`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author accepting the follow.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the author to accept.
- **Body**: None required.

#### Behaviour

- Looks up a `PENDING` `FollowRelationship` where `follower=FOREIGN_AUTHOR` and `followee=AUTHOR_SERIAL`.
- If found: sets status to `APPROVED`.
- If the newly approved follower is a remote author: fans out `{AUTHOR_SERIAL}`'s existing entries to that remote author's inbox.
- If not found: returns `404`.

#### Response

- **Status**: `200 OK` — follow accepted. Body is the follow object with `state: "accepted"`.
- **Status**: `404 Not Found` — no matching pending request.
- **Status**: `403 Forbidden` — not the owner.
- **Body** (`application/json`):

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `type` | string | `"follow"` | Object type |
| `summary` | string | `"UserA wants to follow UserB"` | Human-readable description |
| `state` | string | `"accepted"` | Confirmed accepted state |
| `actor` | author object | `{...}` | The follower |
| `object` | author object | `{...}` | The followee (this author) |

#### Example response (`200 OK`)

```json
{
  "type": "follow",
  "summary": "UserA wants to follow UserB",
  "state": "accepted",
  "actor": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/a-uuid",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "UserA",
    "web": "http://127.0.0.1:8000/authors/a-uuid",
    "github": "",
    "profileImage": ""
  },
  "object": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/b-uuid",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "UserB",
    "web": "http://127.0.0.1:8000/authors/b-uuid",
    "github": "",
    "profileImage": ""
  }
}
```

---

### DELETE /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}

- **When to use**: Reject a pending follow request, or remove an already-approved follower. Use when the author wants to deny someone following them, or revoke a previously accepted follow.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Why not remote**: Only the followee manages who is allowed to follow them.

#### Request

- **Method**: `DELETE`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author removing the follower.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the follower to remove.

#### Behaviour (differs by current relationship state)

| Current state | Behaviour | Response |
|---------------|-----------|----------|
| `PENDING` | Sets status to `DENIED`. Record is kept in DB. | `204 No Content` |
| `APPROVED` | Deletes the relationship record entirely. | `204 No Content` |
| Does not exist or `DENIED` | Returns error. | `404 Not Found` |

#### Response

- **Status**: `204 No Content` — request denied or follower removed.
- **Status**: `404 Not Found` — no matching pending request or approved follower.
- **Status**: `403 Forbidden` — not the owner.

---

### GET /api/authors/{AUTHOR_SERIAL}/follow_requests

- **When to use**: List all **pending** incoming follow requests that `{AUTHOR_SERIAL}` needs to review. Use to populate the "follow requests" notification in the UI.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).
- **Why**: An author should only see their own incoming requests.

#### Request

- **Method**: `GET`
- **Path params**: `AUTHOR_SERIAL` — UUID of the local author.

#### Response

- **Status**: `200 OK`
- **Status**: `403 Forbidden` — not the owner.
- **Body** (`application/json`):

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `type` | string | `"follow_requests"` | Identifies this as a follow request list |
| `requests` | array | `[...]` | Array of follow objects (pending only) |
| `items` | array | `[...]` | Same as `requests` — included for backwards compatibility |

Each item in `requests`/`items` is a follow object with `state: "requesting"`.

#### Example response

```json
{
  "type": "follow_requests",
  "requests": [
    {
      "type": "follow",
      "summary": "UserA wants to follow UserB",
      "state": "requesting",
      "actor": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/a-uuid",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "UserA",
        "web": "http://127.0.0.1:8000/authors/a-uuid",
        "github": "",
        "profileImage": ""
      },
      "object": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/b-uuid",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "UserB",
        "web": "http://127.0.0.1:8000/authors/b-uuid",
        "github": "",
        "profileImage": ""
      }
    }
  ],
  "items": ["... same as requests ..."]
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/friends

- **When to use**: List all mutual friends of `{AUTHOR_SERIAL}` — authors where both the follow from `{AUTHOR_SERIAL}` to them **and** their follow back to `{AUTHOR_SERIAL}` are `APPROVED`.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).

#### Request

- **Method**: `GET`
- **Path params**: `AUTHOR_SERIAL` — UUID of the local author.

#### Response

- **Status**: `200 OK`
- **Status**: `403 Forbidden` — not the owner.
- **Body** (`application/json`):

| Field | Type | Example | Purpose |
|-------|------|---------|---------|
| `type` | string | `"friends"` | Identifies this as a friends list |
| `friends` | array | `[...]` | Array of author objects (mutual approved follows only) |

#### Example response

```json
{
  "type": "friends",
  "friends": [
    {
      "type": "author",
      "id": "http://127.0.0.1:8000/api/authors/b-uuid",
      "host": "http://127.0.0.1:8000/api/",
      "displayName": "UserB",
      "web": "http://127.0.0.1:8000/authors/b-uuid",
      "github": "",
      "profileImage": ""
    }
  ]
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/friends/{FOREIGN_AUTHOR_FQID}

- **When to use**: Check whether a specific author is a mutual friend of `{AUTHOR_SERIAL}`. Use to drive visibility decisions (e.g. whether a `FRIENDS` entry should be shown to the viewer).
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Request

- **Method**: `GET`
- **Path params**:
  - `AUTHOR_SERIAL` — UUID of the local author.
  - `FOREIGN_AUTHOR_FQID` — percent-encoded FQID of the author to check friendship with.

#### Response

- **Status**: `200 OK` — they are mutual friends. Body is the friend's author object.
- **Status**: `404 Not Found` — not mutual friends.
- **Status**: `403 Forbidden` — not the owner.

---

## Comments API

Comments are attached to an entry. Each comment has an author, body, content type (`text/plain` or `text/markdown`), and published time.
For `FRIENDS` entries, friendship in this repo means a mutual `APPROVED` follow (`FollowRelationship.are_friends`).
Comment visibility is enforced with the same shared queryset logic in the REST API and the HTML entry detail page.
Deleted entries never expose comments. This repo does not soft-delete comments today; deleted comments are excluded because they are removed from the `Comment` table.

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments

- **When to use**: List comments on an entry. Use when displaying the comment thread or when syncing comments (e.g. for a remote node).
- **Auth**: None required for public entries. The response only includes comments visible to the current viewer.
- **Visibility on `FRIENDS` entries**:
  - The entry author sees all comments on the entry.
  - The entry author's friends see all comments on the entry.
  - A non-friend commenter sees only comments they authored themselves.
  - A non-friend who did not author a comment sees no comments.
  - Anonymous viewers see no comments.
- **Query params**:
  - `page` (optional, default `1`) – 1-based page number
  - `size` (optional, default `10`) – page size

#### Response

- **Status**: `200 OK` (or `400` if the entry is deleted).
- **Body**: A `comments` object with `type`, `id`, `web`, `page_number`, `size`, `count`, and `src` (array of comment objects).

#### Example response

```json
{
  "type": "comments",
  "id": "http://127.0.0.1:8000/api/authors/f67eb8e9-57e9-494b-88f4-c7234ce3f39b/entries/141ffa4c-43ec-42c6-83bb-e65eb72db04d/comments",
  "web": "http://127.0.0.1:8000/authors/f67eb8e9-57e9-494b-88f4-c7234ce3f39b/entries/141ffa4c-43ec-42c6-83bb-e65eb72db04d",
  "page_number": 1,
  "size": 5,
  "count": 3,
  "src": [
    {
      "type": "comment",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/...",
        "displayName": "Commenter",
        "github": "",
        "profileImage": "",
        "web": "..."
      },
      "comment": "Owner comment",
      "contentType": "text/plain",
      "published": "2026-03-01T12:00:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/.../commented/...",
      "entry": "http://127.0.0.1:8000/api/authors/.../entries/...",
      "web": "http://127.0.0.1:8000/authors/.../entries/..."
    },
    {
      "type": "comment",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/...",
        "displayName": "Friend",
        "github": "",
        "profileImage": "",
        "web": "..."
      },
      "comment": "Friend comment",
      "contentType": "text/plain",
      "published": "2026-03-01T12:05:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/.../commented/...",
      "entry": "http://127.0.0.1:8000/api/authors/.../entries/...",
      "web": "http://127.0.0.1:8000/authors/.../entries/..."
    },
    {
      "type": "comment",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/...",
        "displayName": "Stranger Commenter",
        "github": "",
        "profileImage": "",
        "web": "..."
      },
      "comment": "Stranger commenter comment",
      "contentType": "text/plain",
      "published": "2026-03-01T12:10:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/.../commented/...",
      "entry": "http://127.0.0.1:8000/api/authors/.../entries/...",
      "web": "http://127.0.0.1:8000/authors/.../entries/..."
    }
  ]
}
```

#### Example response for a non-friend commenter on the same `FRIENDS` entry

```json
{
  "type": "comments",
  "id": "http://127.0.0.1:8000/api/authors/f67eb8e9-57e9-494b-88f4-c7234ce3f39b/entries/141ffa4c-43ec-42c6-83bb-e65eb72db04d/comments",
  "web": "http://127.0.0.1:8000/authors/f67eb8e9-57e9-494b-88f4-c7234ce3f39b/entries/141ffa4c-43ec-42c6-83bb-e65eb72db04d",
  "page_number": 1,
  "size": 5,
  "count": 1,
  "src": [
    {
      "type": "comment",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/...",
        "displayName": "Stranger Commenter",
        "github": "",
        "profileImage": "",
        "web": "..."
      },
      "comment": "Stranger commenter comment",
      "contentType": "text/plain",
      "published": "2026-03-01T12:10:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/.../commented/...",
      "entry": "http://127.0.0.1:8000/api/authors/.../entries/...",
      "web": "http://127.0.0.1:8000/authors/.../entries/..."
    }
  ]
}
```

### POST /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments

- **When to use**: Create a comment on an entry (local or from a remote node sending to inbox; for Part E, local POST is used).
- **Auth**: Author must be identified via session (logged-in user with AuthorAccount) or via request body (`authorId` or `author.id`).
- **Body** (`application/json`):
  - `comment` (required) – text of the comment
  - `contentType` (optional) – `text/plain` or `text/markdown`; default `text/plain`
  - `authorId` (optional) – UUID of the commenting author if not using session

#### Response

- **Status**: `201 Created` with the new comment object in the body, or `400 Bad Request` if `comment` is missing, author cannot be resolved, or `contentType` is invalid.
- **Federation behavior**: newly created local comments are fanned out to relevant remote inboxes as `type: "comment"` payloads.

#### Example request

Session-authenticated callers may omit `authorId` (the server uses the logged-in author). Remote clients or tests often pass the commenting author explicitly:

```http
POST /api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f/comments
Content-Type: application/json

{
  "authorId": "11111111-1111-1111-1111-111111111111",
  "comment": "Great post!",
  "contentType": "text/plain"
}
```

You may also identify the author with a nested `author` object (`id` / `fqid` / `uuid`) or top-level `author_id` / `authorId` strings (same resolution rules as entry likes).

#### Example response (`201 Created`)

```json
{
  "type": "comment",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "Commenter",
    "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
    "github": "",
    "profileImage": ""
  },
  "comment": "Great post!",
  "contentType": "text/plain",
  "published": "2026-03-16T12:00:00+00:00",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "entry": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
  "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f"
}
```

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_REF}

- **When to use**: Fetch a single comment by UUID or by FQID (comment ref may be the comment UUID or the percent-encoded comment FQID).
- **Behaviour**: The server loads the comment for this entry, then checks **comment visibility** (same rules as `GET .../comments`): if the resolved comment is not in the visible set for the current viewer, the response is `404` (same JSON shape as a missing comment).
- **Response**:
  - **`200 OK`**: Single `comment` object.
  - **`400 Bad Request`**: Entry is deleted / not visible (`is_visible` is false).
  - **`404 Not Found`**: No comment on this entry matches `COMMENT_REF`, **or** the comment exists but is **not visible** to the current viewer (including anonymous on `FRIENDS` threads).

### DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_REF}

- **When to use**: Delete a comment. Only the **comment author** may delete.
- **Auth**: The actor must be resolved via session or JSON body (`authorId`, nested `author`, etc., same as POST comments) and must equal `comment.author`.
- **Behaviour**: The server resolves the comment by **entry + ref** first (UUID or FQID), **without** applying the comment-list visibility filter. That way the author can delete their own comment even when they would no longer see it in `GET .../comments` (for example after unfollowing on a `FRIENDS` entry). Ownership is enforced after lookup.
- **Response**:
  - **`204 No Content`**: Comment deleted.
  - **`400 Bad Request`**: Entry deleted, JSON body invalid, or author cannot be resolved.
  - **`403 Forbidden`**: Resolved author is not the comment author.
  - **`404 Not Found`**: No comment on this entry matches `COMMENT_REF`.
- **Federation behavior**: Successful local deletions are fanned out as `type: "comment_delete"` inbox payloads to keep remote copies consistent.

---

## Likes API

Likes are per (author, entry); at most one like per author per entry.

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/likes

- **When to use**: List who liked an entry. Use when displaying like count or list on the entry detail/stream.
- **Auth**: None required.
- **Access control (current implementation)**: The handler only requires that the entry exists and is not deleted (`entry.is_visible`). It lists likes for that entry **without** applying the same **entry visibility** rules as `GET .../entries/{ENTRY_SERIAL}` (e.g. `FRIENDS` / follower checks). Callers should not rely on this endpoint to hide likes for entries they cannot view on the entry-detail API.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK` with the likes envelope, or **`400 Bad Request`** if the entry is deleted.
- **Body**: A `likes` object with `type`, `id`, `web`, `page_number`, `size`, `count`, and `src` (array of like objects). Each like has `type`, `author`, `published`, `id`, and `object` (the entry FQID).

#### Example response

```json
{
  "type": "likes",
  "id": "http://127.0.0.1:8000/api/authors/.../entries/.../likes",
  "web": "http://127.0.0.1:8000/authors/.../entries/...",
  "page_number": 1,
  "size": 5,
  "count": 1,
  "src": [
    {
      "type": "like",
      "author": { "type": "author", "id": "...", "displayName": "Liker", "..." },
      "published": "2026-03-01T12:00:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/.../liked/...",
      "object": "http://127.0.0.1:8000/api/authors/.../entries/..."
    }
  ]
}
```

### POST /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/likes

- **When to use**: Record a like from an author on an entry. Idempotent: if the author already liked the entry, returns `200` with the existing like.
- **Auth**: Author must be identified via session or request body (`authorId`).
- **Body** (`application/json`): Optional. May include `authorId` (author UUID) if not using session.

#### Response

- **Status**: `201 Created` when a new like is created, or `200 OK` when the like already existed. Body is the like object.
- **`400 Bad Request`**: Entry deleted, invalid JSON, or like author cannot be resolved.

#### Example request

Session-authenticated callers may send an empty JSON object (`{}`). Otherwise pass `authorId` or the same alternate author fields as for comment POST:

```http
POST /api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f/likes
Content-Type: application/json

{
  "authorId": "11111111-1111-1111-1111-111111111111"
}
```

#### Example response (`201 Created`)

```json
{
  "type": "like",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "Liker",
    "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
    "github": "",
    "profileImage": ""
  },
  "published": "2026-03-16T12:00:00+00:00",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/liked/b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "object": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f"
}
```

If the like already exists, the server returns **`200 OK`** with the same like object shape (idempotent).

### DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/likes

- **When to use**: Remove the like (unlike) for the given author.
- **Auth**: Author must be identified via session or request body (`authorId`).
- **Body** (`application/json`): Optional. May include `authorId` if not using session.

#### Response

- **Status**: `204 No Content` on success (also when there was no like to remove). **`400 Bad Request`** if the entry is deleted or the like author cannot be resolved.

---

## Comment Likes API

Comment likes are per (author, comment); at most one like per author per comment.

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/likes

- **When to use**: List who liked a comment.
- **Auth**: None required.
- **Access control**: The comment must be in the **visible comments** set for this entry and viewer (same rules as `GET .../comments`). If the comment is not visible, lookup returns **`404 Not Found`**.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK`, or **`400 Bad Request`** if the entry is deleted, or **`404 Not Found`** if the comment does not exist on this entry or is not visible to the viewer.
- **Body**: A `likes` object with `type`, `id`, `page_number`, `size`, `count`, and `src` (array of like objects). Each like has `type`, `author`, `published`, `id`, and `object` (the comment FQID).

### POST /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/likes

- **When to use**: Record a like from an author on a comment. Idempotent: if the author already liked the comment, returns `200` with the existing like.
- **Auth**: Author must be identified via session or request body (`authorId`).
- **Body** (`application/json`): Optional. May include `authorId` (author UUID) if not using session.

#### Response

- **Status**: `201 Created` when a new like is created.
- **Status**: `200 OK` when the like already existed (idempotent).
- **Status**: `400 Bad Request` if author cannot be resolved.
- **Status**: `404 Not Found` if the entry or comment does not exist, is hidden from the caller, or does not match the path.

##### Example request

```http
POST /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/likes
Content-Type: application/json

{
  "authorId": "8d35d13e-f0ee-468d-bd6f-f942ec660f43"
}
```

##### Example response (201 Created)

```json
{
  "type": "like",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43",
    "displayName": "Liker",
    "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43",
    "github": "",
    "profileImage": ""
  },
  "published": "2026-03-01T12:00:00+00:00",
  "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/liked/123e4567-e89b-12d3-a456-426614174000",
  "object": "http://127.0.0.1:8000/api/authors/.../commented/{COMMENT_SERIAL}"
}
```

### DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/likes

- **When to use**: Remove the like (unlike) for the given author on a comment.
- **Auth**: Author must be identified via session or request body (`authorId`).
- **Body** (`application/json`): Optional. May include `authorId` if not using session.

#### Response

- **Status**: `204 No Content` on success (even if there was no like).
- **Status**: `400 Bad Request` if author cannot be resolved.
- **Status**: `404 Not Found` if the entry or comment does not exist, is hidden from the caller, or does not match the path.
- **Federation behavior**: if a like existed and is removed locally, a `type: "like_delete"` inbox payload is sent to relevant remote inboxes.

### UI: Comment Like/Unlike

Each comment on the entry detail page shows:
- The total like count (e.g. "3 likes").
- A **Like** or **Unlike** button (depending on whether the current user has already liked it).
- On `FRIENDS` entries, the rendered comment list uses the same visibility rule as the API.

HTML endpoints:
- `POST /authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/like/` – like a comment, redirects back to entry detail.
- `POST /authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/unlike/` – unlike a comment, redirects back to entry detail.

---

## Commented API

The "commented" endpoints expose the comments an author has made, addressed by the comment author's UUID rather than the entry's. This is the spec-required pattern: `api/authors/{AUTHOR_SERIAL}/commented`.

### GET /api/authors/{AUTHOR_SERIAL}/commented

- **When to use**: List all comments that `{AUTHOR_SERIAL}` has authored across all entries.
- **Access control**:
  - **Local callers** (session-authenticated): see comments on any entry.
  - **Remote callers** (node Basic Auth): see only comments on `PUBLIC` and `UNLISTED` entries.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK`
- **Body**: A `comments` object with `type`, `id`, `page_number`, `size`, `count`, and `src`.

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111/commented?page=1&size=5
```

#### Example response

```json
{
  "type": "comments",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented",
  "page_number": 1,
  "size": 5,
  "count": 2,
  "src": [
    {
      "type": "comment",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "Greg Johnson",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
        "github": "",
        "profileImage": ""
      },
      "comment": "Sick Olde English",
      "contentType": "text/markdown",
      "published": "2026-03-09T13:07:04+00:00",
      "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001",
      "entry": "http://127.0.0.1:8000/api/authors/22222222-2222-2222-2222-222222222222/entries/33333333-3333-3333-3333-333333333333",
      "web": "http://127.0.0.1:8000/authors/22222222-2222-2222-2222-222222222222/entries/33333333-3333-3333-3333-333333333333",
      "likes": {
        "type": "likes",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001/likes",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001/likes",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      }
    }
  ]
}
```

### POST /api/authors/{AUTHOR_SERIAL}/commented

- **When to use**: Create a comment via the comment author's URL. The body is a comment object with an `entry` field that identifies the target entry (FQID or UUID). The node creates the comment locally and distributes it to the entry owner's inbox if the entry is remote.
- **Auth**: Local only (session-authenticated as the author or providing author identity in body).
- **Body** (`application/json`):

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `entry` | yes | — | Entry FQID (URL) or UUID |
| `comment` | yes | — | Comment text |
| `contentType` | no | `"text/plain"` | `"text/plain"` or `"text/markdown"` |

#### Response

- **Status**: `201 Created` with the new comment object.
- **Status**: `400 Bad Request` if `comment` or `entry` is missing, or `contentType` is invalid.
- **Status**: `404 Not Found` if the referenced entry does not exist on this node.

#### Example request

```http
POST /api/authors/11111111-1111-1111-1111-111111111111/commented
Content-Type: application/json

{
  "entry": "http://127.0.0.1:8000/api/authors/22222222-2222-2222-2222-222222222222/entries/33333333-3333-3333-3333-333333333333",
  "comment": "Great post!",
  "contentType": "text/plain"
}
```

#### Example response (`201 Created`)

```json
{
  "type": "comment",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "Greg Johnson",
    "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
    "github": "",
    "profileImage": ""
  },
  "comment": "Great post!",
  "contentType": "text/plain",
  "published": "2026-03-25T12:00:00+00:00",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/d4e5f6a7-0000-0000-0000-000000000002",
  "entry": "http://127.0.0.1:8000/api/authors/22222222-2222-2222-2222-222222222222/entries/33333333-3333-3333-3333-333333333333",
  "web": "http://127.0.0.1:8000/authors/22222222-2222-2222-2222-222222222222/entries/33333333-3333-3333-3333-333333333333",
  "likes": {
    "type": "likes",
    "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/d4e5f6a7-0000-0000-0000-000000000002/likes",
    "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111/commented/d4e5f6a7-0000-0000-0000-000000000002/likes",
    "page_number": 1,
    "size": 5,
    "count": 0,
    "src": []
  }
}
```

### GET /api/authors/{AUTHOR_SERIAL}/commented/{COMMENT_SERIAL}

- **When to use**: Fetch a single comment by its UUID, scoped to the comment author.
- **Access control**: Same as the list endpoint (remote callers only see comments on public/unlisted entries).

#### Response

- **Status**: `200 OK` with a single comment object.
- **Status**: `404 Not Found` if the comment does not exist or the caller lacks access.

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001
```

### GET /api/authors/{AUTHOR_SERIAL}/commented/{COMMENT_SERIAL}/likes

- **When to use**: List who liked a specific comment, addressed via the comment author's URL.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK` with a `likes` object.
- **Body**: Same shape as the entry likes list but with `object` pointing to the comment FQID.

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001/likes
```

#### Example response

```json
{
  "type": "likes",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001/likes",
  "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001/likes",
  "page_number": 1,
  "size": 10,
  "count": 1,
  "src": [
    {
      "type": "like",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/22222222-2222-2222-2222-222222222222",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "Lara Croft",
        "web": "http://127.0.0.1:8000/authors/22222222-2222-2222-2222-222222222222",
        "github": "",
        "profileImage": ""
      },
      "published": "2026-03-25T13:00:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/22222222-2222-2222-2222-222222222222/liked/e5f6a7b8-0000-0000-0000-000000000003",
      "object": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/commented/a1b2c3d4-0000-0000-0000-000000000001"
    }
  ]
}
```

This endpoint also supports `POST` (create a like) and `DELETE` (remove a like) with the same body format as the entry-scoped comment likes.

---

## Liked API

The "liked" endpoints show everything an author has liked, including both entry likes and comment likes merged into a single paginated list.

### GET /api/authors/{AUTHOR_SERIAL}/liked

- **When to use**: List all things `{AUTHOR_SERIAL}` has liked (entries and comments), sorted by most recent first.
- **Auth**: Accessible to local and remote callers.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK`
- **Body**: A `likes` object. Each item in `src` is a like object whose `object` field points to either an entry FQID or a comment FQID.

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111/liked?page=1&size=10
```

#### Example response

```json
{
  "type": "likes",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/liked",
  "page_number": 1,
  "size": 10,
  "count": 2,
  "src": [
    {
      "type": "like",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "Greg Johnson",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
        "github": "",
        "profileImage": ""
      },
      "published": "2026-03-25T14:00:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/liked/f6a7b8c9-0000-0000-0000-000000000004",
      "object": "http://127.0.0.1:8000/api/authors/22222222-2222-2222-2222-222222222222/entries/33333333-3333-3333-3333-333333333333"
    },
    {
      "type": "like",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "Greg Johnson",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
        "github": "",
        "profileImage": ""
      },
      "published": "2026-03-25T13:00:00+00:00",
      "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/liked/a7b8c9d0-0000-0000-0000-000000000005",
      "object": "http://127.0.0.1:8000/api/authors/44444444-4444-4444-4444-444444444444/commented/55555555-5555-5555-5555-555555555555"
    }
  ]
}
```

### GET /api/authors/{AUTHOR_SERIAL}/liked/{LIKE_SERIAL}

- **When to use**: Fetch a single like by its UUID. Searches both entry likes and comment likes.

#### Response

- **Status**: `200 OK` with a single like object.
- **Status**: `404 Not Found` if no like with that UUID exists for this author.

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111/liked/f6a7b8c9-0000-0000-0000-000000000004
```

---

## FQID Shortcut Routes

These endpoints allow looking up objects by their fully qualified ID (FQID) instead of by serial UUID. The FQID must be percent-encoded in the URL path.

### GET /api/entries/{ENTRY_FQID}/comments

- **When to use**: Get comments on an entry identified by its FQID (useful when you know the full URL but not the individual UUID components).
- **Access control**: Same visibility rules as `GET /api/authors/{SERIAL}/entries/{SERIAL}/comments`.

#### Example request

```http
GET /api/entries/http%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fauthors%2F222%2Fentries%2F249/comments
```

### GET /api/entries/{ENTRY_FQID}/likes

- **When to use**: Get likes on an entry identified by its FQID.
- **Access control**: Local callers only.

### GET /api/commented/{COMMENT_FQID}

- **When to use**: Fetch a single comment by its FQID.
- **Access control**: Local callers only.

#### Example request

```http
GET /api/commented/http%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fauthors%2F111%2Fcommented%2F130
```

### GET /api/liked/{LIKE_FQID}

- **When to use**: Fetch a single like by its FQID. Searches both entry likes and comment likes.
- **Access control**: Local callers only.

---

## User Registration (Signup with Admin Approval)

### GET /api/authors

- **Purpose**: Retrieve the paginated list of non-deleted author profiles known by this node.
- **Auth**: None required.
- **Query params**:
  - `page` (optional, default `1`)
  - `size` (optional, default `10`)

#### Example request

```http
GET /api/authors?page=1&size=2
```

#### Example response

```json
{
  "type": "authors",
  "page_number": 1,
  "size": 2,
  "count": 2,
  "authors": [
    {
      "type": "author",
      "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
      "host": "http://127.0.0.1:8000/api/",
      "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
      "displayName": "Alice",
      "github": "https://github.com/alice",
      "profileImage": "https://example.com/alice.png",
      "description": "About Alice"
    },
    {
      "type": "author",
      "id": "http://127.0.0.1:8000/api/authors/33333333-3333-3333-3333-333333333333",
      "host": "http://127.0.0.1:8000/api/",
      "web": "http://127.0.0.1:8000/authors/33333333-3333-3333-3333-333333333333",
      "displayName": "Bob",
      "github": "https://github.com/bob",
      "profileImage": "https://example.com/bob.png",
      "description": "About Bob"
    }
  ]
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `200 OK` | Author list returned successfully |

---

### POST /api/authors

- **Purpose**: Create a new local user registration through the API, mirroring the browser signup flow.
- **Auth**: None required.
- **Body** (`application/json`):
  - `username` (string, required)
  - `displayName` or `display_name` (string, required)
  - `password1` (string, required)
  - `password2` (string, required, must match `password1`)
  - `github` (string, optional)
  - `profileImage` or `profile_image` (string, optional)
  - `description` (string, optional)

#### Behaviour

1. Validates the signup payload.
2. Creates a Django `User` with `is_active=False`.
3. Creates a linked local `Author`.
4. Returns the new author object and indicates that admin approval is still required.

#### Example request

```http
POST /api/authors
Content-Type: application/json

{
  "username": "apiuser",
  "displayName": "API User",
  "password1": "strongPass99",
  "password2": "strongPass99",
  "github": "https://github.com/apiuser",
  "profileImage": "https://example.com/apiuser.png",
  "description": "Created through the API"
}
```

#### Example response

```json
{
  "pendingApproval": true,
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
    "host": "http://127.0.0.1:8000/api/",
    "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
    "displayName": "API User",
    "github": "https://github.com/apiuser",
    "profileImage": "https://example.com/apiuser.png",
    "description": "Created through the API"
  }
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `201 Created` | Pending account and author created successfully |
| `400 Bad Request` | Invalid JSON or validation errors |

---

## Admin Author Management API

These endpoints are for node administrators to add, modify, and soft-delete authors.

### Authentication and Authorization

- Caller must be authenticated as a local Django user session.
- Caller must be an admin user (`is_staff=True` or `is_superuser=True`).
- If not authenticated, endpoints return `401`.
- If authenticated but not admin, endpoints return `403`.

### POST /api/admin/authors

- **Purpose**: Admin creates an author directly (optional linked local login account).
- **Auth**: Publicly Accessible
- **Body** (`application/json`):
  - `displayName` (string, required)
  - `github` (string, optional)
  - `profileImage` (string, optional)
  - `description` (string, optional)
  - `id` or `fqid` (string URL, optional; defaults to local canonical ID)
  - `host` (string URL, optional; defaults to local API host)
  - `web` (string URL, optional; defaults to local profile URL)
  - `isLocal` (boolean, optional, default `true`)
  - `username` + `password` (optional pair; if provided creates linked `User` + `AuthorAccount`)
  - `isActive` (boolean, optional; applies to created linked user)

#### Example request

```http
POST /api/admin/authors
Content-Type: application/json

{
  "displayName": "Node Managed Author",
  "github": "https://github.com/nodeauthor",
  "profileImage": "https://example.com/nodeauthor.png",
  "description": "Created by node admin",
  "username": "node_author",
  "password": "StrongPass123!",
  "isActive": true
}
```

#### Example response

```json
{
  "type": "admin_author_create",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/7f3bd47b-0c72-4e81-9fd1-5c3a4f021c30",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "Node Managed Author",
    "github": "https://github.com/nodeauthor",
    "profileImage": "https://example.com/nodeauthor.png",
    "web": "http://127.0.0.1:8000/authors/7f3bd47b-0c72-4e81-9fd1-5c3a4f021c30"
  },
  "linkedUser": "node_author"
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `201 Created` | Author created |
| `400 Bad Request` | Missing/invalid fields, duplicate username, or FQID conflict |
| `401 Unauthorized` | Not logged in |
| `403 Forbidden` | Not an admin user |

---

### PUT /api/admin/authors/{AUTHOR_SERIAL}

- **Purpose**: Admin updates author fields (including soft-delete toggle).
- **Auth**: Admin-only.
- **Body** (`application/json`): any subset of
  - `displayName`, `github`, `profileImage`, `description`, `host`, `web`, `id`/`fqid`, `isLocal`, `isDeleted`

#### Example request

```http
PUT /api/admin/authors/7f3bd47b-0c72-4e81-9fd1-5c3a4f021c30
Content-Type: application/json

{
  "displayName": "Node Managed Author (Updated)",
  "github": "https://github.com/nodeauthor-updated",
  "isDeleted": false
}
```

#### Example response

```json
{
  "type": "admin_author_update",
  "author": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/7f3bd47b-0c72-4e81-9fd1-5c3a4f021c30",
    "host": "http://127.0.0.1:8000/api/",
    "displayName": "Node Managed Author (Updated)",
    "github": "https://github.com/nodeauthor-updated",
    "profileImage": "https://example.com/nodeauthor.png",
    "web": "http://127.0.0.1:8000/authors/7f3bd47b-0c72-4e81-9fd1-5c3a4f021c30"
  }
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `200 OK` | Author updated |
| `400 Bad Request` | Invalid field values or FQID conflict |
| `401 Unauthorized` | Not logged in |
| `403 Forbidden` | Not an admin user |
| `404 Not Found` | Author not found |

---

### DELETE /api/admin/authors/{AUTHOR_SERIAL}

- **Purpose**: Admin soft-deletes an author.
- **Auth**: Admin-only.
- **Behavior**: sets `is_deleted=true` and `deleted_at` timestamp; does not remove the DB row.

#### Example request

```http
DELETE /api/admin/authors/7f3bd47b-0c72-4e81-9fd1-5c3a4f021c30
```

#### Response

- **Status**: `204 No Content` on success.

#### Status codes

| Status | Meaning |
|--------|---------|
| `204 No Content` | Author soft-deleted |
| `401 Unauthorized` | Not logged in |
| `403 Forbidden` | Not an admin user |
| `404 Not Found` | Author not found |

---

### GET /accounts/signup/

- **Purpose**: Render the signup form for new users.
- **Auth**: None required (public page).

### POST /accounts/signup/

- **Purpose**: Create a new user account pending admin approval.
- **Auth**: None required (public page).
- **Body** (`application/x-www-form-urlencoded`):
  - `username` (string, required) -- the login username
  - `display_name` (string, required) -- display name for the Author profile
  - `password1` (string, required) -- password
  - `password2` (string, required) -- password confirmation (must match `password1`)

#### Behaviour

1. Creates a Django `User` with `is_active=False` (cannot log in until approved).
2. Creates an `Author` profile with the given `display_name`.
3. Creates an `AuthorAccount` linking the `User` to the `Author`.
4. Renders a "pending approval" confirmation page.

#### Approval workflow

- A node admin logs into Django admin (`/admin/`).
- Under **Users**, pending users appear with `is_active = False`.
- The admin checks the `Active` checkbox (or uses the "Approve selected users" action) to activate the account.
- Once `is_active = True`, the user can log in at `/accounts/login/`.

#### Example flow

```
GET /accounts/signup/        -> 200 (signup form)
POST /accounts/signup/       -> 200 (pending approval page)
Admin sets is_active=True    -> user can now login
POST /accounts/login/        -> 302 redirect to follows/ui
```

#### Example request

```http
POST /accounts/signup/
Content-Type: application/x-www-form-urlencoded

username=newuser&display_name=New%20User&password1=strongPass99&password2=strongPass99
```

#### Example response

- **Status**: `200 OK` – the signup form is re-rendered as a "pending approval" page.
- **Body**: HTML page containing text like:

```html
<h1>Account Created</h1>
<p>Your account has been created and is pending admin approval.</p>
```

#### Error responses

- **Duplicate username**: re-renders form with "A user with that username already exists."
- **Mismatched passwords**: re-renders form with "Passwords do not match."
- **Missing required fields** (e.g., `display_name`): re-renders form with appropriate validation errors and does **not** create a user.

#### Status codes

| Status      | Meaning                                                      |
|------------|--------------------------------------------------------------|
| `200 OK`   | Form rendered (initial, success pending approval, or errors) |

---

## Author Profile API

### GET /api/authors/{AUTHOR_SERIAL}

- **Purpose**: Retrieve the public profile of an author.
- **Auth**: None required (public endpoint).

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111
```

#### Example response

```json
{
  "type": "author",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
  "host": "http://127.0.0.1:8000/api/",
  "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
  "displayName": "Alice",
  "github": "https://github.com/alice",
  "profileImage": "https://example.com/alice.png",
  "description": "About Alice"
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `200 OK` | Profile returned successfully |
| `404 Not Found` | Author does not exist or is deleted |

---

### PUT /api/authors/{AUTHOR_SERIAL}

- **Purpose**: Update the authenticated author's profile.
- **Auth**: Must be authenticated as the author in the URL path (owner-only).

#### Example request

```http
PUT /api/authors/11111111-1111-1111-1111-111111111111
Content-Type: application/json

{
  "displayName": "Alice Updated",
  "description": "Updated description",
  "github": "https://github.com/alice-updated",
  "profileImage": "https://example.com/alice-v2.png"
}
```

#### Example response

```json
{
  "type": "author",
  "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
  "host": "http://127.0.0.1:8000/api/",
  "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
  "displayName": "Alice Updated",
  "github": "https://github.com/alice-updated",
  "profileImage": "https://example.com/alice-v2.png",
  "description": "Updated description"
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `200 OK` | Profile updated successfully |
| `400 Bad Request` | Invalid JSON body or validation errors |
| `403 Forbidden` | Not authenticated as the owner, or author is not local |
| `404 Not Found` | Author does not exist or is deleted |

---

### Profile Management (UI)

Authors can manage their profile from the browser:
- **View profile**: `GET /authors/{AUTHOR_SERIAL}` — public page showing profile info, entries, and GitHub activity.
- **Edit profile**: `GET /authors/{AUTHOR_SERIAL}/edit` — form to update display name, description, profile image, and GitHub URL (owner-only).
- **Save changes**: `POST /authors/{AUTHOR_SERIAL}/edit` — saves the edited profile and redirects to the profile page.

---

## GitHub Activity API

### GET /api/authors/{AUTHOR_SERIAL}/github

- **Purpose**: Fetch the author's public GitHub activity, sync new events into the local database as public entries, and return the list of GitHub-sourced entries.
- **Auth**: None required (public endpoint).

#### Behaviour

1. Extracts the GitHub username from the author's `github` URL field.
2. Calls `https://api.github.com/users/{username}/events/public` to fetch recent public events.
3. Creates a new `Entry` for each event not already stored (deduplicated by `external_id`).
4. Returns all GitHub-sourced entries from the database (most recent first, up to 30).

#### Example request

```http
GET /api/authors/11111111-1111-1111-1111-111111111111/github
```

#### Example response

```json
{
  "type": "github_activity",
  "events": [
    {
      "type": "github_event",
      "title": "GitHub: PushEvent",
      "content": "Pushed 2 commit(s) to octocat/Hello-World: Initial commit; Add README",
      "published": "2026-03-15T12:00:00+00:00",
      "id": "github-12345678"
    },
    {
      "type": "github_event",
      "title": "GitHub: WatchEvent",
      "content": "Starred django/django",
      "published": "2026-03-15T11:00:00+00:00",
      "id": "github-12345679"
    }
  ]
}
```

#### Status codes

| Status | Meaning |
|--------|---------|
| `200 OK` | Events returned (may be empty if no GitHub URL or no events) |
| `404 Not Found` | Author does not exist or is deleted |

#### When author has no GitHub URL

```json
{
  "type": "github_activity",
  "events": []
}
```

---

## Inbox API

### POST /api/authors/{AUTHOR_SERIAL}/inbox

- **Purpose**: Receive remote payloads from other nodes. This is the primary endpoint for inter-node communication (federation).
- **Auth**: Must be authenticated as a remote node (HTTP Basic Auth via `RemoteNode` credentials).

#### Accepted payload types

| `type` field | Purpose | Handler |
|-------------|---------|---------|
| `"follow"` | Follow request from a remote author | Creates/updates `FollowRelationship` |
| `"entry"` | Entry pushed by a remote author to a local follower's inbox | Creates/updates local `Entry` record |
| `"like"` | Like from a remote author on a local entry or comment | Creates `EntryLike` or `CommentLike` |

#### Follow payload

```json
{
  "type": "follow",
  "actor": {
    "type": "author",
    "id": "https://remote.example/api/authors/remote-uuid",
    "host": "https://remote.example/api/",
    "displayName": "Remote Author",
    "web": "https://remote.example/authors/remote-uuid",
    "github": "",
    "profileImage": ""
  },
  "object": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/local-uuid",
    "displayName": "Local Author"
  }
}
```

**Response**: `201 Created` with follow state.

#### Entry payload

```json
{
  "type": "entry",
  "id": "https://remote.example/api/authors/remote-uuid/entries/entry-uuid",
  "title": "Remote Post Title",
  "content": "Post body text",
  "contentType": "text/plain",
  "visibility": "PUBLIC",
  "web": "https://remote.example/authors/remote-uuid/entries/entry-uuid",
  "author": {
    "type": "author",
    "id": "https://remote.example/api/authors/remote-uuid",
    "host": "https://remote.example/api/",
    "displayName": "Remote Author",
    "web": "https://remote.example/authors/remote-uuid",
    "github": "",
    "profileImage": ""
  },
  "published": "2026-03-22T12:00:00+00:00"
}
```

**Response**: `201 Created` if new entry, `200 OK` if updated (deduped by FQID).

#### Like payload

The `object` field is resolved by exact FQID lookup on this node:
- If the FQID matches a local entry, creates an `EntryLike`
- If the FQID matches a local comment, creates a `CommentLike`

Access control is enforced before creation:
- Entry likes require the remote actor to be able to view that entry under current visibility rules.
- Comment likes require the remote actor to be able to view that comment under the same shared comment-visibility logic used by stream/detail APIs.

```json
{
  "type": "like",
  "author": {
    "type": "author",
    "id": "https://remote.example/api/authors/remote-liker",
    "host": "https://remote.example/api/",
    "displayName": "Remote Liker",
    "web": "https://remote.example/authors/remote-liker",
    "github": "",
    "profileImage": ""
  },
  "object": "http://127.0.0.1:8000/api/authors/local-uuid/entries/entry-uuid"
}
```

**Response**: `201 Created` if new like, `200 OK` if already existed (idempotent).

#### Status codes

| Status | Meaning |
|--------|---------|
| `201 Created` | Payload accepted and new record created |
| `200 OK` | Payload accepted, record already existed (idempotent) |
| `400 Bad Request` | Invalid JSON, missing required fields, or unsupported type |
| `401 Unauthorized` | Missing or invalid node credentials |
| `403 Forbidden` | Node is disabled, or caller is not a remote node |
| `404 Not Found` | Target local author not found |

---

## Entry Distribution (Outgoing)

When a local author creates an entry (via API or UI), the node automatically distributes it to remote followers' inboxes:

| Entry visibility | Recipients |
|-----------------|------------|
| `PUBLIC` | All remote authors with an APPROVED follow on the entry author |
| `FRIENDS` | Only remote authors who are mutual friends (both directions APPROVED) |
| `UNLISTED` | Not distributed (accessible only via direct link) |
| `DELETED` | Not distributed |

Distribution uses `make_node_request()` to POST the entry payload to each recipient's inbox. Disabled nodes and unreachable nodes are silently skipped.

---

## Comment-Like Distribution (Outgoing)

When a local author likes a comment, the node fans out a `like` payload to relevant remote inboxes that should already know about the content (for example: remote comment/entry authors and eligible remote recipients by follow/friend rules).

Likes on comments of locally-authored entries do not trigger outgoing distribution (the like is already stored locally).

Loop prevention rule:
- Outgoing distribution happens only for local user actions (UI/API endpoints).
- Inbox-ingested likes are persisted locally but are **not** re-forwarded to other nodes.
