# gbrain-mcp

Search and read the company knowledge base via GBrain. Useful for finding
product specs, brand guidelines, FAQ answers, and other knowledge about
the company's brands (戴恩医疗科技, 永和大健康/生命优雅).

## Tools

### gbrain-mcp.search
Search the knowledge base.

Parameters:
- `query` (string, required): The search query in Chinese or English.
- `limit` (int, optional, default=5): Max results to return (1-20).

Returns: list of `{slug, title, type, score, chunk_text}`

Usage example:
```
Search for "戴恩护理机器人参数" → find product specs
Search for "阴虚体质" → find TCM constitution info
```

### gbrain-mcp.get_page
Read a full page by slug.

Parameters:
- `slug` (string, required): The page slug, e.g. "daien/product/care_robot".

Returns: `{slug, title, type, compiled_truth, updated_at, tags, ...}`

Usage example:
```
get_page("daien/product/care_robot") → full product specs
get_page("yonghe/company/brand_guidelines") → brand guidelines
get_page("yonghe/health/constitution_types") → TCM constitution handbook
```

## Notes

- The knowledge base covers two brands:
  - `daien/` — 戴恩医疗科技 (intelligent nursing robots, bathing machines)
  - `yonghe/` — 永和大健康/生命优雅 (AI pulse diagnosis ring, health management)
- Use `search` first to find relevant pages, then `get_page` to read full content.
- Search supports semantic matching (not just keyword), so natural language
  queries work well.
