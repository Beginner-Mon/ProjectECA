"""OAuth connectors (stage 5): the two-endpoint flow of plan §8.

* `oauth.py`: `start`, `complete`, `refresh`, `revoke`, `access_token`.
* `store.py`: `ConnectionStore` (`user_connections`) and `PendingStore`
  (`oauth_pending`), Memory and Pg versions.
"""
