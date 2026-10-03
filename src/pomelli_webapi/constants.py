"""Everything that was read out of the live app rather than guessed.

Pomelli is a Google Labs app built on the Bettany framework
(``gstatic.com/_/bettany/<build>/sage/app_bundle.js``). It is **not** a
batchexecute app and ships no ``WIZ_global_data`` at all: no ``SNlM0e``, no
``FdrFJe``, no ``cfb2h``. Its API is plain clients6 JSON-RPC, so every call here
is one POST with a JSON array in and a JSON array out.

The tables below - method names, shot type ids, aspect ratios, style reference
images - come from that bundle and from recorded traffic. None of them are
documented by Google, and all of them can move when the app ships a new build.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- transport

SAGE_HOST = "https://labssage-pa.clients6.google.com"
SAGE_SERVICE = "google.internal.labs.sage.v1.LabsSageService"
ORIGIN = "https://labs.google.com"
RPC_USER_AGENT = "grpc-web-javascript/0.1"

LABS = "https://labs.google.com"
GSTATIC = "https://www.gstatic.com"

UPLOAD_URL = f"{LABS}/pomelli_uploads/resource?authuser=0"
DOWNLOAD_URL = f"{LABS}/pomelli_downloads/accounts/{{account}}/resources/{{resid}}"
PHOTOSHOOT_URL = f"{LABS}/pomelli/photoshoot"

# Public clients6 key. It ships inside the Bettany client and is the same one
# the page puts on its own requests, so it is not a per-account secret.
API_KEY = "AIzaSyApIzRHveewtVE3PbyathntMwQdC3heX-8"

# The app build id is the build date in base64 and goes stale as the app ships.
# The live page is always asked first; this is only the fallback.
FALLBACK_BUILD = "MjAyNjA5MjAuMDFfcDA"

# Statuses worth a second attempt. Everything else is an answer, not a hiccup: a
# 400 means the params are wrong and repeating them changes nothing.
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Text Google uses when the session itself is gone. Deliberately narrow: a bare
# 401 is not enough, because a method can demand its own credential on top of a
# perfectly good session - GetInstagramProfile answers 401 with
# "Expected OAuth 2 access token", which says nothing about the cookies. A
# classifier keyed on the status alone would report a healthy session as dead.
AUTH_MARKERS = (
    "SESSION_COOKIE_INVALID",
    "UNAUTHENTICATED",
    "INVALID_CREDENTIALS",
    "LOGIN_REQUIRED",
)

# Everything LabsSageService exposes, lifted out of the bundle. The interesting
# ones for the photoshoot flow are AllocateIds, CreateBusinessItem,
# MutateBusinessItem, GetBusinessItems and the download URL.
METHODS = (
    "AddAdsAsset", "AllocateIds", "ConvertVideoToGif", "CreateAgentSession",
    "CreateBusinessItem", "DeleteBusiness", "DeleteBusinessItem",
    "ExtractImagesFromUrl", "ExtractProductFromUrl", "GenerateCampaign",
    "GenerateCampaignIdeas", "GenerateCampaignSuggestions", "GenerateCaption",
    "GenerateCreativeIdeaFromCreativeFrame", "GenerateCreativeIdeas",
    "GenerateImage", "GenerateTextOverlays", "GetAccount", "GetAllowedFonts",
    "GetBrandBook", "GetBrandBookPdf", "GetBusiness", "GetBusinessItems",
    "GetInstagramProfile", "GetLimitInfo", "GetPhotoshootSuggestions",
    "GetResourceInfo", "GetStillCreativeEditableLayout", "GetWebsite",
    "LinkGbpProfile", "ListAdsCustomers", "ListBusinessItems", "ListBusinesses",
    "ListGbpListings", "MutateBusiness", "MutateBusinessItem",
    "PublishToInstagram", "RenderUrl", "StreamMessageAgent", "SubmitFeedback",
    "UpdateAccountSettings", "UpscaleImage", "ValidateWebsiteSlug",
)

# Methods that build something. They take longer than a read, so they get a
# longer timeout instead of being retried blindly.
GEN_METHODS = frozenset({
    "GenerateImage", "GenerateCampaign", "GenerateCampaignIdeas",
    "GenerateCampaignSuggestions", "GenerateCaption", "GenerateCreativeIdeas",
    "GenerateCreativeIdeaFromCreativeFrame", "GenerateTextOverlays",
    "UpscaleImage", "ConvertVideoToGif", "ExtractImagesFromUrl",
    "ExtractProductFromUrl", "PublishToInstagram", "RenderUrl",
    "StreamMessageAgent",
})
GEN_TIMEOUT = 300.0

# The SAPISID family is what the authorization header is built from:
# ``SAPISIDHASH <ts>_<sha1("<ts> <value> https://labs.google.com")>``. On a
# normal account all three carry the same value, so the three digests come out
# equal, which is exactly what the browser sends.
AUTH_COOKIE_LABELS = (
    ("SAPISIDHASH", "SAPISID"),
    ("SAPISID1PHASH", "__Secure-1PAPISID"),
    ("SAPISID3PHASH", "__Secure-3PAPISID"),
)
SESSION_COOKIE_NAMES = ("SID", "__Secure-1PSID", "__Secure-3PSID")

# ------------------------------------------------------------------ wizard

# A business item's terminal state. GetBusinessItems returns 1 the moment an
# item is created, 2 while it renders, and 3 with the finished resource. The
# bundle carries no enum for these, so nothing more is claimed than was seen.
STATUS_CREATED = 1
STATUS_RENDERING = 2
STATUS_DONE = 3

# One photoshoot holds at most six picks, shared between the product-shot
# section and Style Inspirations: step 2 shows a single `(n/6)` counter.
MAX_PICKS = 6

# Shot type ids, read out of the bundle's own card table. They are global: a
# template is named by its id, so the category never has to be chosen or sent.
TEMPLATE_IDS = {
    "Model Try On": 1,
    "In Use": 2,
    "Contextual": 3,
    "Seasonal": 4,
    "Studio": 5,
    "Floating": 6,
    "Ingredient": 8,
    "Flatlay": 9,
    "Amazon Product Image": 12,
}

# Some Fashion seasonal cards use id 10 rather than 4. Kept separate instead of
# averaged, because one of the two is right per card and nothing recorded says
# which.
TEMPLATE_IDS_EXTRA = {"Fashion Seasonal": 10}

# Category ids, for reporting only. A template is named by its id, so the
# category never has to be chosen or sent.
CATEGORIES = {
    0: "Home", 1: "Beauty", 2: "General", 3: "Fashion", 4: "Consumables",
    5: "Amazon Product Images",
}

# Aspect ratio, from the bundle's own comparisons: story is 1, and a post is 2
# or 3. Square is pinned to 3 by the recorded runs, which were square and sent
# 3, which leaves 2 for 4:5.
ASPECT_IDS = {
    "story (9:16)": 1, "story": 1, "9:16": 1,
    "feed (4:5)": 2, "feed": 2, "4:5": 2, "post": 2,
    "square (1:1)": 3, "square": 3, "1:1": 3,
}
DEFAULT_ASPECT = 3
ASPECT_LABELS = {1: "Story (9:16)", 2: "Feed (4:5)", 3: "Square (1:1)"}

# The curated Style Inspirations, lifted from the bundle. A style is not an
# enum: the request carries the resource ids of these reference images, so they
# are downloaded from gstatic and uploaded like any other file before anything
# is rendered.
STYLES = {
    "curated-blooms": {
        "label": "Floral Whimsy",
        "images": ["photoshoot/blooms_01.png", "photoshoot/blooms_02.png",
                   "photoshoot/blooms_03.png"],
    },
    "curated-on-ice": {
        "label": "On Ice",
        "images": ["photoshoot/on_ice_01.png", "photoshoot/on_ice_02.png",
                   "photoshoot/on_ice_03.png"],
    },
    "curated-fruit-burst": {
        "label": "Fruit Burst",
        "images": ["photoshoot/fruit_burst_01.png", "photoshoot/fruit_burst_02.png",
                   "photoshoot/fruit_burst_03.png"],
    },
    "curated-plastique-pop": {
        "label": "Plastique Pop",
        "images": ["photoshoot/plastique_pop_01.png", "photoshoot/plastique_pop_02.png",
                   "photoshoot/plastique_pop_04.png"],
    },
    "curated-alpine-adventure": {
        "label": "Alpine Adventure",
        "images": ["photoshoot/alpine_adventure_01.png"],
    },
    "curated-watercolor": {
        "label": "Watercolor",
        "images": ["photoshoot/watercolor_01.png"],
    },
    "curated-in-my-bag": {
        "label": "In My Bag",
        "images": ["photoshoot/in_my_bag_01.png", "photoshoot/in_my_bag_02.png",
                   "photoshoot/in_my_bag_03.png"],
    },
    "curated-manga": {
        "label": "Mangacore",
        "images": ["photoshoot/manga_01.png", "photoshoot/manga_02.png"],
    },
    "curated-chrome": {
        "label": "Liquid Metal",
        "images": ["photoshoot/chrome_01.png"],
    },
    "curated-warm-glass": {
        "label": "Amber Hour",
        "images": ["photoshoot/warm_glass_01.png"],
    },
    "curated-porcelain": {
        "label": "Vintage Tea",
        "images": ["photoshoot/porcelain_01.png"],
    },
}

# Other names the same style goes by. The first is what step 2 renders; the
# rest are how it reads elsewhere in the app.
STYLE_ALIASES = {
    "manga": "curated-manga", "mangacore": "curated-manga",
    "chrome": "curated-chrome", "liquid metal": "curated-chrome",
    "porcelain": "curated-porcelain", "vintage tea": "curated-porcelain",
    "amber hour": "curated-warm-glass", "warm glass": "curated-warm-glass",
}

MIME_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
}

# A survey fires every method with an empty body. On a read that costs one
# request; on DeleteBusiness, PublishToInstagram or GenerateImage it is an
# attempt to delete, publish or render with no target.
READ_PREFIXES = ("Get", "List", "Validate")


def is_readonly(method: str) -> bool:
    """Would calling this with an empty body only read something?"""
    return method.startswith(READ_PREFIXES)


def normalize(text: str) -> str:
    """Fold a label so `Model Try On` and `model_try-on` resolve the same."""
    return re.sub(r"[\s_\-]+", " ", (text or "").strip().lower())


__all__ = [
    "API_KEY", "ASPECT_IDS", "ASPECT_LABELS", "AUTH_COOKIE_LABELS", "AUTH_MARKERS",
    "CATEGORIES", "DEFAULT_ASPECT", "DOWNLOAD_URL", "FALLBACK_BUILD", "GEN_METHODS",
    "GEN_TIMEOUT", "GSTATIC", "LABS", "MAX_PICKS", "METHODS", "MIME_TYPES", "ORIGIN",
    "PHOTOSHOOT_URL", "READ_PREFIXES", "RETRY_STATUS", "RPC_USER_AGENT", "SAGE_HOST",
    "SAGE_SERVICE", "SESSION_COOKIE_NAMES", "STATUS_CREATED", "STATUS_DONE",
    "STATUS_RENDERING", "STYLES", "STYLE_ALIASES", "TEMPLATE_IDS", "TEMPLATE_IDS_EXTRA",
    "UPLOAD_URL", "is_readonly", "normalize",
]
