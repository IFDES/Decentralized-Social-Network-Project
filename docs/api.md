## API Overview

- **Base URL (local dev)**: `http://127.0.0.1:8000`
- **Base URL (deployed)**: Heroku App
- **API prefix**: all API paths below are relative to `{BASE_URL}` (whether it is from local dev, or heroku app)
- **Authentication**:
  - **Local** (browser / same node): Django session (login form) or whatever the team chooses.
  - **Remote** (node-to-node): HTTP Basic Auth as required by the project spec (to be wired in later project parts).

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
- **Optional query param**:
  - `author` (UUID): when provided, includes this local author's own non-public entries in addition to public entries.

### GET /api/stream

- **Purpose**: Return a consolidated stream of entries an author should know about.
- **Auth**: None currently required for local development.
- **Query params**:
  - `page` (optional, default `1`)
  - `size` (optional, default `10`)
  - `author` (optional UUID): requester author context; includes that author's own non-public entries.

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

- Always includes `PUBLIC` entries from local authors.
- Includes requester author's own non-public entries only when requester context is provided via `author` query parameter.
- Relationship-based visibility (follows/friends) is not implemented in this repo yet, so no additional relationship visibility is applied.
- Non-public entries from other authors are excluded.

#### Example request

```http
GET /api/stream?page=1&size=2&author=8d35d13e-f0ee-468d-bd6f-f942ec660f43
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
      "title": "Draft note",
      "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/6b1dc656-0704-48da-bf2f-c6507aa2fbbd",
      "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/6b1dc656-0704-48da-bf2f-c6507aa2fbbd",
      "description": "",
      "contentType": "text/plain",
      "content": "Personal draft",
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
        "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/6b1dc656-0704-48da-bf2f-c6507aa2fbbd/comments",
        "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/6b1dc656-0704-48da-bf2f-c6507aa2fbbd",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      },
      "likes": {
        "type": "likes",
        "id": "http://127.0.0.1:8000/api/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/6b1dc656-0704-48da-bf2f-c6507aa2fbbd/likes",
        "web": "http://127.0.0.1:8000/authors/8d35d13e-f0ee-468d-bd6f-f942ec660f43/entries/6b1dc656-0704-48da-bf2f-c6507aa2fbbd",
        "page_number": 1,
        "size": 5,
        "count": 0,
        "src": []
      },
      "published": "2026-02-28T11:20:00+00:00",
      "updated_at": "2026-02-28T11:20:00+00:00",
      "visibility": "FRIENDS"
    }
  ]
}
```

Deleted entries are not included in stream responses.

