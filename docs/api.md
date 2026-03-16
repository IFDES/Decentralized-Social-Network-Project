## API Overview

- **Base URL (local dev)**: `http://127.0.0.1:8000`
- **Base URL (deployed)**: Heroku App
- **API prefix**: all API paths below are relative to `{BASE_URL}` (whether it is from local dev, or heroku app)
- **Authentication**:
  - **Local** (browser / same node): Django session (login form) or whatever the team chooses.
  - **Remote** (node-to-node): HTTP Basic Auth as required by the project spec (to be wired in later project parts).

## Authorization (owner-scoped mutations)

For local endpoints that mutate author-owned resources, the caller must be authenticated
as the same author in the URL (`AuthorAccount.author.uuid == AUTHOR_SERIAL`).

If this ownership check fails, the server returns `403 Forbidden`.

Protected operations include:
- `POST /api/authors/{AUTHOR_SERIAL}/entries`
- `PUT /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}`
- `DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}`
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

- Includes all `PUBLIC` entries this node currently knows about via the local `Entry` store.
  This covers local entries and also covers remote entries if/when they are ingested and stored as `Entry` rows.
- Excludes all non-public entries (`FRIENDS`, `UNLISTED`, and any equivalent private visibility values).
- Relationship-based visibility (follows/friends) is not implemented in this repo yet, so no additional relationship visibility is applied.
- Non-public entries are excluded for all viewers.

#### Example request

```http
GET /api/stream?page=1&size=2
```

The response order is newest-first according to `updated_at`.

#### Example response

```json
{
  "type": "entries",
  "page_number": 1,
  "size": 2,
  "count": 2,
  "src": [
    {
      "type": "entry",
      "title": "Weekly update",
      "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
      "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/d25343a5-c5cf-4734-b8cf-11211f7af26f",
      "description": "",
      "contentType": "text/plain",
      "content": "Edited content",
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
      "visibility": "PUBLIC"
    },
    {
      "type": "entry",
      "title": "Another public post",
      "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/entries/22222222-2222-2222-2222-222222222222",
      "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111/entries/22222222-2222-2222-2222-222222222222",
      "description": "",
      "contentType": "text/plain",
      "content": "A second public message",
      "author": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111",
        "host": "http://127.0.0.1:8000/api/",
        "displayName": "Another Author",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111",
        "github": "",
        "profileImage": ""
      },
      "comments": {
        "type": "comments",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/entries/22222222-2222-2222-2222-222222222222/comments",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111/entries/22222222-2222-2222-2222-222222222222",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      },
      "likes": {
        "type": "likes",
        "id": "http://127.0.0.1:8000/api/authors/11111111-1111-1111-1111-111111111111/entries/22222222-2222-2222-2222-222222222222/likes",
        "web": "http://127.0.0.1:8000/authors/11111111-1111-1111-1111-111111111111/entries/22222222-2222-2222-2222-222222222222",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      },
      "published": "2026-02-28T11:20:00+00:00",
      "updated_at": "2026-02-28T11:20:00+00:00",
      "visibility": "PUBLIC"
    }
  ]
}
```

Deleted entries are not included in stream responses.

---

## Follow API

Follow relationships represent one author wanting to follow another. A follow goes through states: **requesting** (pending approval) → **accepted** (approved) or **rejected** (denied). All follow endpoints are author-scoped and require the caller to be authenticated as the author in the URL path.

FQIDs (fully qualified IDs) used in follow URL paths must be **percent-encoded**, e.g. `http%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fauthors%2F111`.

### GET /api/authors/{AUTHOR_SERIAL}/following

- **When to use**: List the authors that `{AUTHOR_SERIAL}` is following (includes both pending and approved).
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).

#### Response

- **Status**: `200 OK`, or `403 Forbidden` if not the owner.
- **Body** (`application/json`):

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

- **When to use**: Check whether `{AUTHOR_SERIAL}` is following the author identified by `{FOREIGN_AUTHOR_FQID}`.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Response

