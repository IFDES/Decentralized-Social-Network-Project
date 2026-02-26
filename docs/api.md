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

