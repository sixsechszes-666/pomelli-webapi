# pomelli-webapi

An async Python client for the Google Pomelli web app, driven over HTTP. No
browser, no Selenium, no Playwright, no CDP.

```python
import asyncio
from pomelli_webapi import PomelliClient


async def main():
    async with PomelliClient() as client:
        report = await client.photoshoot("shoe.jpg", ["Studio", "Flatlay"], aspect="square")
        report.save("out")
        print(report)


asyncio.run(main())
```

Pomelli is Google Labs' product-photoshoot app: you give it one photo of a
product and it renders it as a set of styled shots. The web app does all of it
through six RPC calls, and every one of them is reproducible from cookies alone.
This package makes those calls directly.

## Why

The browser was only ever holding cookies and replaying a fixed sequence. Two
things in that sequence are signed with nothing but a cookie value, so the whole
session is reproducible from a cookie jar:

- the `authorization` header is `sha1("<ts> <cookie> <origin>")`, three times
  over, for three cookies;
- the upload is a plain two-step resumable POST, not a multipart form.

Everything else is JSON arrays in and JSON arrays out. A browser adds a few
hundred megabytes of process and a class of failure - a hung renderer, a stale
selector - that has nothing to do with the render itself.

## Install

```bash
pip install pomelli-webapi            # client only
pip install pomelli-webapi[browser]   # plus reading cookies out of local browsers
```

Python 3.10 or newer. Two dependencies: `curl_cffi` for the TLS fingerprint and
`loguru`.

## Cookies

Pomelli needs a signed-in Google session. Export the cookies of the account that
has Pomelli open.

```python
from pomelli_webapi import Cookies, PomelliClient

jar = Cookies.from_file("cookies.json")   # a list of records, or {name: value}
client = PomelliClient(jar)
```

Or let the package read them from a local browser, which needs the `browser`
extra:

```python
client = PomelliClient()          # searches the local browsers for .google.com
```

**The trap worth knowing about.** A signed-in Google profile holds three copies
of every authentication cookie - one each for `.google.com`, `.google.ru` and
`.youtube.com`. Most hand-rolled clients collapse the jar into a `{name: value}`
dict, which keeps whichever copy came last, and Google answers
`SESSION_COOKIE_INVALID` naming the cookie. That reads as an expired session and
sends you back to a browser login you never needed. A browser attaches cookies by
domain and path, and so does this package: `Cookies.scoped(host, path)` applies
RFC 6265 matching and picks the most specific copy, so the `.google.ru` copy
never goes to a `google.com` host. `Cookies` also treats a plain
`{name: value}` mapping as global on purpose, for the case where you copied
SAPISID and friends by hand.

`pomelli-cli cookies` prints what would be sent where, which is the fastest way
to see this working.

## Presets

Step 2 of the wizard shows one grid under one counter, and it holds two kinds of
card: product shots, named by an integer, and Style Inspirations, which have no
name at all - a style is identified by the resource ids of its own reference
images, so a style run uploads those first. Both kinds are addressed by the label
the picker shows.

Product shots:

| Label | id | | Label | id |
|---|---|---|---|---|
| Model Try On | 1 | | Ingredient | 8 |
| In Use | 2 | | Flatlay | 9 |
| Contextual | 3 | | Fashion Seasonal | 10 |
| Seasonal | 4 | | Amazon Product Image | 12 |
| Studio | 5 | | | |
| Floating | 6 | | | |

Styles: `curated-blooms`, `curated-on-ice`, `curated-fruit-burst`,
`curated-plastique-pop`, `curated-alpine-adventure`, `curated-watercolor`,
`curated-in-my-bag`, `curated-manga`, `curated-chrome`, `curated-warm-glass`,
`curated-porcelain`. Names are matched loosely - by label, by id, and with
spaces, hyphens and underscores treated alike - so `floral whimsy`, `Floral
Whimsy` and `curated-blooms` all resolve to the same style.

At most six picks per photoshoot. Aspect is `story` (9:16), `feed` (4:5) or
`square` (1:1); the default is square.