- **Status**: `200 OK` with the followed author object if a pending or approved relationship exists, or `404 Not Found` if not following.
- **Body** (`application/json`) on 200:

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

### PUT /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}

- **When to use**: Create a follow request from `{AUTHOR_SERIAL}` to `{FOREIGN_AUTHOR_FQID}`. If a previously denied request exists, it is re-set to pending.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Request

- **Method**: `PUT`
- **Body**: None required.

#### Response

- **Status**: `201 Created` if newly created, `200 OK` if already existed.
- **Status**: `400 Bad Request` if trying to follow yourself or a remote author (remote not yet supported).
- **Status**: `403 Forbidden` if not the owner.
- **Status**: `404 Not Found` if the target author does not exist.
- **Body** (`application/json`):

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

### DELETE /api/authors/{AUTHOR_SERIAL}/following/{FOREIGN_AUTHOR_FQID}

- **When to use**: Unfollow the target author. Deletes the follow relationship entirely.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Response

- **Status**: `204 No Content` on success.
- **Status**: `404 Not Found` if no follow relationship existed.
- **Status**: `403 Forbidden` if not the owner.

---

### GET /api/authors/{AUTHOR_SERIAL}/followers

- **When to use**: List the approved followers of `{AUTHOR_SERIAL}`.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).

#### Response

- **Status**: `200 OK`, or `403 Forbidden` if not the owner.
- **Body** (`application/json`):

```json
{
  "type": "followers",
  "followers": [
    {
      "type": "author",
      "id": "http://127.0.0.1:8000/api/authors/a-uuid",
      "host": "http://127.0.0.1:8000/api/",
      "displayName": "UserA",
      "web": "http://127.0.0.1:8000/authors/a-uuid",
      "github": "",
      "profileImage": ""
    }
  ]
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}

- **When to use**: Check whether `{FOREIGN_AUTHOR_FQID}` is an approved follower of `{AUTHOR_SERIAL}`.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Response

- **Status**: `200 OK` with the follower's author object, or `404 Not Found` if not an approved follower.

### PUT /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}

- **When to use**: Accept a pending follow request from `{FOREIGN_AUTHOR_FQID}`.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Request

- **Method**: `PUT`
- **Body**: None required.

#### Response

- **Status**: `200 OK` with follow object (state=`accepted`).
- **Status**: `404 Not Found` if there is no matching pending request.
- **Status**: `403 Forbidden` if not the owner.
- **Body** (`application/json`):

```json
{
  "type": "follow",
  "summary": "UserA wants to follow UserB",
  "state": "accepted",
  "actor": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/a-uuid",
    "displayName": "UserA",
    "...": "..."
  },
  "object": {
    "type": "author",
    "id": "http://127.0.0.1:8000/api/authors/b-uuid",
    "displayName": "UserB",
    "...": "..."
  }
}
```

### DELETE /api/authors/{AUTHOR_SERIAL}/followers/{FOREIGN_AUTHOR_FQID}

- **When to use**: Reject a pending follow request, or remove an approved follower.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Response

- **Status**: `200 OK` with follow object (state=`rejected`) when denying a pending request.
- **Status**: `204 No Content` when removing an approved follower.
- **Status**: `404 Not Found` if no matching relationship exists.
- **Status**: `403 Forbidden` if not the owner.

---

### GET /api/authors/{AUTHOR_SERIAL}/follow_requests

- **When to use**: List incoming follow requests (pending only) that `{AUTHOR_SERIAL}` needs to approve or deny.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).

#### Response

- **Status**: `200 OK`, or `403 Forbidden` if not the owner.
- **Body** (`application/json`):

```json
{
  "type": "follow_requests",
  "items": [
    {
      "type": "follow",
      "summary": "UserA wants to follow UserB",
      "state": "requesting",
      "actor": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/a-uuid",
        "displayName": "UserA",
        "...": "..."
      },
      "object": {
        "type": "author",
        "id": "http://127.0.0.1:8000/api/authors/b-uuid",
        "displayName": "UserB",
        "...": "..."
      }
    }
  ]
}
```

---

### GET /api/authors/{AUTHOR_SERIAL}/friends

- **When to use**: List mutual friends (both authors follow each other with approved status).
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}` (owner-only).

