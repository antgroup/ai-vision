# Cross-repository source-compatible Daily cutover

## Accepted request

Implement the reviewed cross-repository corrections before using a main-product
`0.1.0` Daily to validate the release process. The user selected standard
source-compatible delivery; compiled native artifacts remain optional. Repository
visibility is unchanged. Skills stay external; temporary private distribution is
not the public product channel. Product publication must use fixed, qualified
inputs and authenticated remote readback, with final production approval after a
reviewable candidate exists.

## Ownership

- `ai-vision-ob`: product source, normal CLI, its four owned images, compatibility
  manifest, candidate qualification and product Releases. It only consumes the
  selected SigNoz digest; it does not move SigNoz image aliases.
- `signoz`: downstream Go/query checks, source/image provenance and its own image
  namespace/aliases. Same-repository PRs are identified by repository identity;
  reviewed external code runs read-only without inherited organization secrets.
- `ai-vision-skills`: four independently installed Skills and their references,
  scripts and tests. Product installs select an explicitly published immutable
  product tag; historical private delivery is explicit compatibility only.
- `ai-vision-docs`: three-language user instructions and Mintlify publication,
  using canonical Skill paths and the same concrete product Release contract.

## Implementation boundary

Public source coordinates use GitHub HTTPS, actual `ob-labs/signoz` and product
`main`; explicit per-repository overrides support internal/fork use. Regenerate
the CLI's canonical embedded assets. Root VERSION changes from 0.0.1 to 0.1.0 as
explicit release preparation; independently versioned dependencies are unchanged.

Product manifests add `source_repository` and `release_tag`; these identify the
source/product owner and immutable Release, not a Skills catalog. The manifest
retains the exact five runtime image digests, while product tag writes target
only the four product-owned images. SigNoz build receipts remain separately
identified source/run/attempt/digest/platform evidence.

The standard candidate pipeline retains source checks and actual packaged-CLI
deployment against CE and seekdb. Manual runs default to candidate generation,
without product Releases or rolling alias promotion. A separate explicit Daily
promotion may reuse the exact successful candidate's assets and acceptance,
provided source/run/attempt and hashes still match. Stable/RC approval must not be
bypassed. Actual Release assets are downloaded and compared before aliases move;
installed CLI/readback qualification is additional evidence after publication.

No historical Release, tag, package, saved deployment, user worktree, or repository
visibility is rewritten/deleted. Raw runtime logs and private configuration are
not public acceptance assets. Compiled-native privacy gates remain intact for
explicit historical private use. No current source or static tests are relabeled
as a successful candidate, anonymous public download or deployment.

## Qualification boundary

The first end-to-end release qualification targets Linux amd64 with both database
backends. Other image/CLI builds and optional native compilation retain their
actual separate scope. Publishing a Daily demonstrates the selected release and
installation process, not every model, platform, migration history or feature.
While repositories remain private, authenticated access is verified explicitly;
anonymous access is not inferred from a private-repository run.
