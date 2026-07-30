# Voice Integration Test Accounts & Channels

Conventions for `VESKTOP_TEST_USER_ID` and `VESKTOP_TEST_CHANNEL_ID`. Pick an
unused pair before starting a test run — there's no automated allocation;
this table is the source of truth for what's already claimed.

Two independent axes:
- **User** selects which persistent, pre-authenticated Vesktop profile to
  use (`scripts/vesktop-test-login.sh <user-id>` sets one up once).
- **Channel** is the concurrency-locking axis — two unrelated runs must
  never share a channel ID at the same time, since `vesktop-test-<channel-id>`
  becomes the container name and Docker rejects the second `docker run`.

The user axis is *also* effectively exclusive per concurrent run, for a
different reason: two containers mounting the same user's profile volume
read-write at once collide on Electron's own singleton lock inside the
profile directory, so the same user-id can't be used by two concurrent runs
either, even across different channels. Both constraints are real and
independent — pick an unused channel *and* make sure no other run is
currently using the same user-id.

## Test users (`VESKTOP_TEST_USER_ID` → Discord account)

| user-id | Discord account | Notes |
|---|---|---|
| `charlietube_33976` | `charlietube_33976` | Logged in via `scripts/vesktop-test-login.sh charlietube_33976` |
| `tubertube` | `tubertube` | Logged in via `scripts/vesktop-test-login.sh tubertube` |

## Test channels (`VESKTOP_TEST_CHANNEL_ID` → channel)

| channel-id | Server / channel name | Notes |
|---|---|---|
| `1517630097538941189` | shared test channel | Accessible to both `charlietube_33976` and `tubertube` |
| `803098229855748150` | test channel | Accessible to `charlietube_33976`; `tubertube` confirmed NOT to have access |