#### Response

- **Status**: `200 OK`, or `403 Forbidden` if not the owner.
- **Body** (`application/json`):

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

### GET /api/authors/{AUTHOR_SERIAL}/friends/{FOREIGN_AUTHOR_FQID}

- **When to use**: Check whether a specific author is a mutual friend.
- **Auth**: Must be authenticated as `{AUTHOR_SERIAL}`.

#### Response

- **Status**: `200 OK` with the friend's author object if they are mutual friends, or `404 Not Found` if not friends.

---

## Comments API

Comments are attached to an entry. Each comment has an author, body, content type (`text/plain` or `text/markdown`), and published time.

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments

- **When to use**: List comments on an entry. Use when displaying the comment thread or when syncing comments (e.g. for a remote node).
- **Auth**: None required for public entries. Only comments for entries the caller can access are returned (visibility rules match the entry).
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
  "count": 1,
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
      "comment": "Nice post",
      "contentType": "text/plain",
      "published": "2026-03-01T12:00:00+00:00",
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

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_REF}

- **When to use**: Fetch a single comment by UUID or by FQID (comment ref may be the comment UUID or the percent-encoded comment FQID).
- **Response**: `200 OK` with a single `comment` object, or `404 Not Found` if the comment or entry does not exist or the entry is not visible.

---

## Likes API

Likes are per (author, entry); at most one like per author per entry.

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/likes

- **When to use**: List who liked an entry. Use when displaying like count or list on the entry detail/stream.
- **Auth**: None required for entries the caller can access.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK`.
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

- **Status**: `201 Created` when a new like is created, or `200 OK` when the like already existed. Body is the like object. `400 Bad Request` if author cannot be resolved.

### DELETE /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/likes

- **When to use**: Remove the like (unlike) for the given author.
- **Auth**: Author must be identified via session or request body (`authorId`).
- **Body** (`application/json`): Optional. May include `authorId` if not using session.

#### Response

- **Status**: `204 No Content` on success. `400 Bad Request` if author cannot be resolved. If there was no like, `204` is still returned.

---

## Comment Likes API

Comment likes are per (author, comment); at most one like per author per comment.

### GET /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/likes

- **When to use**: List who liked a comment.
- **Auth**: None required for comments the caller can access.
- **Query params**: `page` (optional, default `1`), `size` (optional, default `10`).

#### Response

- **Status**: `200 OK`.
- **Body**: A `likes` object with `type`, `id`, `page_number`, `size`, `count`, and `src` (array of like objects). Each like has `type`, `author`, `published`, `id`, and `object` (the comment FQID).

### POST /api/authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/likes

- **When to use**: Record a like from an author on a comment. Idempotent: if the author already liked the comment, returns `200` with the existing like.
- **Auth**: Author must be identified via session or request body (`authorId`).
- **Body** (`application/json`): Optional. May include `authorId` (author UUID) if not using session.

#### Response

- **Status**: `201 Created` when a new like is created.
- **Status**: `200 OK` when the like already existed (idempotent).
- **Status**: `400 Bad Request` if author cannot be resolved.
- **Status**: `404 Not Found` if the entry or comment does not exist or does not match the path.

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
- **Status**: `404 Not Found` if the entry or comment does not exist or does not match the path.

### UI: Comment Like/Unlike

Each comment on the entry detail page shows:
- The total like count (e.g. "3 likes").
- A **Like** or **Unlike** button (depending on whether the current user has already liked it).

HTML endpoints:
- `POST /authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/like/` – like a comment, redirects back to entry detail.
- `POST /authors/{AUTHOR_SERIAL}/entries/{ENTRY_SERIAL}/comments/{COMMENT_SERIAL}/unlike/` – unlike a comment, redirects back to entry detail.

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

