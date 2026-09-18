"""Deterministic, reversible narrowing within a search snapshot."""
from pathlib import Path
import time


class ResultRefinement:
    def __init__(self, rows, query=''):
        self.original = list(rows)
        self.query = query
        self.steps = []

    @property
    def rows(self):
        return self.steps[-1][1] if self.steps else self.original

    def select(self, title, predicate):
        self.steps.append((title, [r for r in self.rows if predicate(r)]))

    def narrow(self, title, rows):
        paths = {r['path'] for r in rows}
        self.select(title, lambda r: r['path'] in paths)

    def facet(self, kind, value=None):
        if kind == 'pdf':
            self.select('PDF', lambda r: Path(r['path']).suffix.lower() == '.pdf')
        elif kind == 'currency':
            self.select(value, lambda r: value in (r.get('fields') or {}).get('currencies', []))
        elif kind == 'recent':
            cutoff = time.time() - 7 * 86400
            self.select('최근 7일 수정', lambda r: cutoff <= r.get('mtime', 0) <= time.time())
        elif kind == 'direct':
            self.select('일치하는 파일', lambda r: r.get('group') == '일치하는 파일')
        elif kind == 'text' and value.strip():
            terms = value.casefold().split()
            def matches(row):
                text = ' '.join(str(row.get(k, '')) for k in ('name', 'body', 'fields', 'tags', 'note')).casefold()
                return all(term in text for term in terms)
            self.select(value.strip(), matches)

    def back(self):
        if self.steps: self.steps.pop()

    def reset(self):
        self.steps.clear()
