# Salvage

Fragments kept from code that has been deleted. Nothing here is imported, and
nothing here should be: this file is `.md` precisely so that it cannot be. It
exists because a deletion loses the *reasoning* in a piece of code along with
the code, and a couple of those were worth keeping around.

---

## `_smart_chunks` — sentence-boundary text splitter

From `src/services/coqui_client.py` (`CoquiClient._smart_chunks`), deleted with
the rest of the retired Coqui pipeline. Kept because the *problem* it solves did
not go away with the provider that had it: long answers still have to be cut
into synthesis-sized pieces, and cutting at a sentence boundary rather than at a
hard character count is what stops a clause from being sliced mid-word. VieNeu
does not need this today — it takes the whole string — but the natural home for
it now is the LangGraph layer, which is where response text is assembled and
where a streaming TTS path would want to emit a sentence at a time rather than
wait for the full answer.

Note the two properties worth preserving if it is ever reimplemented: it never
drops characters (the `test_smart_chunks_no_period_splits_at_max` test asserted
`"".join(chunks) == text`), and it degrades to a hard cut at `max_chars` when
there is no period to split on, rather than emitting an over-long chunk.

```python
    def _smart_chunks(self, text: str, max_chars=200):
        chunks = []
        while len(text) > max_chars:
            split = text.rfind(".", 0, max_chars)
            if split == -1:
                split = max_chars
            chunks.append(text[:split + 1])
            text = text[split + 1:]
        chunks.append(text)
        return chunks
```
