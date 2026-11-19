"""Local item identity mapping."""


def map_items(items, mappings):
    """Always derive from immutable source IDs, so corrected mappings remain reversible."""
    mapping = {row['unknown_id']: row for row in mappings}
    merged = {}
    for item in items:
        source = item.get('source_template_id', item['item'])
        row = mapping.get(source, {})
        key = row.get('item_id', item['item']), row.get('tag') or item.get('tag') or ''
        merged[key] = merged.get(key, 0) + item['amount']
    return [{'item': item, 'tag': tag, 'amount': amount}
            for (item, tag), amount in sorted(merged.items())]
