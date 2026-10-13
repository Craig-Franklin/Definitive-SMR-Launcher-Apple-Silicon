These three XML files contain invented events, text and modifier values. They are
not extracted game or map assets. The complete original, base and candidate bytes
were specified before the checker implementation; their fixed SHA-256 values are
asserted in `test_event_inheritance_candidate.py`.

The original and candidate retain CRLF line endings, an outer comment containing
fake Effects delimiters and an unselected fourth event. The differently ordered
base uses LF and whitespace inside its effect containers. The candidate removes
only the three selected Effects subtrees. A byte match does not establish native
inheritance, resource selection, preserved features, a crash repair or gameplay.
