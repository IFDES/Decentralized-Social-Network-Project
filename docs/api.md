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
