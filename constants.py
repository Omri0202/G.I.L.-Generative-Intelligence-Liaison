"""
constants.py — G.I.L.
Shared constants used by multiple modules.
Import from here rather than redefining inline.
"""

# Words that indicate a request is about generating a website.
# Used in conversation_engine.py and action_router.py to reroute
# "build" actions to the webgen fast-path.
WEBGEN_WORDS: frozenset[str] = frozenset({
    "website", "web site", "webpage", "web page", "landing page", "landing",
    "web app", "web application", "html", "frontend", "front-end", "site",
    "homepage", "home page", "front end",
})
