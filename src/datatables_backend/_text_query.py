"""
Google-style query syntax parser for text field column filters.

Supports:
- Implicit AND: "red apple" -> must contain both
- Explicit OR: "red OR green" -> must contain either
- NOT/exclusion: "apple -green" or "apple NOT green" -> must NOT contain
- Exact phrase: '"granny smith"' -> exact phrase match
- Combinations: '"granny smith" OR fuji -green'

Converts user input into OpenSearch bool queries with must, should, must_not clauses.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

# Marker tokens for internal parsing (not passwords)
_NOT_MARKER = '__NOT__'
_OR_MARKER = '__OR__'
_MINUS_CHAR = '-'


class QueryType(Enum):
    """Query term type for boolean logic."""

    MUST = 'must'  # AND - required term
    SHOULD = 'should'  # OR - optional term (at least one required)
    MUST_NOT = 'must_not'  # NOT - excluded term


@dataclass
class QueryTerm:
    """Represents a parsed query term."""

    value: str
    query_type: QueryType
    is_phrase: bool = False


@dataclass
class _ParseState:
    """Internal state during parsing."""

    terms: list[QueryTerm]
    or_group_active: bool = False
    not_next: bool = False


class QueryParser:
    """
    Parser for Google-style query syntax.

    Converts natural language search queries into structured terms
    that can be converted to OpenSearch bool queries.
    """

    # Regex patterns for parsing
    PHRASE_PATTERN = re.compile(r'"([^"]*)"')
    OR_PATTERN = re.compile(r'\bOR\b', re.IGNORECASE)
    NOT_PATTERN = re.compile(r'\bNOT\b', re.IGNORECASE)

    def parse(self, query: str) -> list[QueryTerm]:
        """
        Parse a query string into structured terms.

        Args:
            query: The user's search query string

        Returns:
            List of QueryTerm objects representing the parsed query
        """
        if not query or not query.strip():
            return []

        remaining = query.strip()

        # Extract phrases and prepare token string
        phrase_placeholders, remaining = self._extract_phrases(remaining)
        tokens = self._tokenize(remaining)

        # Process tokens into terms
        state = _ParseState(terms=[])
        i = 0
        while i < len(tokens):
            i = self._process_token(tokens, i, phrase_placeholders, state)

        return state.terms

    def _extract_phrases(self, text: str) -> tuple[dict[str, tuple[str, bool]], str]:
        """Extract quoted phrases and replace with placeholders."""
        phrase_placeholders: dict[str, tuple[str, bool]] = {}
        placeholder_idx = 0

        def replace_phrase(match: re.Match[str]) -> str:
            nonlocal placeholder_idx
            phrase = match.group(1)
            if not phrase:  # Empty quote
                return ''

            # Check if preceded by - or NOT
            start = match.start()
            prefix = text[:start].rstrip()
            is_negated = prefix.endswith(_MINUS_CHAR) or bool(
                self.NOT_PATTERN.search(prefix[-4:] if len(prefix) >= 4 else prefix)
            )

            placeholder = f'__PHRASE_{placeholder_idx}__'
            phrase_placeholders[placeholder] = (phrase, is_negated)
            placeholder_idx += 1
            return placeholder

        remaining = self.PHRASE_PATTERN.sub(replace_phrase, text)
        return phrase_placeholders, remaining

    def _tokenize(self, text: str) -> list[str]:
        """Convert text to tokens, replacing keywords with markers."""
        # Replace NOT and OR with markers
        text = self.NOT_PATTERN.sub(f' {_NOT_MARKER} ', text)
        text = self.OR_PATTERN.sub(f' {_OR_MARKER} ', text)
        return text.split()

    def _process_token(
        self, tokens: list[str], i: int, phrase_placeholders: dict[str, tuple[str, bool]], state: _ParseState
    ) -> int:
        """Process a single token and update state. Returns next index."""
        token = tokens[i]

        # Handle markers
        if token == _NOT_MARKER:
            state.not_next = True
            return i + 1

        if token == _OR_MARKER:
            state.or_group_active = True
            self._convert_last_term_to_should(state)
            return i + 1

        # Handle phrase placeholders
        if self._process_phrase_token(token, phrase_placeholders, state):
            return i + 1

        # Handle negation prefix (-word)
        if token.startswith(_MINUS_CHAR) and len(token) > 1:
            self._add_negated_term(token, state)
            return i + 1

        # Handle regular term
        self._add_regular_term(token, tokens, i, state)
        return i + 1

    def _convert_last_term_to_should(self, state: _ParseState) -> None:
        """Convert the last MUST term to SHOULD for OR grouping."""
        if state.terms and state.terms[-1].query_type == QueryType.MUST:
            last = state.terms[-1]
            state.terms[-1] = QueryTerm(value=last.value, query_type=QueryType.SHOULD, is_phrase=last.is_phrase)

    def _process_phrase_token(
        self, token: str, phrase_placeholders: dict[str, tuple[str, bool]], state: _ParseState
    ) -> bool:
        """Process a phrase placeholder token. Returns True if handled."""
        is_negated_prefix = token.startswith(_MINUS_CHAR)
        actual_token = token.lstrip(_MINUS_CHAR)

        if actual_token not in phrase_placeholders:
            return False

        phrase_value, was_negated = phrase_placeholders[actual_token]
        is_negated = is_negated_prefix or was_negated or state.not_next

        query_type = self._determine_query_type(is_negated, state.or_group_active)
        state.terms.append(QueryTerm(value=phrase_value, query_type=query_type, is_phrase=True))

        state.not_next = False
        state.or_group_active = False
        return True

    def _add_negated_term(self, token: str, state: _ParseState) -> None:
        """Add a term with negation prefix (-word)."""
        value = token[1:]
        # Handle double minus
        while value.startswith(_MINUS_CHAR) and len(value) > 1:
            value = value[1:]
        if value:
            state.terms.append(QueryTerm(value=value, query_type=QueryType.MUST_NOT, is_phrase=False))
        state.or_group_active = False
        state.not_next = False

    def _add_regular_term(self, token: str, tokens: list[str], i: int, state: _ParseState) -> None:
        """Add a regular (non-phrase, non-negated) term."""
        if not token or token == _MINUS_CHAR:
            return

        query_type = self._determine_query_type(state.not_next, state.or_group_active)
        state.terms.append(QueryTerm(value=token, query_type=query_type, is_phrase=False))

        state.not_next = False

        # Check if OR group should continue
        if state.or_group_active:
            if i + 1 < len(tokens) and tokens[i + 1] == _OR_MARKER:
                pass  # Keep OR group active
            else:
                state.or_group_active = False

    def _determine_query_type(self, is_negated: bool, is_or_group: bool) -> QueryType:
        """Determine the query type based on context."""
        if is_negated:
            return QueryType.MUST_NOT
        if is_or_group:
            return QueryType.SHOULD
        return QueryType.MUST

    def to_opensearch_query(self, query: str, field_name: str) -> dict[str, Any]:
        """
        Convert a query string to an OpenSearch query.

        Args:
            query: The user's search query string
            field_name: The field to search in

        Returns:
            OpenSearch query dict
        """
        terms = self.parse(query)

        if not terms:
            return {'match_all': {}}

        # Single term optimization
        if len(terms) == 1:
            return self._build_single_term_query(terms[0], field_name)

        return self._build_bool_query(terms, field_name)

    def _build_single_term_query(self, term: QueryTerm, field_name: str) -> dict[str, Any]:
        """Build query for a single term."""
        if term.is_phrase:
            clause = {'match_phrase': {field_name: term.value}}
        else:
            clause = {'match': {field_name: {'query': term.value}}}

        if term.query_type == QueryType.MUST_NOT:
            return {'bool': {'must_not': [clause]}}
        return clause

    def _build_bool_query(self, terms: list[QueryTerm], field_name: str) -> dict[str, Any]:
        """Build a bool query from multiple terms."""
        must_clauses: list[dict[str, Any]] = []
        should_clauses: list[dict[str, Any]] = []
        must_not_clauses: list[dict[str, Any]] = []

        for term in terms:
            clause = self._build_term_clause(term, field_name)

            if term.query_type == QueryType.MUST:
                must_clauses.append(clause)
            elif term.query_type == QueryType.SHOULD:
                should_clauses.append(clause)
            elif term.query_type == QueryType.MUST_NOT:
                must_not_clauses.append(clause)

        bool_query: dict[str, Any] = {}

        if must_clauses:
            bool_query['must'] = must_clauses
        if should_clauses:
            bool_query['should'] = should_clauses
            bool_query['minimum_should_match'] = 1
        if must_not_clauses:
            bool_query['must_not'] = must_not_clauses

        return {'bool': bool_query}

    def _build_term_clause(self, term: QueryTerm, field_name: str) -> dict[str, Any]:
        """Build a single clause for a term."""
        if term.is_phrase:
            return {'match_phrase': {field_name: term.value}}
        return {'match': {field_name: {'query': term.value}}}