## The client

```python
client = PomelliClient(
    cookies=None,          # jar, path, mapping, or None to search the browser
    proxy=None,            # falls back to $POMELLI_PROXY, then direct
    business="",           # read from the account at first use
    account="",
    timeout=90.0,          # per read call
    retries=3,
    poll=4.0,              # how often to ask whether a render finished
    style_cache=None,      # where style reference PNGs are cached, per build
    strict=True,           # validate the jar on construction
)
```

| Call | What it does |
|---|---|
| `await client.photoshoot(image, picks, aspect=..., timeout=..., on_progress=...)` | Upload one photo, render every pick, download the results. Returns a `RenderReport`. |
| `await client.photoshoot_many(image, presets)` | Several presets over one photo, uploading it once. Stops when the quota ends. |
| `await client.render_one(image, pick)` | One photo, one pick. |
| `await client.init()` | Resolve the business id, account id and app build up front. |
| `await client.ping()` | Is the session alive. Answers honestly, and does not claim to know the remaining budget. |
| `await client.account()` | Account id, business id, business list. |
| `await client.limits()` | Raw `GetLimitInfo`. |
| `await client.business_items(page, size)` | A page of the account's items. |
| `await client.delete_item(item_id)` | Remove one item. |
| `await client.sage.survey()` | Call the read-only methods with an empty body and report who answers. |
| `await client.sage.call(method, params)` | Anything else the service exposes. |

The report holds image bytes on the objects rather than in any dictionary, so
`report.as_dict()` is always JSON-safe. A finished run used to die at the last
step on `TypeError: Object of type bytes is not JSON serializable`, throwing away
every render it had already paid for.

```python
report = await client.photoshoot("shoe.jpg", ["Studio", "Flatlay"])
report.ok           # True when at least one image came back
report.images       # RenderedImage: label, resid, data, mime, digest, save()
report.queued       # picks that were accepted
report.refused      # picks that could not even be queued, with the error
report.failed       # picks that rendered and did not finish
report.quota        # the batch was stopped by the account running out
report.stopped      # "settled" or "timeout"
report.save("out")  # writes NN_<label>_<hash>.png
```

## Command line

```bash
pomelli-cli cookies                       # what jar is used, and where each cookie goes
pomelli-cli ping                          # is the session alive
pomelli-cli account                       # business and account ids
pomelli-cli limits                        # raw GetLimitInfo
pomelli-cli templates                     # shot types and their ids
pomelli-cli styles                        # curated styles
pomelli-cli items --page 1 --size 20      # the account's items
pomelli-cli render shoe.jpg -p Studio -p Flatlay -o out
pomelli-cli survey                        # walk the read-only methods
```

Flags that are not tied to a subcommand work on either side of it:

```bash
pomelli-cli --cookies jar.json ping
pomelli-cli ping --cookies jar.json
```

## What the API actually is

Not batchexecute. Pomelli talks to
`google.internal.labs.sage.v1.LabsSageService` over the plain clients6 RPC
style - one POST per method, a JSON array in, a JSON array out. No `)]}'` prefix,
no byte-count chunking, no `wrb.fr` wrapper, and no `at` / `bl` / `f.sid`
tokens, because none of those exist outside the Boq/WIZ framework.

Every call carries:

```
content-type    application/json+protobuf    (plain application/json gets a 404)
x-goog-api-key  a public clients6 key, the one the app itself ships
authorization   SAPISIDHASH <ts>_<sha1> SAPISID1PHASH ... SAPISID3PHASH ...
x-goog-authuser 0
x-user-agent    grpc-web-javascript/0.1
origin          https://labs.google.com
```

The six calls the wizard makes, as recorded off live runs:

