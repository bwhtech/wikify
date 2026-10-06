# Playwright suite conventions

The orchestrator picks the location while writing the plan (shown at the plan checkpoint),
then builds the config and helpers in pre-flight. Test agents only add specs.

| The app has | `<e2e-dir>` (suite) | `<pkg-dir>` (package.json, run commands here) | `<cfg>` |
|---|---|---|---|
| UI tests already (`cypress/`, `e2e/`, `tests/ui/`, ...) | Ask the user: extend that framework, or add Playwright beside it. Never add a second framework silently | | |
| `frontend/package.json` (Vue SPA) | `<app>/frontend/e2e/` | `<app>/frontend` (reuse its `node_modules`) | `-c e2e` |
| Neither (Desk-only or portal app) | `<app>/e2e/` | `<app>/e2e` (own small `package.json`) | `-c .` |

Playwright tests Desk (`/app/...`) and portal pages the same way.

## Layout

```
<e2e-dir>/
  playwright.config.ts
  .env.e2e              E2E_BASE_URL, E2E_USER, E2E_PASSWORD, E2E_API_KEY, E2E_API_SECRET
  .gitignore            .env.e2e  .state/  test-results/  playwright-report/
  helpers/
    env.ts              reads the E2E_* vars; PREFIX = "[test]"
    api.ts              call(method, args), getValue, getList (token auth)
    wait.ts             waitForValue(doctype, name, field, expected, { timeout, interval })
    upload.ts           uploadViaUi(page, input, path); uploadFile(path, { doctype, docname })
    cleanup.ts          deleteByPrefix(doctype, titleField, prefix)
    test.ts             `test` extended with `api` and `fixture` (reads .state/fixtures.json)
  setup/
    auth.setup.ts       log in once, save .state/user.json
    seed.setup.ts       find-or-create the [fixture] records, wait for their final status,
                        write .state/fixtures.json
  specs/
    <area>.spec.ts      one file per area, test titles start with the plan's test ID
```

## Config essentials

- Load the env file first: `process.loadEnvFile("<env-path>")` (Node 20.12+). The path is
  relative to `<pkg-dir>`, where every command runs: `e2e/.env.e2e` in a SPA, `.env.e2e`
  standalone. On older Node use the `dotenv` package: `dotenv.config({ path: "<env-path>" })`.
- `testDir: "./specs"`, `use.baseURL` from `E2E_BASE_URL`, `use.storageState: ".state/user.json"`.
- Two projects: `setup` (matches `setup/*.setup.ts`, no storageState) and `chromium`
  (`dependencies: ["setup"]`).
- `workers: 1` while specs share the fixture. Raise it only for specs that create their own data.
- Long jobs: per-test `test.setTimeout(...)`; don't raise the global timeout for everyone.

## Helpers contract

- **Auth:** `auth.setup.ts` posts `/api/method/login` (`usr`, `pwd`) with the `request` fixture
  and saves `request.storageState()`. UI specs start logged in.
- **API calls use token auth** (`Authorization: token <key>:<secret>`). A browser session that
  has loaded a page has a CSRF token, and cookie POSTs without `X-Frappe-CSRF-Token` fail;
  token auth avoids that. Make keys in pre-flight:
  `bench --site <site> execute frappe.core.doctype.user.user.generate_keys --args "['<user>']"`.
  It rotates the secret, so use a user nothing else depends on. Some Frappe versions return
  only `api_secret`; then read the key with
  `bench --site <site> execute frappe.db.get_value --args "['User', '<user>', 'api_key']"`.
  Write both into `.env.e2e`.
- **`call` throws** on a non-2xx response with the server message; returns `message` otherwise.
- **`waitForValue` polls the API** (default every 3 s) and fails with the last value seen plus
  the record's error field. Never wait on UI progress text; it can be stale.
- **Seed:** `seed.setup.ts` looks up `[fixture] <name>` first and creates it only if it is
  missing or not in its final status. Expensive pipelines run once per site, not per run.
- **Data:** every record a spec creates is titled `` `${PREFIX} <area> ${Date.now()}` ``
  and deleted in `afterAll` with `deleteByPrefix`. Specs never delete `[fixture]` records; a
  spec that changes one restores it in `afterEach`, or creates its own `[test]` record instead.
- **Selectors:** `getByRole`, `getByLabel`, `getByText`, matching the case file's "button
  'Start'" wording. Dialogs: `page.getByRole("dialog")`. No CSS classes from component
  libraries, no `nth()` unless the case says "the second image".

## Tags

Every test has exactly one tier tag (`@smoke`, `@functional` or `@negative`) and one area tag.
Add `@sanity` to the 1-3 fastest tests that prove an area still works. Add `@llm` on top of the
tier tag when the test needs the AI (`@functional @llm @agent`), so AI tests still run in tier
runs and can also run on their own. Apps with no AI features have no `@llm` tag.

| Tag | Purpose | Size / time | When |
|---|---|---|---|
| `@smoke` | Main journey works at all | under 5 min | after every deploy or build |
| `@sanity` | One area after a fix (combine with `@<area>`) | 1-3 tests per area | after a fix |
| `@functional` | Every feature with good input | the full set | before a release |
| `@negative` | Bad and boundary input fails cleanly | | before a release |
| `@llm` | Extra tag, AI apps only: needs the AI, real configured model | | alone on demand; costs credits |

```ts
import { test, expect } from "../helpers/test";

test("F-TREE-01 rename persists after reload", { tag: ["@functional", "@tree", "@sanity"] }, async ({ page, api, fixture }) => {
	...
});
```

`@llm` rules: put `test.describe.configure({ retries: 1 })` in the describe block; assert DB
state through `api`, never the reply text; never override the site's model setting.

## Known failures

A test that FAILED in the exploratory run is kept, marked expected-to-fail:

```ts
test("F-AGENT-11 stop ends the turn", {
	tag: ["@functional", "@agent"],
	annotation: { type: "issue", description: "https://github.com/<owner>/<repo>/issues/<n>" },
}, async ({ page }) => {
	// known failure: #<n>. Remove test.fail() when the issue is closed.
	test.fail();
	...
});
```

When the bug is fixed the test passes, Playwright reports "expected to fail, but passed", and
someone removes `test.fail()`. A spec that can't be made reliable gets `test.fixme()` with the
reason; it doesn't count as coverage.

## Commands (from `<pkg-dir>`)

```bash
<pm> add -D @playwright/test && npx playwright install chromium    # once; <pm> = yarn or npm, per the lockfile
npx playwright test <cfg> --project setup                        # seed fixture + login
npx playwright test <cfg> --grep @smoke                          # after deploy/build
npx playwright test <cfg> --grep "(?=.*@sanity)(?=.*@tree)"      # after a tree fix
npx playwright test <cfg>                                        # before a release (metered service: check balance)
npx playwright test <cfg> --grep @llm                            # AI apps only: AI tests, on demand
npx playwright show-report <e2e-dir>/playwright-report
```
