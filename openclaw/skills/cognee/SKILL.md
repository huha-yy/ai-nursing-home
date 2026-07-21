# cognee

Knowledge memory for agents. Ingest and search across knowledge libraries.

## Tools

### cognee.add
Ingest content into a knowledge library.

Parameters:
- `content` (string, required): The content to ingest.
- `path` (string, optional): A logical path for the content (e.g. "notes/meeting.md").
- `library` (string, optional): Target library slug. Defaults to the agent's private library.

Returns: `{library_slug, path, chunk_count, content_hash, ingested_at}`

### cognee.search
Search across knowledge libraries.

Parameters:
- `query` (string, required): The search query.
- `limit` (int, optional, default=5): Max results to return (1-20).
- `library_slugs` (list[string], optional): Specific libraries to search. Defaults to all readable libraries.

Returns: `{results: [{library_slug, path, text, cosine_distance}], partial: bool, errors: []}`
