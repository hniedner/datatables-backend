# 008: How the total cache drops a total

This is the record of a decision for issue #50 (2026-11-24): what `TotalCache` does when it is full, and why it has no
separate sweep of expired totals. The requirements are PRD-C14 and PRD-C16 in `docs/PRD.md`. The one measurement here
was made on 2026-11-24 with `tracemalloc`; the rest is reasoning, labelled INFERRED.

## Why a bound

A scope per user or tenant (PRD-C15) adds one cached total for each distinct value, and a bound value that changes with
every request (a `datetime.now()` in a base filter, an object whose `repr` holds its address) adds one for each
request. Without a bound the default setup grows until `clear()` is called.

## The default: 10,000 totals

MEASURED: 100,000 `put` calls of fresh keys held 263 bytes each (a 64-character digest key, an `int`, a `float` and the
dict's slot). The digest has a fixed length, so the figure does not depend on how long the key is. 10,000 totals are
about 2.6 MB and 100,000 about 26 MB. 10,000 covers every user or tenant active within one default TTL (300 s) for all
but the largest deployments; a larger one raises `max_entries`.

INFERRED: when more distinct scopes are active inside one TTL than `max_entries`, a cache that drops the oldest and is
read cyclically keeps no total long enough to hit, so every request counts. The answer is right and only slower, the
same as having no cache.

## The rule: oldest first

The total stored longest ago is dropped first, and a read changes nothing. All totals have one TTL, so the oldest is the
nearest its expiry and an expired total always goes before a valid one.

| Rule | For | Against |
|---|---|---|
| Oldest first (chosen) | One sentence states it. O(1). The total dropped has the least validity left. A read does not write. | A total stored early can go before a later one if more than `max_entries` other totals arrive inside one TTL. |
| Least recently used | A total read often survives a flood of keys nobody reads again. | A read must reorder. The rule is harder to state, and an expired total may outlive a valid one. A total read often is stored again once per TTL anyway. |

A total stored again (it had expired, or two requests counted it together) is removed and inserted, so it becomes the
newest. SPEC-C14.5 holds that.

## No separate sweep of expired totals

The owner's comment of 2026-11-24 on #50 named "dropping expired entries". INFERRED: with one TTL the entries are in the
order of their expiry, so "drop the expired ones, then the oldest while over the bound" and "drop the oldest while over
the bound" drop the same totals whenever the bound is reached. They differ only in the memory held while there is room,
at most `max_entries` x 263 bytes. No answer differs, so a request through `fetch` cannot tell a sweep from none.

Proving a sweep needs a size accessor. `__len__` is the obvious one and is a trap: the backends test the cache with
`if self.total_cache` (sqlalchemy.py, opensearch.py), so a `__len__` makes an empty cache falsy and it is never filled.
Fixing the six sites to `is not None` is a change in both backends for a diagnostic. The owner approved no sweep on
2026-11-24. The cost of adding one later is `__len__`, those six edits and a test through `len`.

## Not done

- No `None` for an unbounded cache: whoever wants one passes a large number.
- No single flight: two requests that both miss a total both count it and both store it. Unchanged from before.
- No test of concurrent `put`: a race cannot be provoked deterministically. Every change to the dict is inside the one
  lock, which the review checks.
- The get/put protocol for other stores stays out (#162, v1.0). `get` and `put` keep their signatures.
