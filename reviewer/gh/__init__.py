"""GitHub API clients.

Split in two because GitHub is split in two:

* ``rest``    — listing PRs, fetching files/patches, submitting reviews, replies.
* ``graphql`` — review threads with ``isResolved``/``isOutdated``, the check
  rollup, and ``resolveReviewThread``. Resolving a conversation has no REST
  equivalent, so GraphQL is not optional here.
"""

from .graphql import GraphQLClient
from .rest import GitHubError, RestClient

__all__ = ["GitHubError", "GraphQLClient", "RestClient"]