```
AllocateIds [50]                      -> 50 reserved ids, one per output

POST labs.google.com/pomelli_uploads/resource?authuser=0
                                      resumable, two steps: `start` answers an
                                      x-goog-upload-url, then `upload, finalize`
                                      with the bytes answers {"id": <resid>, ...}

MutateBusiness [business, [A, B]]     A = [null x5, [[[resid, mime]]]]
                                      B = [null x7, [[resid]]]

CreateBusinessItem [business, [null x12, []], item_id]

MutateBusinessItem [business, item_id, [[21 fields]]]
                                      field 2 is the aspect ratio, and either
                                      field 5 (a product shot) or field 8 (a
                                      style) carries the job

GetBusinessItems [business, [ids]]    status walks 1 -> 2 -> 3, and 3 hands the
                                      finished resource back in field 5[12][0]

GET labs.google.com/pomelli_downloads/accounts/<acct>/resources/<resid>
```

The item config is nested one level deeper than it looks. The outer array is 21
slots and carries the whole job in slot 20; the job is itself a shorter array
whose slot 2 is the aspect ratio, slot 5 a shot type id, slot 8 a style. The
browser trims trailing nulls, so a product-shot job is recorded six slots long
and a style job nine. Both recorded shapes are reproduced exactly - a longer
array than the browser ever sent would be a guess.

Responses are wrapped one level deeper than they look, too: `AllocateIds`
answers `[[id, ...]]` and `GetBusinessItems` answers `[[row, ...]]`. Iterating
the response directly yields the wrapper instead of the contents, which for ids
reads as "the server refused to reserve anything" and for item rows reads as
"nothing has finished rendering yet" - the one wrong answer that looks like
patience, because a poll loop that believes it waits out its whole timeout and
reports a run that rendered nothing, with no error anywhere. Nothing in
`utils/parsing.py` indexes by position; rows are found by shape.

## Failures, told apart

Four situations all look like "the request failed", and they need different
answers:

| Error | Meaning | What to do |
|---|---|---|
| `AuthError` | No usable cookies, or none that can build an authorization header. | Fix your side. Raised at construction, not on the first call. |
| `SessionExpiredError` | Google dropped the session. Carries the clock skew against the response `Date` header, because the auth header hashes a local timestamp and a machine whose clock has drifted cannot authenticate no matter how fresh the cookies are. | Re-capture the cookies. |
| `QuotaExceededError` | The account is out of renders. Never retried inside the transport: it is a state of the account, not a hiccup. | Wait. The batch keeps what was already rendering and stops. |
| `SageError` | The server answered something unexpected. `.status`, `.method`, `.payload`. | Read the payload. |
| `RenderError` | The wizard flow failed as a whole - the upload answered a sign-in page instead of an upload URL, or nothing could be queued. | Check the jar, or the picks. |

Rendering costs the account real quota, so a batch that dies on its third pick
does not abandon the two it already paid for: the refusal is recorded per pick,
queueing stops, and everything already rendering is still downloaded.

Two facts about the quota, both learned the hard way: the account gets roughly
56 renders and then answers `429 Resource has been exhausted`, reads stay alive
after that, and `GetLimitInfo` carries no counter at all - so it is a liveness
check and never a budget one.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

58 tests, no network. The transport is faked at the one seam where everything
happens - `SageClient.call` for RPC, `SageClient.request` for uploads and
downloads - so the whole flow runs offline: header construction (including the
exact hash scheme), the recorded item-config shapes, response unwrapping, and the
quota stop-and-harvest path.

If your environment has a broken third-party pytest plugin, `pytest` may fail
before collecting anything; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest` works
around it.

## Layout

```
src/pomelli_webapi/
├── client.py                 the client: session, caches, lifecycle
├── sage.py                   the RPC transport, auth headers, retries, survey
├── constants.py              hosts, methods, shot types, styles, aspects
├── exceptions.py             the error split above
├── cli.py                    pomelli-cli
├── components/
│   ├── business_mixin.py     account, business, items, polling
│   └── photoshoot_mixin.py   upload, queue, wait, download
├── types/                    Source, Plan, ItemState, RenderedImage, RenderReport
└── utils/
    ├── cookies.py            RFC 6265 scoping, the reason this works
    ├── parsing.py            response shapes, read by shape not index
    ├── planning.py           label -> id, for both kinds of card
    ├── load_browser_cookies.py
    └── logger.py
```

## License

MIT.
